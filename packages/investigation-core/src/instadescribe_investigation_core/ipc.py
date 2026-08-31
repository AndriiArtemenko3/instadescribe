"""Strict primitive decoding for isolated child-to-parent IPC."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from .belief import BeliefConfig, update_beliefs
from .media import MediaMetadata
from .models import (
    ActionType,
    BeliefCandidate,
    BeliefSnapshot,
    CandidatePrior,
    ConnectivityPolicy,
    EgressDecision,
    EvidenceContribution,
    EvidenceItem,
    EvidenceKind,
    Investigation,
    InvestigationKind,
    InvestigationStatus,
    InvestigationStep,
    JsonValue,
    ModelProvenance,
    SourceRecord,
    StepStatus,
    TraceEvent,
    TraceEventType,
    VerificationState,
)
from .runner import LocalRunResult
from .serialization import to_primitive
from .trace import validate_trace

_MAX_STRING_CHARACTERS = 131_072
_MAX_COLLECTION_ITEMS = 4_096
_MAX_OBJECT_FIELDS = 256
_MAX_JSON_DEPTH = 32
_MAX_JSON_NODES = 100_000
_FLOAT_TOLERANCE = 1e-9


@dataclass(frozen=True, slots=True)
class LocalRunExpectation:
    """Parent-owned identities and inference inputs required to trust child IPC.

    Construct this value from the durable job/source record before launching the
    isolated child. Never derive it from the child response being validated.
    """

    source: SourceRecord
    investigation_id: str
    trace_id: str
    candidates: tuple[CandidatePrior, ...]
    model_provenance: tuple[ModelProvenance, ...] = ()
    belief_config: BeliefConfig = field(default_factory=BeliefConfig)
    kind: InvestigationKind = InvestigationKind.GEOLOCATE_PROVENANCE
    connectivity_policy: ConnectivityPolicy = ConnectivityPolicy.LOCAL

    def __post_init__(self) -> None:
        if not isinstance(self.source, SourceRecord):
            raise TypeError("source must be a parent-owned SourceRecord")
        if len(self.source.content_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.source.content_sha256
        ):
            raise ValueError("source.content_sha256 must be a lowercase SHA-256 digest")
        for name in ("investigation_id", "trace_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be empty")
            if len(value) > _MAX_STRING_CHARACTERS:
                raise ValueError(f"{name} exceeds the maximum string length")
        if not isinstance(self.candidates, tuple) or not self.candidates:
            raise ValueError("candidates must be a non-empty tuple")
        if any(not isinstance(candidate, CandidatePrior) for candidate in self.candidates):
            raise TypeError("candidates must contain CandidatePrior values")
        candidate_ids = [candidate.candidate_id for candidate in self.candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("candidate IDs must be unique")
        if not isinstance(self.belief_config, BeliefConfig):
            raise TypeError("belief_config must be a BeliefConfig")
        if not isinstance(self.model_provenance, tuple) or any(
            not isinstance(model, ModelProvenance) for model in self.model_provenance
        ):
            raise TypeError("model_provenance must be a tuple of ModelProvenance values")
        if len(self.model_provenance) > 1:
            raise ValueError("a deterministic local run supports at most one observer model")
        if self.kind is not InvestigationKind.GEOLOCATE_PROVENANCE:
            raise ValueError("the parent IPC boundary supports only geolocateProvenance")
        if self.connectivity_policy is not ConnectivityPolicy.LOCAL:
            raise ValueError("the parent IPC boundary supports only the local policy")


def _preflight_json_shape(value: object) -> None:
    """Bound an already-decoded object before structured reconstruction.

    The product parent must separately cap encoded bytes *before* calling
    ``json.loads``. These post-parse limits constrain nested extension objects and
    collections, but cannot undo memory allocated by the JSON parser itself.
    """

    stack: list[tuple[str, object, int]] = [("$", value, 0)]
    nodes = 0
    while stack:
        path, current, depth = stack.pop()
        nodes += 1
        if nodes > _MAX_JSON_NODES:
            raise ValueError(f"$ exceeds the maximum JSON node count {_MAX_JSON_NODES}")
        if depth > _MAX_JSON_DEPTH:
            raise ValueError(f"{path} exceeds the maximum JSON nesting depth")
        if isinstance(current, str):
            if len(current) > _MAX_STRING_CHARACTERS:
                raise ValueError(
                    f"{path} exceeds the maximum string length {_MAX_STRING_CHARACTERS}"
                )
            continue
        if current is None or isinstance(current, bool | int):
            continue
        if isinstance(current, float):
            if not math.isfinite(current):
                raise ValueError(f"{path} numbers must be finite")
            continue
        if isinstance(current, list):
            if len(current) > _MAX_COLLECTION_ITEMS:
                raise ValueError(
                    f"{path} exceeds the maximum collection size {_MAX_COLLECTION_ITEMS}"
                )
            stack.extend(
                (f"{path}[{index}]", item, depth + 1)
                for index, item in reversed(tuple(enumerate(current)))
            )
            continue
        if isinstance(current, dict):
            if len(current) > _MAX_OBJECT_FIELDS:
                raise ValueError(f"{path} exceeds the maximum field count {_MAX_OBJECT_FIELDS}")
            for key, item in reversed(tuple(current.items())):
                if not isinstance(key, str):
                    raise ValueError(f"{path} keys must be strings")
                if len(key) > _MAX_STRING_CHARACTERS:
                    raise ValueError(f"{path} contains a key exceeding the maximum string length")
                stack.append((f"{path}.{key}", item, depth + 1))
            continue
        raise ValueError(f"{path} contains a non-JSON value")


def _object(value: object, *, path: str, fields: frozenset[str]) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    if len(value) > _MAX_OBJECT_FIELDS:
        raise ValueError(f"{path} exceeds the maximum field count {_MAX_OBJECT_FIELDS}")
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{path} keys must be strings")
    actual = set(value)
    unexpected = sorted(actual - fields)
    missing = sorted(fields - actual)
    if unexpected:
        raise ValueError(f"{path} contains unexpected fields: {', '.join(unexpected)}")
    if missing:
        raise ValueError(f"{path} is missing fields: {', '.join(missing)}")
    return value


def _array(value: object, *, path: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{path} must be an array")
    if len(value) > _MAX_COLLECTION_ITEMS:
        raise ValueError(f"{path} exceeds the maximum collection size {_MAX_COLLECTION_ITEMS}")
    return value


def _string(value: object, *, path: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{path} must be a string")
    if len(value) > _MAX_STRING_CHARACTERS:
        raise ValueError(f"{path} exceeds the maximum string length {_MAX_STRING_CHARACTERS}")
    if not allow_empty and not value.strip():
        raise ValueError(f"{path} must not be empty")
    return value


def _optional_string(value: object, *, path: str) -> str | None:
    return None if value is None else _string(value, path=path)


def _boolean(value: object, *, path: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{path} must be a boolean")
    return value


def _integer(value: object, *, path: str, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{path} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{path} must be at least {minimum}")
    return value


def _optional_integer(
    value: object,
    *,
    path: str,
    minimum: int | None = None,
) -> int | None:
    return None if value is None else _integer(value, path=path, minimum=minimum)


def _number(
    value: object,
    *,
    path: str,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float | int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{path} must be a number")
    if not math.isfinite(value):
        raise ValueError(f"{path} must be finite")
    if minimum is not None and value < minimum:
        raise ValueError(f"{path} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{path} must not exceed {maximum}")
    return value


def _optional_number(
    value: object,
    *,
    path: str,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float | int | None:
    if value is None:
        return None
    return _number(value, path=path, minimum=minimum, maximum=maximum)


def _timestamp(value: object, *, path: str) -> datetime:
    encoded = _string(value, path=path)
    try:
        parsed = datetime.fromisoformat(encoded.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{path} must be an RFC 3339 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{path} must include a UTC offset")
    return parsed.astimezone(UTC)


def _optional_timestamp(value: object, *, path: str) -> datetime | None:
    return None if value is None else _timestamp(value, path=path)


def _enum[EnumT: StrEnum](value: object, enum_type: type[EnumT], *, path: str) -> EnumT:
    encoded = _string(value, path=path)
    try:
        return enum_type(encoded)
    except ValueError as error:
        raise ValueError(f"{path} is not a valid {enum_type.__name__}") from error


def _strings(value: object, *, path: str) -> tuple[str, ...]:
    return tuple(
        _string(item, path=f"{path}[{index}]")
        for index, item in enumerate(_array(value, path=path))
    )


def _json_value(value: object, *, path: str, depth: int = 0) -> JsonValue:
    if depth > _MAX_JSON_DEPTH:
        raise ValueError(f"{path} exceeds the maximum JSON nesting depth")
    if value is None or isinstance(value, str | bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} numbers must be finite")
        return value
    if isinstance(value, list):
        return [
            _json_value(item, path=f"{path}[{index}]", depth=depth + 1)
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError(f"{path} keys must be strings")
        return {
            key: _json_value(item, path=f"{path}.{key}", depth=depth + 1)
            for key, item in value.items()
        }
    raise ValueError(f"{path} contains a non-JSON value")


def _json_object(value: object, *, path: str) -> dict[str, JsonValue]:
    parsed = _json_value(value, path=path)
    if not isinstance(parsed, dict):
        raise ValueError(f"{path} must be an object")
    return parsed


def _bbox(value: object, *, path: str) -> tuple[float, float, float, float] | None:
    if value is None:
        return None
    items = _array(value, path=path)
    if len(items) != 4:
        raise ValueError(f"{path} must contain four numbers")
    parsed = tuple(_number(item, path=f"{path}[{index}]") for index, item in enumerate(items))
    return parsed  # type: ignore[return-value]


def _model(value: object, *, path: str) -> ModelProvenance:
    payload = _object(
        value,
        path=path,
        fields=frozenset({"name", "version", "digest", "runtime", "prompt_digest"}),
    )
    return ModelProvenance(
        name=_string(payload["name"], path=f"{path}.name"),
        version=_string(payload["version"], path=f"{path}.version"),
        digest=_string(payload["digest"], path=f"{path}.digest"),
        runtime=_string(payload["runtime"], path=f"{path}.runtime"),
        prompt_digest=_optional_string(payload["prompt_digest"], path=f"{path}.prompt_digest"),
    )


def _source(value: object, *, path: str) -> SourceRecord:
    payload = _object(
        value,
        path=path,
        fields=frozenset(
            {
                "source_id",
                "content_sha256",
                "collected_at",
                "license_basis",
                "publisher",
                "source_url",
                "published_at",
                "consent_basis",
                "redistribution_policy",
                "retention_policy",
            }
        ),
    )
    return SourceRecord(
        source_id=_string(payload["source_id"], path=f"{path}.source_id"),
        content_sha256=_string(payload["content_sha256"], path=f"{path}.content_sha256"),
        collected_at=_timestamp(payload["collected_at"], path=f"{path}.collected_at"),
        license_basis=_string(payload["license_basis"], path=f"{path}.license_basis"),
        publisher=_optional_string(payload["publisher"], path=f"{path}.publisher"),
        source_url=_optional_string(payload["source_url"], path=f"{path}.source_url"),
        published_at=_optional_timestamp(payload["published_at"], path=f"{path}.published_at"),
        consent_basis=_optional_string(payload["consent_basis"], path=f"{path}.consent_basis"),
        redistribution_policy=_string(
            payload["redistribution_policy"], path=f"{path}.redistribution_policy"
        ),
        retention_policy=_string(payload["retention_policy"], path=f"{path}.retention_policy"),
    )


def _media(value: object, *, path: str) -> MediaMetadata:
    payload = _object(
        value,
        path=path,
        fields=frozenset(
            {
                "path",
                "content_sha256",
                "size_bytes",
                "media_type",
                "container",
                "duration_ms",
                "width",
                "height",
                "frame_rate",
                "video_streams",
                "audio_streams",
                "perceptual_hash",
                "probe_available",
                "warnings",
            }
        ),
    )
    content_sha256 = _string(payload["content_sha256"], path=f"{path}.content_sha256")
    if len(content_sha256) != 64:
        raise ValueError(f"{path}.content_sha256 must be a SHA-256 digest")
    try:
        int(content_sha256, 16)
    except ValueError as error:
        raise ValueError(f"{path}.content_sha256 must be hexadecimal") from error
    width = _optional_integer(payload["width"], path=f"{path}.width", minimum=1)
    height = _optional_integer(payload["height"], path=f"{path}.height", minimum=1)
    return MediaMetadata(
        path=_string(payload["path"], path=f"{path}.path"),
        content_sha256=content_sha256,
        size_bytes=_integer(payload["size_bytes"], path=f"{path}.size_bytes", minimum=0),
        media_type=_string(payload["media_type"], path=f"{path}.media_type"),
        container=_optional_string(payload["container"], path=f"{path}.container"),
        duration_ms=_optional_integer(
            payload["duration_ms"], path=f"{path}.duration_ms", minimum=0
        ),
        width=width,
        height=height,
        frame_rate=_optional_number(payload["frame_rate"], path=f"{path}.frame_rate", minimum=0),
        video_streams=_integer(payload["video_streams"], path=f"{path}.video_streams", minimum=0),
        audio_streams=_integer(payload["audio_streams"], path=f"{path}.audio_streams", minimum=0),
        perceptual_hash=_optional_string(
            payload["perceptual_hash"], path=f"{path}.perceptual_hash"
        ),
        probe_available=_boolean(payload["probe_available"], path=f"{path}.probe_available"),
        warnings=_strings(payload["warnings"], path=f"{path}.warnings"),
    )


def _contribution(value: object, *, path: str) -> EvidenceContribution:
    payload = _object(
        value,
        path=path,
        fields=frozenset({"candidate_id", "score"}),
    )
    return EvidenceContribution(
        candidate_id=_string(payload["candidate_id"], path=f"{path}.candidate_id"),
        score=_number(payload["score"], path=f"{path}.score", minimum=-1, maximum=1),
    )


def _evidence(value: object, *, path: str) -> EvidenceItem:
    payload = _object(
        value,
        path=path,
        fields=frozenset(
            {
                "evidence_id",
                "observation",
                "source_id",
                "artifact_id",
                "correlation_group",
                "reliability",
                "contributions",
                "kind",
                "verification_state",
                "created_at",
                "frame_time_ms",
                "bbox_xywh",
                "attributes",
            }
        ),
    )
    contributions = tuple(
        _contribution(item, path=f"{path}.contributions[{index}]")
        for index, item in enumerate(_array(payload["contributions"], path=f"{path}.contributions"))
    )
    return EvidenceItem(
        evidence_id=_string(payload["evidence_id"], path=f"{path}.evidence_id"),
        observation=_string(payload["observation"], path=f"{path}.observation"),
        source_id=_string(payload["source_id"], path=f"{path}.source_id"),
        artifact_id=_string(payload["artifact_id"], path=f"{path}.artifact_id"),
        correlation_group=_string(payload["correlation_group"], path=f"{path}.correlation_group"),
        reliability=_number(
            payload["reliability"], path=f"{path}.reliability", minimum=0, maximum=1
        ),
        contributions=contributions,
        kind=_enum(payload["kind"], EvidenceKind, path=f"{path}.kind"),
        verification_state=_enum(
            payload["verification_state"],
            VerificationState,
            path=f"{path}.verification_state",
        ),
        created_at=_timestamp(payload["created_at"], path=f"{path}.created_at"),
        frame_time_ms=_optional_integer(
            payload["frame_time_ms"], path=f"{path}.frame_time_ms", minimum=0
        ),
        bbox_xywh=_bbox(payload["bbox_xywh"], path=f"{path}.bbox_xywh"),
        attributes=_json_object(payload["attributes"], path=f"{path}.attributes"),
    )


def _belief_candidate(value: object, *, path: str) -> BeliefCandidate:
    payload = _object(
        value,
        path=path,
        fields=frozenset({"candidate_id", "label", "log_score", "probability", "group_scores"}),
    )
    group_payload = _object_dynamic(payload["group_scores"], path=f"{path}.group_scores")
    return BeliefCandidate(
        candidate_id=_string(payload["candidate_id"], path=f"{path}.candidate_id"),
        label=_string(payload["label"], path=f"{path}.label"),
        log_score=_number(payload["log_score"], path=f"{path}.log_score"),
        probability=_number(
            payload["probability"], path=f"{path}.probability", minimum=0, maximum=1
        ),
        group_scores={
            group_id: _number(score, path=f"{path}.group_scores.{group_id}")
            for group_id, score in group_payload.items()
        },
    )


def _object_dynamic(value: object, *, path: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{path} must be an object with string keys")
    if len(value) > _MAX_OBJECT_FIELDS:
        raise ValueError(f"{path} exceeds the maximum field count {_MAX_OBJECT_FIELDS}")
    return value


def _belief(value: object, *, path: str) -> BeliefSnapshot:
    payload = _object(
        value,
        path=path,
        fields=frozenset(
            {
                "snapshot_id",
                "created_at",
                "candidates",
                "entropy",
                "normalized_entropy",
                "abstained",
                "abstention_reasons",
                "evidence_ids",
            }
        ),
    )
    return BeliefSnapshot(
        snapshot_id=_string(payload["snapshot_id"], path=f"{path}.snapshot_id"),
        created_at=_timestamp(payload["created_at"], path=f"{path}.created_at"),
        candidates=tuple(
            _belief_candidate(item, path=f"{path}.candidates[{index}]")
            for index, item in enumerate(_array(payload["candidates"], path=f"{path}.candidates"))
        ),
        entropy=_number(payload["entropy"], path=f"{path}.entropy", minimum=0),
        normalized_entropy=_number(
            payload["normalized_entropy"],
            path=f"{path}.normalized_entropy",
            minimum=0,
            maximum=1,
        ),
        abstained=_boolean(payload["abstained"], path=f"{path}.abstained"),
        abstention_reasons=_strings(
            payload["abstention_reasons"], path=f"{path}.abstention_reasons"
        ),
        evidence_ids=_strings(payload["evidence_ids"], path=f"{path}.evidence_ids"),
    )


def _step(value: object, *, path: str) -> InvestigationStep:
    payload = _object(
        value,
        path=path,
        fields=frozenset(
            {
                "step_id",
                "action",
                "status",
                "started_at",
                "completed_at",
                "input_evidence_ids",
                "output_evidence_ids",
                "model_digest",
                "prompt_digest",
                "tool_version",
                "latency_ms",
                "peak_memory_bytes",
                "cost_units",
                "egress_decision",
                "entropy_before",
                "entropy_after",
                "error",
                "attributes",
            }
        ),
    )
    return InvestigationStep(
        step_id=_string(payload["step_id"], path=f"{path}.step_id"),
        action=_enum(payload["action"], ActionType, path=f"{path}.action"),
        status=_enum(payload["status"], StepStatus, path=f"{path}.status"),
        started_at=_timestamp(payload["started_at"], path=f"{path}.started_at"),
        completed_at=_optional_timestamp(payload["completed_at"], path=f"{path}.completed_at"),
        input_evidence_ids=_strings(
            payload["input_evidence_ids"], path=f"{path}.input_evidence_ids"
        ),
        output_evidence_ids=_strings(
            payload["output_evidence_ids"], path=f"{path}.output_evidence_ids"
        ),
        model_digest=_optional_string(payload["model_digest"], path=f"{path}.model_digest"),
        prompt_digest=_optional_string(payload["prompt_digest"], path=f"{path}.prompt_digest"),
        tool_version=_optional_string(payload["tool_version"], path=f"{path}.tool_version"),
        latency_ms=_optional_integer(payload["latency_ms"], path=f"{path}.latency_ms", minimum=0),
        peak_memory_bytes=_optional_integer(
            payload["peak_memory_bytes"], path=f"{path}.peak_memory_bytes", minimum=0
        ),
        cost_units=_number(payload["cost_units"], path=f"{path}.cost_units", minimum=0),
        egress_decision=_enum(
            payload["egress_decision"], EgressDecision, path=f"{path}.egress_decision"
        ),
        entropy_before=_optional_number(
            payload["entropy_before"], path=f"{path}.entropy_before", minimum=0
        ),
        entropy_after=_optional_number(
            payload["entropy_after"], path=f"{path}.entropy_after", minimum=0
        ),
        error=_optional_string(payload["error"], path=f"{path}.error"),
        attributes=_json_object(payload["attributes"], path=f"{path}.attributes"),
    )


def _investigation(value: object, *, path: str) -> Investigation:
    payload = _object(
        value,
        path=path,
        fields=frozenset(
            {
                "investigation_id",
                "kind",
                "connectivity_policy",
                "status",
                "source_id",
                "trace_id",
                "created_at",
                "updated_at",
                "model_provenance",
                "final_hypothesis_id",
                "confidence",
                "abstained",
            }
        ),
    )
    return Investigation(
        investigation_id=_string(payload["investigation_id"], path=f"{path}.investigation_id"),
        kind=_enum(payload["kind"], InvestigationKind, path=f"{path}.kind"),
        connectivity_policy=_enum(
            payload["connectivity_policy"],
            ConnectivityPolicy,
            path=f"{path}.connectivity_policy",
        ),
        status=_enum(payload["status"], InvestigationStatus, path=f"{path}.status"),
        source_id=_string(payload["source_id"], path=f"{path}.source_id"),
        trace_id=_string(payload["trace_id"], path=f"{path}.trace_id"),
        created_at=_timestamp(payload["created_at"], path=f"{path}.created_at"),
        updated_at=_timestamp(payload["updated_at"], path=f"{path}.updated_at"),
        model_provenance=tuple(
            _model(item, path=f"{path}.model_provenance[{index}]")
            for index, item in enumerate(
                _array(payload["model_provenance"], path=f"{path}.model_provenance")
            )
        ),
        final_hypothesis_id=_optional_string(
            payload["final_hypothesis_id"], path=f"{path}.final_hypothesis_id"
        ),
        confidence=_optional_number(
            payload["confidence"], path=f"{path}.confidence", minimum=0, maximum=1
        ),
        abstained=_boolean(payload["abstained"], path=f"{path}.abstained"),
    )


def _trace_event(value: object, *, path: str) -> TraceEvent:
    payload = _object(
        value,
        path=path,
        fields=frozenset({"trace_id", "sequence", "event_type", "occurred_at", "payload"}),
    )
    return TraceEvent(
        trace_id=_string(payload["trace_id"], path=f"{path}.trace_id"),
        sequence=_integer(payload["sequence"], path=f"{path}.sequence", minimum=0),
        event_type=_enum(payload["event_type"], TraceEventType, path=f"{path}.event_type"),
        occurred_at=_timestamp(payload["occurred_at"], path=f"{path}.occurred_at"),
        payload=_json_object(payload["payload"], path=f"{path}.payload"),
    )


def _assert_close(actual: float, expected: float, *, label: str) -> None:
    if not math.isclose(actual, expected, rel_tol=1e-12, abs_tol=_FLOAT_TOLERANCE):
        raise ValueError(f"{label} is inconsistent: expected {expected!r}, got {actual!r}")


def _validate_probability_math(belief: BeliefSnapshot) -> None:
    probabilities = tuple(candidate.probability for candidate in belief.candidates)
    entropy = -sum(value * math.log(value) for value in probabilities if value > 0)
    maximum_entropy = math.log(len(probabilities)) if len(probabilities) > 1 else 0
    normalized = min(1.0, max(0.0, entropy / maximum_entropy)) if maximum_entropy else 0
    _assert_close(belief.entropy, entropy, label="$.belief.entropy")
    _assert_close(
        belief.normalized_entropy,
        normalized,
        label="$.belief.normalized_entropy",
    )

    expected_order = tuple(
        sorted(belief.candidates, key=lambda item: (-item.probability, item.candidate_id))
    )
    if belief.candidates != expected_order:
        raise ValueError(
            "$.belief candidates must be ordered by descending probability and candidate ID"
        )

    # A temperature-scaled softmax preserves all pairwise log-score odds. The
    # temperature is not carried by LocalRunResult, so infer it from positive
    # probabilities and reject any mutually inconsistent ratios. An authoritative
    # caller can additionally pass priors/config below for an exact recomputation.
    inferred_temperatures: list[float] = []
    for left_index, left in enumerate(belief.candidates):
        for right in belief.candidates[left_index + 1 :]:
            if left.probability == 0 or right.probability == 0:
                continue
            score_delta = left.log_score - right.log_score
            odds_delta = math.log(left.probability) - math.log(right.probability)
            if abs(score_delta) <= 1e-12:
                if abs(odds_delta) > _FLOAT_TOLERANCE:
                    raise ValueError(
                        "$.belief probabilities are inconsistent with equal log scores"
                    )
                continue
            if abs(odds_delta) <= 1e-12 or score_delta * odds_delta <= 0:
                raise ValueError("$.belief probabilities are inconsistent with log-score ordering")
            inferred_temperatures.append(score_delta / odds_delta)

    if inferred_temperatures:
        temperature = inferred_temperatures[0]
        if not math.isfinite(temperature) or temperature <= 0:
            raise ValueError("$.belief implies a non-positive softmax temperature")
        for inferred in inferred_temperatures[1:]:
            if not math.isclose(inferred, temperature, rel_tol=1e-7, abs_tol=1e-9):
                raise ValueError("$.belief probabilities imply inconsistent softmax temperatures")
        scaled = tuple(candidate.log_score / temperature for candidate in belief.candidates)
        maximum = max(scaled)
        exponentials = tuple(math.exp(value - maximum) for value in scaled)
        denominator = sum(exponentials)
        expected_probabilities = tuple(value / denominator for value in exponentials)
        for index, (actual, expected) in enumerate(
            zip(probabilities, expected_probabilities, strict=True)
        ):
            _assert_close(
                actual,
                expected,
                label=f"$.belief.candidates[{index}].probability",
            )
    elif len(belief.candidates) > 1:
        log_scores = tuple(candidate.log_score for candidate in belief.candidates)
        if max(log_scores) - min(log_scores) <= 1e-12:
            expected = 1 / len(belief.candidates)
            for index, probability in enumerate(probabilities):
                _assert_close(
                    probability,
                    expected,
                    label=f"$.belief.candidates[{index}].probability",
                )
        else:
            positive = [candidate for candidate in belief.candidates if candidate.probability > 0]
            zero = [candidate for candidate in belief.candidates if candidate.probability == 0]
            positive_scores = tuple(candidate.log_score for candidate in positive)
            positive_probabilities = tuple(candidate.probability for candidate in positive)
            if (
                not positive
                or max(positive_scores) - min(positive_scores) > 1e-12
                or max(positive_probabilities) - min(positive_probabilities) > _FLOAT_TOLERANCE
                or any(candidate.log_score >= min(positive_scores) for candidate in zero)
            ):
                raise ValueError("$.belief probabilities are inconsistent with log scores")


def _validate_group_scores(
    belief: BeliefSnapshot,
    evidence: tuple[EvidenceItem, ...],
) -> None:
    active = tuple(
        sorted(
            (
                item
                for item in evidence
                if item.verification_state is not VerificationState.REJECTED
            ),
            key=lambda item: item.evidence_id,
        )
    )
    for candidate in belief.candidates:
        contributions: dict[str, list[tuple[EvidenceItem, float]]] = {}
        for item in active:
            for contribution in item.contributions:
                if contribution.candidate_id == candidate.candidate_id:
                    contributions.setdefault(item.correlation_group, []).append(
                        (item, contribution.score)
                    )
        required_groups = {
            group_id
            for group_id, items in contributions.items()
            if any(item.verification_state is not VerificationState.UNVERIFIED for item, _ in items)
        }
        actual_groups = set(candidate.group_scores)
        if required_groups - actual_groups or actual_groups - set(contributions):
            raise ValueError(
                f"$.belief candidate {candidate.candidate_id!r} group_scores do not match evidence"
            )
        for group_id, items in contributions.items():
            if group_id not in candidate.group_scores:
                continue
            if not group_id.strip():
                raise ValueError("$.belief group_scores keys must not be empty")
            actual = candidate.group_scores[group_id]
            fixed_values: list[float] = []
            maximum_unverified = 0.0
            possible_signs: set[int] = set()
            for item, score in items:
                raw = item.reliability * score
                possible_signs.add((raw > 0) - (raw < 0))
                if item.verification_state is VerificationState.UNVERIFIED:
                    maximum_unverified = max(maximum_unverified, abs(raw))
                else:
                    fixed_values.append(raw)
            fixed: float | None = None
            if fixed_values:
                maximum_fixed = max(abs(value) for value in fixed_values)
                strongest = tuple(
                    value
                    for value in fixed_values
                    if math.isclose(
                        abs(value),
                        maximum_fixed,
                        rel_tol=1e-12,
                        abs_tol=1e-12,
                    )
                )
                has_positive = any(value > 0 for value in strongest)
                has_negative = any(value < 0 for value in strongest)
                if has_positive and has_negative:
                    fixed = 0.0
                elif has_positive:
                    fixed = maximum_fixed
                elif has_negative:
                    fixed = -maximum_fixed
                else:
                    fixed = 0.0
            maximum_fixed = max((abs(value) for value in fixed_values), default=0.0)
            maximum = max(maximum_fixed, maximum_unverified)
            if abs(actual) > maximum + _FLOAT_TOLERANCE:
                raise ValueError(
                    f"$.belief candidate {candidate.candidate_id!r} group {group_id!r} "
                    "exceeds evidence support"
                )
            actual_sign = (actual > 0) - (actual < 0)
            if actual_sign not in possible_signs and actual_sign != 0:
                raise ValueError(
                    f"$.belief candidate {candidate.candidate_id!r} group {group_id!r} "
                    "has an unsupported sign"
                )
            if maximum_unverified == 0 or maximum_fixed > maximum_unverified:
                _assert_close(
                    actual,
                    fixed if fixed is not None else 0,
                    label=(
                        f"$.belief candidate {candidate.candidate_id!r} group {group_id!r} score"
                    ),
                )


def _validate_belief_integrity(
    belief: BeliefSnapshot,
    evidence: tuple[EvidenceItem, ...],
    *,
    expected_candidates: tuple[CandidatePrior, ...] | None,
    expected_belief_config: BeliefConfig | None,
) -> None:
    expected_evidence_ids = tuple(
        sorted(
            item.evidence_id
            for item in evidence
            if item.verification_state is not VerificationState.REJECTED
        )
    )
    if belief.evidence_ids != expected_evidence_ids:
        raise ValueError("$.belief.evidence_ids must exactly match active evidence")
    snapshot_suffix = belief.snapshot_id.removeprefix("belief-")
    if (
        len(snapshot_suffix) != 20
        or belief.snapshot_id != f"belief-{snapshot_suffix}"
        or any(character not in "0123456789abcdef" for character in snapshot_suffix)
    ):
        raise ValueError("$.belief.snapshot_id must be a canonical belief digest identifier")
    if belief.abstained != bool(belief.abstention_reasons):
        raise ValueError("$.belief.abstained must match the presence of abstention reasons")
    _validate_probability_math(belief)
    _validate_group_scores(belief, evidence)

    if expected_belief_config is not None and expected_candidates is None:
        raise ValueError("expected_belief_config requires expected_candidates")
    if expected_candidates is not None:
        expected = update_beliefs(
            expected_candidates,
            evidence,
            config=expected_belief_config,
            created_at=belief.created_at,
        )
        if belief != expected:
            raise ValueError(
                "$.belief does not match the caller-bound candidate priors and belief config"
            )


def _validate_step_and_trace_spine(
    *,
    investigation: Investigation,
    source: SourceRecord,
    media: MediaMetadata,
    evidence: tuple[EvidenceItem, ...],
    belief: BeliefSnapshot,
    steps: tuple[InvestigationStep, ...],
    trace: tuple[TraceEvent, ...],
) -> None:
    if investigation.connectivity_policy is not ConnectivityPolicy.LOCAL:
        raise ValueError("a deterministic local result must use the local connectivity policy")
    if investigation.status is not InvestigationStatus.NEEDS_REVIEW:
        raise ValueError("a deterministic local result must end in needs_review")
    if len(steps) != 3:
        raise ValueError("$.steps must contain the deterministic inspect, observe and review spine")
    inspect_step, observe_step, review_step = steps
    expected_actions = (
        ActionType.INSPECT_MEDIA,
        ActionType.OBSERVE,
        ActionType.REQUEST_REVIEW,
    )
    if tuple(step.action for step in steps) != expected_actions:
        raise ValueError("$.steps do not match the deterministic action spine")
    if any(step.status is not StepStatus.SUCCEEDED or step.completed_at is None for step in steps):
        raise ValueError("every deterministic local step must be completed successfully")
    if inspect_step.input_evidence_ids or inspect_step.output_evidence_ids:
        raise ValueError("the inspect step must not reference evidence")
    expected_evidence_ids = tuple(sorted(item.evidence_id for item in evidence))
    if observe_step.input_evidence_ids or observe_step.output_evidence_ids != expected_evidence_ids:
        raise ValueError("the observe step must output every evidence item exactly once")
    if review_step.input_evidence_ids != belief.evidence_ids or review_step.output_evidence_ids:
        raise ValueError("the review step must consume exactly the active belief evidence")
    if review_step.entropy_after is None:
        raise ValueError("the review step must record posterior entropy")
    _assert_close(
        review_step.entropy_after,
        belief.entropy,
        label="$.steps review entropy_after",
    )
    media_audit_payload = to_primitive(media)
    if not isinstance(media_audit_payload, dict):  # defensive; MediaMetadata is a dataclass
        raise ValueError("$.media must serialize to an object")
    media_audit_payload.pop("path", None)
    if inspect_step.attributes != {"media": media_audit_payload}:
        raise ValueError("the inspect step media attributes do not match $.media")
    if set(observe_step.attributes) != {"warnings"} or not isinstance(
        observe_step.attributes["warnings"], list
    ):
        raise ValueError("the observe step must contain only a warnings array")
    if any(not isinstance(item, str) for item in observe_step.attributes["warnings"]):
        raise ValueError("the observe step warnings must be strings")
    if review_step.attributes:
        raise ValueError("the deterministic review step must not contain extension attributes")
    if any(
        step.egress_decision is not EgressDecision.NOT_APPLICABLE
        or step.latency_ms is not None
        or step.peak_memory_bytes is not None
        or step.cost_units != 0
        or step.error is not None
        for step in steps
    ):
        raise ValueError("deterministic local steps must not claim egress, cost or runtime metrics")
    if (
        inspect_step.model_digest is not None
        or inspect_step.prompt_digest is not None
        or inspect_step.entropy_before is not None
        or inspect_step.entropy_after is not None
        or inspect_step.tool_version != "investigation-core/0.1.0"
    ):
        raise ValueError("the inspect step contains inconsistent deterministic metadata")
    if observe_step.entropy_before is not None or observe_step.entropy_after is not None:
        raise ValueError("the observe step cannot claim an unrecorded entropy transition")
    if (
        review_step.model_digest is not None
        or review_step.prompt_digest is not None
        or review_step.entropy_before is not None
        or review_step.tool_version != "investigation-core/0.1.0"
    ):
        raise ValueError("the review step contains inconsistent deterministic metadata")

    models = investigation.model_provenance
    if len(models) > 1:
        raise ValueError("a deterministic local result may reference at most one observer model")
    if models:
        model = models[0]
        if (
            observe_step.model_digest != model.digest
            or observe_step.prompt_digest != model.prompt_digest
            or observe_step.tool_version != model.version
        ):
            raise ValueError("the observe step provenance does not match the investigation model")
    elif observe_step.model_digest is not None or observe_step.prompt_digest is not None:
        raise ValueError("the observe step cannot claim model provenance absent from investigation")
    elif observe_step.tool_version != "static/1":
        raise ValueError("the model-free observe step must use the static adapter version")

    if not trace:
        raise ValueError("$.trace must not be empty")
    expected_events: list[tuple[TraceEventType, dict[str, JsonValue]]] = [
        (
            TraceEventType.INVESTIGATION_STARTED,
            {
                "investigationId": investigation.investigation_id,
                "kind": investigation.kind.value,
                "connectivityPolicy": investigation.connectivity_policy.value,
                "sourceId": source.source_id,
            },
        ),
        (
            TraceEventType.MEDIA_INSPECTED,
            {"stepId": inspect_step.step_id, "media": media_audit_payload},
        ),
        (
            TraceEventType.STEP_STARTED,
            {"action": ActionType.OBSERVE.value, "stepId": observe_step.step_id},
        ),
    ]
    expected_events.extend(
        (
            TraceEventType.EVIDENCE_RECORDED,
            {"stepId": observe_step.step_id, "evidence": to_primitive(item)},
        )
        for item in sorted(evidence, key=lambda item: item.evidence_id)
    )
    expected_events.extend(
        [
            (
                TraceEventType.BELIEF_UPDATED,
                {"stepId": observe_step.step_id, "belief": to_primitive(belief)},
            ),
            (TraceEventType.STEP_COMPLETED, {"step": to_primitive(observe_step)}),
            (
                TraceEventType.INVESTIGATION_NEEDS_REVIEW,
                {
                    "beliefSnapshotId": belief.snapshot_id,
                    "investigationId": investigation.investigation_id,
                    "abstained": belief.abstained,
                },
            ),
        ]
    )
    if len(trace) != len(expected_events):
        raise ValueError("$.trace does not match the deterministic event spine")
    for index, (event, (expected_type, expected_payload)) in enumerate(
        zip(trace, expected_events, strict=True)
    ):
        if event.event_type is not expected_type:
            raise ValueError(f"$.trace[{index}] has an unexpected event type")
        if event.payload != expected_payload:
            raise ValueError(f"$.trace[{index}] payload is inconsistent with structured result")


def local_run_result_to_primitive(result: LocalRunResult) -> dict[str, JsonValue]:
    """Return the canonical object passed across the JSON IPC boundary."""

    primitive = to_primitive(result)
    if not isinstance(primitive, dict):  # defensive; LocalRunResult is a dataclass
        raise TypeError("LocalRunResult did not serialize to an object")
    _preflight_json_shape(primitive)
    return primitive


def _unsafe_local_run_result_from_primitive(
    payload: Mapping[str, object],
    *,
    expected_candidates: tuple[CandidatePrior, ...] | None = None,
    expected_belief_config: BeliefConfig | None = None,
) -> LocalRunResult:
    """Decode a self-consistent result without binding it to parent-owned identity.

    This private helper is intentionally unsafe for a child-process trust boundary:
    a hostile child can alter several mutually consistent fields at once. Public
    parent code must call ``local_run_result_from_primitive`` with a durable
    ``LocalRunExpectation`` instead.
    """

    if not isinstance(payload, Mapping):
        raise ValueError("$ must be an object")
    materialized_payload = dict(payload)
    _preflight_json_shape(materialized_payload)
    root = _object(
        materialized_payload,
        path="$",
        fields=frozenset(
            {"investigation", "source", "media", "evidence", "belief", "steps", "trace"}
        ),
    )
    investigation = _investigation(root["investigation"], path="$.investigation")
    source = _source(root["source"], path="$.source")
    media = _media(root["media"], path="$.media")
    evidence = tuple(
        _evidence(item, path=f"$.evidence[{index}]")
        for index, item in enumerate(_array(root["evidence"], path="$.evidence"))
    )
    belief = _belief(root["belief"], path="$.belief")
    steps = tuple(
        _step(item, path=f"$.steps[{index}]")
        for index, item in enumerate(_array(root["steps"], path="$.steps"))
    )
    trace = validate_trace(
        _trace_event(item, path=f"$.trace[{index}]")
        for index, item in enumerate(_array(root["trace"], path="$.trace"))
    )

    if investigation.source_id != source.source_id:
        raise ValueError("$.investigation.source_id must match $.source.source_id")
    if source.content_sha256 != media.content_sha256:
        raise ValueError("$.source.content_sha256 must match $.media.content_sha256")
    evidence_ids = [item.evidence_id for item in evidence]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("$.evidence IDs must be unique")
    if any(item.source_id != source.source_id for item in evidence):
        raise ValueError("every evidence source_id must match $.source.source_id")
    evidence_id_set = set(evidence_ids)
    if len(belief.evidence_ids) != len(set(belief.evidence_ids)):
        raise ValueError("$.belief.evidence_ids must be unique")
    unknown_belief_evidence = set(belief.evidence_ids) - evidence_id_set
    if unknown_belief_evidence:
        raise ValueError("$.belief.evidence_ids reference unknown evidence")
    belief_candidate_ids = [candidate.candidate_id for candidate in belief.candidates]
    if len(belief_candidate_ids) != len(set(belief_candidate_ids)):
        raise ValueError("$.belief candidate IDs must be unique")
    known_candidates = set(belief_candidate_ids)
    if any(
        contribution.candidate_id not in known_candidates
        for item in evidence
        for contribution in item.contributions
    ):
        raise ValueError("$.evidence contributions reference an unknown belief candidate")
    _validate_belief_integrity(
        belief,
        evidence,
        expected_candidates=expected_candidates,
        expected_belief_config=expected_belief_config,
    )
    step_ids = [step.step_id for step in steps]
    if len(step_ids) != len(set(step_ids)):
        raise ValueError("$.steps IDs must be unique")
    for step in steps:
        if (set(step.input_evidence_ids) | set(step.output_evidence_ids)) - evidence_id_set:
            raise ValueError(f"step {step.step_id!r} references unknown evidence")
    if trace and any(event.trace_id != investigation.trace_id for event in trace):
        raise ValueError("every trace event must match $.investigation.trace_id")
    if investigation.abstained != belief.abstained:
        raise ValueError("$.investigation.abstained must match $.belief.abstained")
    candidate_probabilities = {
        candidate.candidate_id: candidate.probability for candidate in belief.candidates
    }
    if investigation.abstained:
        if investigation.final_hypothesis_id is not None or investigation.confidence is not None:
            raise ValueError("an abstained investigation cannot contain a final hypothesis")
    else:
        final_id = investigation.final_hypothesis_id
        if final_id is None or investigation.confidence is None:
            raise ValueError(
                "a non-abstained investigation needs a final hypothesis and confidence"
            )
        if final_id not in candidate_probabilities:
            raise ValueError("$.investigation.final_hypothesis_id is not a belief candidate")
        if final_id != belief.candidates[0].candidate_id:
            raise ValueError("$.investigation.final_hypothesis_id must be the top-ranked candidate")
        if abs(investigation.confidence - candidate_probabilities[final_id]) > 1e-9:
            raise ValueError(
                "$.investigation.confidence must match the final candidate probability"
            )

    _validate_step_and_trace_spine(
        investigation=investigation,
        source=source,
        media=media,
        evidence=evidence,
        belief=belief,
        steps=steps,
        trace=trace,
    )

    return LocalRunResult(
        investigation=investigation,
        source=source,
        media=media,
        evidence=evidence,
        belief=belief,
        steps=steps,
        trace=trace,
    )


def local_run_result_from_primitive(
    payload: Mapping[str, object],
    *,
    expected: LocalRunExpectation,
) -> LocalRunResult:
    """Decode child IPC and bind every security-critical value to parent state.

    ``expected`` must be constructed from the parent-owned job, source, model and
    candidate configuration before the child is launched. Unknown fields, malformed
    structures, inconsistent evidence/trace content and any mismatch with that
    expectation fail closed. The parent must cap encoded bytes before JSON parsing;
    the decoder's size limits apply only after the object has been materialized.
    """

    if not isinstance(expected, LocalRunExpectation):
        raise TypeError("expected must be a parent-owned LocalRunExpectation")
    result = _unsafe_local_run_result_from_primitive(
        payload,
        expected_candidates=expected.candidates,
        expected_belief_config=expected.belief_config,
    )
    mismatches: list[str] = []
    if result.source != expected.source:
        mismatches.append("source")
    if result.investigation.investigation_id != expected.investigation_id:
        mismatches.append("investigation_id")
    if result.investigation.trace_id != expected.trace_id:
        mismatches.append("trace_id")
    if result.investigation.kind is not expected.kind:
        mismatches.append("kind")
    if result.investigation.connectivity_policy is not expected.connectivity_policy:
        mismatches.append("connectivity_policy")
    if result.investigation.model_provenance != expected.model_provenance:
        mismatches.append("model_provenance")
    if mismatches:
        raise ValueError("child result does not match parent expectation: " + ", ".join(mismatches))
    return result
