"""Deterministic local-only runner for fixtures, demos and integration tests."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from .adapters import ObservationAdapter
from .belief import BeliefConfig, update_beliefs
from .media import MediaMetadata, fingerprint_media
from .models import (
    ActionType,
    BeliefSnapshot,
    CandidatePrior,
    ConnectivityPolicy,
    EvidenceBatch,
    EvidenceItem,
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
    utc_now,
)
from .serialization import canonical_json, to_primitive
from .trace import TraceRecorder, write_trace_jsonl

MediaInspector = Callable[[Path], MediaMetadata]


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x00".join(parts).encode()).hexdigest()
    return f"{prefix}-{digest[:20]}"


def _require_parent_identifier(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    return value


def _media_audit_payload(media: MediaMetadata) -> dict[str, JsonValue]:
    """Return stable media metadata without exposing the caller's filesystem path."""

    payload = to_primitive(media)
    if not isinstance(payload, dict):  # pragma: no cover - MediaMetadata is a dataclass
        raise TypeError("media metadata must serialize to an object")
    payload.pop("path", None)
    return payload


class StaticObservationAdapter:
    """Return caller-provided observations without model or network access."""

    def __init__(
        self,
        evidence: tuple[EvidenceItem, ...],
        *,
        provenance: ModelProvenance | None = None,
        warnings: tuple[str, ...] = (),
    ) -> None:
        self._evidence = evidence
        self._provenance = provenance
        self._warnings = warnings

    @property
    def provenance(self) -> ModelProvenance | None:
        return self._provenance

    @property
    def network_access(self) -> bool:
        return False

    def observe(
        self,
        media_path: Path,
        *,
        source: SourceRecord,
        media: MediaMetadata,
    ) -> EvidenceBatch:
        del media_path, media
        evidence = tuple(replace(item, source_id=source.source_id) for item in self._evidence)
        return EvidenceBatch(evidence=evidence, model=self.provenance, warnings=self._warnings)


@dataclass(frozen=True, slots=True)
class LocalRunResult:
    investigation: Investigation
    source: SourceRecord
    media: MediaMetadata
    evidence: tuple[EvidenceItem, ...]
    belief: BeliefSnapshot
    steps: tuple[InvestigationStep, ...]
    trace: tuple[TraceEvent, ...]

    def export_trace(self, destination: Path) -> None:
        write_trace_jsonl(self.trace, destination)


class DeterministicLocalRunner:
    """Execute only the bounded, offline portion of an investigation.

    This runner intentionally refuses connected policies. Product egress requires a
    separate authorization and audit boundary and is not part of the open fake runner.
    """

    def __init__(
        self,
        *,
        observer: ObservationAdapter,
        candidates: tuple[CandidatePrior, ...],
        belief_config: BeliefConfig | None = None,
        media_inspector: MediaInspector = fingerprint_media,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if not candidates:
            raise ValueError("candidates must not be empty")
        self._observer = observer
        self._candidates = candidates
        self._belief_config = belief_config or BeliefConfig()
        self._media_inspector = media_inspector
        self._clock = clock

    def run(
        self,
        media_path: Path,
        *,
        connectivity_policy: ConnectivityPolicy = ConnectivityPolicy.LOCAL,
        kind: InvestigationKind = InvestigationKind.GEOLOCATE_PROVENANCE,
        source: SourceRecord | None = None,
        source_id: str | None = None,
        investigation_id: str | None = None,
        trace_id: str | None = None,
        publisher: str | None = None,
        source_url: str | None = None,
        license_basis: str | None = None,
    ) -> LocalRunResult:
        if connectivity_policy is not ConnectivityPolicy.LOCAL:
            raise ValueError("DeterministicLocalRunner accepts only the local policy")
        if kind is not InvestigationKind.GEOLOCATE_PROVENANCE:
            raise ValueError(
                "DeterministicLocalRunner implements only the geolocateProvenance kind"
            )
        if self._observer.network_access:
            raise ValueError("DeterministicLocalRunner requires a network-free observer")
        if (investigation_id is None) != (trace_id is None):
            raise ValueError(
                "investigation_id and trace_id must be supplied together when parent-owned"
            )
        legacy_source_arguments = (source_id, publisher, source_url, license_basis)
        if source is not None and any(value is not None for value in legacy_source_arguments):
            raise ValueError(
                "source is mutually exclusive with source_id, publisher, source_url and "
                "license_basis"
            )
        if source is not None and not isinstance(source, SourceRecord):
            raise TypeError("source must be a SourceRecord")
        if source is None and license_basis is None:
            raise ValueError("license_basis is required when source is not supplied")
        observer_provenance = self._observer.provenance

        media = self._media_inspector(media_path)
        created_at = self._clock()
        if source is None:
            source_identity = canonical_json(
                {
                    "contentSha256": media.content_sha256,
                    "licenseBasis": license_basis,
                    "publisher": publisher,
                    "sourceUrl": source_url,
                }
            )
            source_identifier = (
                _stable_id("source", source_identity)
                if source_id is None
                else _require_parent_identifier(source_id, "source_id")
            )
            source = SourceRecord(
                source_id=source_identifier,
                content_sha256=media.content_sha256,
                collected_at=created_at,
                license_basis=license_basis,
                publisher=publisher,
                source_url=source_url,
            )
        elif source.content_sha256 != media.content_sha256:
            raise ValueError("source content SHA-256 does not match the inspected media")
        run_identity = canonical_json(
            {
                "beliefConfig": self._belief_config,
                "candidates": tuple(
                    sorted(self._candidates, key=lambda candidate: candidate.candidate_id)
                ),
                "connectivityPolicy": connectivity_policy,
                "kind": kind,
                "model": observer_provenance,
                "source": source,
                "version": "investigation-core/0.1.0",
            }
        )
        if investigation_id is None:
            investigation_id = _stable_id("investigation", run_identity)
            trace_id = _stable_id("trace", investigation_id, run_identity)
        else:
            investigation_id = _require_parent_identifier(investigation_id, "investigation_id")
            trace_id = _require_parent_identifier(trace_id, "trace_id")
        recorder = TraceRecorder(trace_id, clock=self._clock)
        recorder.record(
            TraceEventType.INVESTIGATION_STARTED,
            {
                "investigationId": investigation_id,
                "kind": kind.value,
                "connectivityPolicy": connectivity_policy.value,
                "sourceId": source.source_id,
            },
        )

        inspect_started = self._clock()
        media_audit_payload = _media_audit_payload(media)
        inspect_step = InvestigationStep(
            step_id=_stable_id("step", investigation_id, "inspect"),
            action=ActionType.INSPECT_MEDIA,
            status=StepStatus.SUCCEEDED,
            started_at=inspect_started,
            completed_at=self._clock(),
            tool_version="investigation-core/0.1.0",
            attributes={"media": media_audit_payload},
        )
        recorder.record(
            TraceEventType.MEDIA_INSPECTED,
            {"stepId": inspect_step.step_id, "media": media_audit_payload},
        )

        observe_started = self._clock()
        observe_step_id = _stable_id("step", investigation_id, "observe")
        recorder.record(
            TraceEventType.STEP_STARTED,
            {"action": ActionType.OBSERVE.value, "stepId": observe_step_id},
        )
        batch = self._observer.observe(media_path, source=source, media=media)
        if batch.model != observer_provenance:
            raise ValueError("observer returned model provenance that differs from its declaration")
        evidence_ids = [item.evidence_id for item in batch.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("observer returned duplicate evidence IDs")
        if any(item.source_id != source.source_id for item in batch.evidence):
            raise ValueError("all observed evidence must reference the investigation source")

        observe_step = InvestigationStep(
            step_id=observe_step_id,
            action=ActionType.OBSERVE,
            status=StepStatus.SUCCEEDED,
            started_at=observe_started,
            completed_at=self._clock(),
            output_evidence_ids=tuple(sorted(evidence_ids)),
            model_digest=batch.model.digest if batch.model else None,
            prompt_digest=batch.model.prompt_digest if batch.model else None,
            tool_version=batch.model.version if batch.model else "static/1",
            attributes={"warnings": list(batch.warnings)},
        )
        for item in sorted(batch.evidence, key=lambda evidence: evidence.evidence_id):
            recorder.record(
                TraceEventType.EVIDENCE_RECORDED,
                {"stepId": observe_step_id, "evidence": to_primitive(item)},
            )

        belief = update_beliefs(
            self._candidates,
            batch.evidence,
            config=self._belief_config,
            created_at=self._clock(),
        )
        recorder.record(
            TraceEventType.BELIEF_UPDATED,
            {"stepId": observe_step_id, "belief": to_primitive(belief)},
        )
        recorder.record(
            TraceEventType.STEP_COMPLETED,
            {"step": to_primitive(observe_step)},
        )

        review_started = self._clock()
        review_step = InvestigationStep(
            step_id=_stable_id("step", investigation_id, "review"),
            action=ActionType.REQUEST_REVIEW,
            status=StepStatus.SUCCEEDED,
            started_at=review_started,
            completed_at=self._clock(),
            input_evidence_ids=belief.evidence_ids,
            entropy_after=belief.entropy,
            tool_version="investigation-core/0.1.0",
        )
        recorder.record(
            TraceEventType.INVESTIGATION_NEEDS_REVIEW,
            {
                "beliefSnapshotId": belief.snapshot_id,
                "investigationId": investigation_id,
                "abstained": belief.abstained,
            },
        )
        top = belief.candidates[0]
        now = self._clock()
        models = (batch.model,) if batch.model else ()
        investigation = Investigation(
            investigation_id=investigation_id,
            kind=kind,
            connectivity_policy=connectivity_policy,
            status=InvestigationStatus.NEEDS_REVIEW,
            source_id=source.source_id,
            trace_id=trace_id,
            created_at=created_at,
            updated_at=now,
            model_provenance=models,
            final_hypothesis_id=None if belief.abstained else top.candidate_id,
            confidence=None if belief.abstained else top.probability,
            abstained=belief.abstained,
        )
        return LocalRunResult(
            investigation=investigation,
            source=source,
            media=media,
            evidence=batch.evidence,
            belief=belief,
            steps=(inspect_step, observe_step, review_step),
            trace=recorder.events,
        )
