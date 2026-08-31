from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import replace

import pytest
from conftest import FIXED_TIME, evidence_item

from instadescribe_investigation_core import (
    BeliefConfig,
    CandidatePrior,
    DeterministicLocalRunner,
    LocalRunExpectation,
    MediaMetadata,
    SourceRecord,
    StaticObservationAdapter,
    canonical_json,
    local_run_result_from_primitive,
    local_run_result_to_primitive,
)

CANDIDATES = (
    CandidatePrior("pl", "Poland", 0.5),
    CandidatePrior("sk", "Slovakia", 0.5),
)
FIXTURE_BYTES = b"licensed fixture"
FIXTURE_SHA256 = hashlib.sha256(FIXTURE_BYTES).hexdigest()
SOURCE_ID = "source-1"
INVESTIGATION_ID = "parent-investigation-1"
TRACE_ID = "parent-trace-1"
PARENT_SOURCE = SourceRecord(
    source_id=SOURCE_ID,
    content_sha256=FIXTURE_SHA256,
    collected_at=FIXED_TIME,
    license_basis="CC-BY-4.0",
    publisher="Fixture Publisher",
    source_url="https://publisher.example/fixture",
    published_at=FIXED_TIME,
    consent_basis="writtenFixturePermission",
    redistribution_policy="redistributableFixture",
    retention_policy="deleteAfterEvaluation",
)


def trace_event(payload, event_type):
    return next(item for item in payload["trace"] if item["event_type"] == event_type)


def parent_expectation() -> LocalRunExpectation:
    """Build the authoritative binding without reading any child-owned result."""

    return LocalRunExpectation(
        source=PARENT_SOURCE,
        investigation_id=INVESTIGATION_ID,
        trace_id=TRACE_ID,
        candidates=CANDIDATES,
        model_provenance=(),
        belief_config=BeliefConfig(),
    )


def run_result(
    tmp_path,
    *,
    source: SourceRecord = PARENT_SOURCE,
    investigation_id: str | None = INVESTIGATION_ID,
    trace_id: str | None = TRACE_ID,
    license_basis: str | None = None,
):
    media_path = tmp_path / "fixture.mp4"
    media_path.write_bytes(FIXTURE_BYTES)

    def inspect(_path):
        return MediaMetadata(
            path=str(media_path),
            content_sha256=FIXTURE_SHA256,
            size_bytes=media_path.stat().st_size,
            media_type="video/mp4",
        )

    runner = DeterministicLocalRunner(
        observer=StaticObservationAdapter(
            (
                evidence_item("sign", group="sign", score=0.9),
                evidence_item("domain", group="domain", score=0.8),
            )
        ),
        candidates=CANDIDATES,
        media_inspector=inspect,
        clock=lambda: FIXED_TIME,
    )
    return runner.run(
        media_path,
        source=source,
        investigation_id=investigation_id,
        trace_id=trace_id,
        license_basis=license_basis,
    )


def test_local_run_result_primitive_round_trip_is_canonical(tmp_path) -> None:
    expected = parent_expectation()
    original = run_result(tmp_path)

    primitive = json.loads(canonical_json(local_run_result_to_primitive(original)))
    reconstructed = local_run_result_from_primitive(primitive, expected=expected)

    assert reconstructed == original
    assert canonical_json(reconstructed) == canonical_json(original)


@pytest.mark.parametrize(
    ("investigation_id", "trace_id"),
    [(INVESTIGATION_ID, None), (None, TRACE_ID)],
)
def test_parent_owned_investigation_and_trace_ids_are_atomic(
    tmp_path, investigation_id, trace_id
) -> None:
    with pytest.raises(ValueError, match="must be supplied together"):
        run_result(
            tmp_path,
            investigation_id=investigation_id,
            trace_id=trace_id,
        )


def test_parent_owned_source_is_exclusive_and_must_match_media(tmp_path) -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        run_result(tmp_path, license_basis="CC-BY-4.0")

    forged_hash = replace(PARENT_SOURCE, content_sha256="b" * 64)
    with pytest.raises(ValueError, match="does not match the inspected media"):
        run_result(tmp_path, source=forged_hash)


@pytest.mark.parametrize(
    ("path", "field"),
    [
        ((), "raw_model_secret"),
        (("investigation",), "api_key"),
        (("evidence", 0), "unexpected_prompt"),
        (("belief", "candidates", 0), "provider_payload"),
        (("steps", 0), "shell_command"),
        (("trace", 0), "pickle"),
    ],
)
def test_decoder_rejects_hostile_extra_structured_fields(tmp_path, path, field) -> None:
    trusted = run_result(tmp_path)
    payload = local_run_result_to_primitive(trusted)
    target = payload
    for part in path:
        target = target[part]
    target[field] = "must-not-be-accepted"

    with pytest.raises(ValueError, match="unexpected fields"):
        local_run_result_from_primitive(payload, expected=parent_expectation())


def test_decoder_allows_json_extension_payload_but_rejects_non_finite_number(tmp_path) -> None:
    trusted = run_result(tmp_path)
    expected = parent_expectation()
    payload = local_run_result_to_primitive(trusted)
    payload["evidence"][0]["attributes"] = {"safeExtension": {"score": 0.5}}
    recorded = next(
        item
        for item in payload["trace"]
        if item["event_type"] == "evidence.recorded"
        and item["payload"]["evidence"]["evidence_id"] == payload["evidence"][0]["evidence_id"]
    )
    recorded["payload"]["evidence"]["attributes"] = {"safeExtension": {"score": 0.5}}
    parsed = local_run_result_from_primitive(payload, expected=expected)
    assert parsed.evidence[0].attributes["safeExtension"] == {"score": 0.5}

    hostile = copy.deepcopy(payload)
    hostile["trace"][0]["payload"]["nan"] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        local_run_result_from_primitive(hostile, expected=expected)


def test_decoder_rejects_cross_record_reference_tampering(tmp_path) -> None:
    trusted = run_result(tmp_path)
    payload = local_run_result_to_primitive(trusted)
    payload["belief"]["evidence_ids"].append("unknown-evidence")

    with pytest.raises(ValueError, match="unknown evidence"):
        local_run_result_from_primitive(payload, expected=parent_expectation())


def test_decoder_rejects_non_object_root(tmp_path) -> None:
    expected = parent_expectation()
    with pytest.raises(ValueError, match="must be an object"):
        local_run_result_from_primitive([], expected=expected)  # type: ignore[arg-type]


def test_decoder_rejects_empty_or_reference_forged_trace(tmp_path) -> None:
    trusted = run_result(tmp_path)
    expected = parent_expectation()
    empty = local_run_result_to_primitive(trusted)
    empty["trace"] = []
    with pytest.raises(ValueError, match="trace must not be empty"):
        local_run_result_from_primitive(empty, expected=expected)

    forged = local_run_result_to_primitive(trusted)
    trace_event(forged, "step.started")["payload"]["stepId"] = "unknown-step"
    with pytest.raises(ValueError, match="payload is inconsistent"):
        local_run_result_from_primitive(forged, expected=expected)


def test_decoder_requires_the_final_hypothesis_to_be_top_ranked(tmp_path) -> None:
    trusted = run_result(tmp_path)
    payload = local_run_result_to_primitive(trusted)
    runner_up = payload["belief"]["candidates"][1]
    payload["investigation"]["final_hypothesis_id"] = runner_up["candidate_id"]
    payload["investigation"]["confidence"] = runner_up["probability"]

    with pytest.raises(ValueError, match="top-ranked"):
        local_run_result_from_primitive(payload, expected=parent_expectation())


def test_decoder_recomputes_entropy_and_bound_posterior(tmp_path) -> None:
    trusted = run_result(tmp_path)
    expected = parent_expectation()
    malformed_entropy = local_run_result_to_primitive(trusted)
    malformed_entropy["belief"]["entropy"] += 0.1
    with pytest.raises(ValueError, match="belief.entropy.*inconsistent"):
        local_run_result_from_primitive(malformed_entropy, expected=expected)

    forged = local_run_result_to_primitive(trusted)
    probabilities = (0.9, 0.1)
    for candidate, probability in zip(forged["belief"]["candidates"], probabilities, strict=True):
        candidate["probability"] = probability
    forged_entropy = -sum(value * math.log(value) for value in probabilities)
    forged["belief"]["entropy"] = forged_entropy
    forged["belief"]["normalized_entropy"] = forged_entropy / math.log(2)

    with pytest.raises(ValueError, match="caller-bound candidate priors"):
        local_run_result_from_primitive(forged, expected=expected)


def test_decoder_enforces_post_parse_string_and_collection_limits(tmp_path) -> None:
    trusted = run_result(tmp_path)
    expected = parent_expectation()
    oversized_string = local_run_result_to_primitive(trusted)
    oversized_string["source"]["publisher"] = "x" * 131_073
    with pytest.raises(ValueError, match="maximum string length"):
        local_run_result_from_primitive(oversized_string, expected=expected)

    oversized_collection = local_run_result_to_primitive(trusted)
    trace_event(oversized_collection, "investigation.started")["payload"]["items"] = [0] * 4_097
    with pytest.raises(ValueError, match="maximum collection size"):
        local_run_result_from_primitive(oversized_collection, expected=expected)


def test_public_decoder_requires_parent_owned_expectation(tmp_path) -> None:
    payload = local_run_result_to_primitive(run_result(tmp_path))

    with pytest.raises(TypeError, match="required keyword-only argument: 'expected'"):
        local_run_result_from_primitive(payload)  # type: ignore[call-arg]


def test_parent_expectation_rejects_self_consistent_identity_tampering(tmp_path) -> None:
    trusted = run_result(tmp_path)
    expected = parent_expectation()
    original = local_run_result_to_primitive(trusted)

    forged_source = copy.deepcopy(original)
    forged_source["source"]["source_id"] = "forged-source"
    forged_source["investigation"]["source_id"] = "forged-source"
    for item in forged_source["evidence"]:
        item["source_id"] = "forged-source"
    trace_event(forged_source, "investigation.started")["payload"]["sourceId"] = "forged-source"
    for event in forged_source["trace"]:
        if event["event_type"] == "evidence.recorded":
            event["payload"]["evidence"]["source_id"] = "forged-source"
    with pytest.raises(ValueError, match="parent expectation: source"):
        local_run_result_from_primitive(forged_source, expected=expected)

    forged_hash = copy.deepcopy(original)
    forged_hash["source"]["content_sha256"] = "b" * 64
    forged_hash["media"]["content_sha256"] = "b" * 64
    forged_hash["steps"][0]["attributes"]["media"]["content_sha256"] = "b" * 64
    trace_event(forged_hash, "media.inspected")["payload"]["media"]["content_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="parent expectation: source"):
        local_run_result_from_primitive(forged_hash, expected=expected)

    forged_kind = copy.deepcopy(original)
    forged_kind["investigation"]["kind"] = "damageChange"
    trace_event(forged_kind, "investigation.started")["payload"]["kind"] = "damageChange"
    with pytest.raises(ValueError, match="parent expectation: kind"):
        local_run_result_from_primitive(forged_kind, expected=expected)

    forged_investigation = copy.deepcopy(original)
    forged_investigation["investigation"]["investigation_id"] = "forged-investigation"
    trace_event(forged_investigation, "investigation.started")["payload"]["investigationId"] = (
        "forged-investigation"
    )
    trace_event(forged_investigation, "investigation.needsReview")["payload"]["investigationId"] = (
        "forged-investigation"
    )
    with pytest.raises(ValueError, match="parent expectation: investigation_id"):
        local_run_result_from_primitive(forged_investigation, expected=expected)

    forged_trace = copy.deepcopy(original)
    forged_trace["investigation"]["trace_id"] = "forged-trace"
    for event in forged_trace["trace"]:
        event["trace_id"] = "forged-trace"
    with pytest.raises(ValueError, match="parent expectation: trace_id"):
        local_run_result_from_primitive(forged_trace, expected=expected)

    forged_model = copy.deepcopy(original)
    model = {
        "name": "forged-model",
        "version": "forged-v1",
        "digest": "forged-digest",
        "runtime": "forged-runtime",
        "prompt_digest": None,
    }
    forged_model["investigation"]["model_provenance"] = [model]
    observe_step = forged_model["steps"][1]
    observe_step["model_digest"] = model["digest"]
    observe_step["prompt_digest"] = model["prompt_digest"]
    observe_step["tool_version"] = model["version"]
    trace_event(forged_model, "step.completed")["payload"]["step"] = copy.deepcopy(observe_step)
    with pytest.raises(ValueError, match="parent expectation: model_provenance"):
        local_run_result_from_primitive(forged_model, expected=expected)

    forged_policy = copy.deepcopy(original)
    forged_policy["investigation"]["connectivity_policy"] = "connected"
    trace_event(forged_policy, "investigation.started")["payload"]["connectivityPolicy"] = (
        "connected"
    )
    with pytest.raises(ValueError, match="must use the local connectivity policy"):
        local_run_result_from_primitive(forged_policy, expected=expected)


@pytest.mark.parametrize(
    ("field", "forged_value"),
    [
        ("license_basis", "forged-commercial-license"),
        ("publisher", "Forged Publisher"),
        ("source_url", "https://attacker.example/forged"),
        ("collected_at", "2030-01-01T00:00:00Z"),
        ("published_at", "2030-01-01T00:00:00Z"),
        ("consent_basis", "forgedConsent"),
        ("redistribution_policy", "unrestricted"),
        ("retention_policy", "retainForever"),
    ],
)
def test_parent_expectation_rejects_legal_provenance_tampering(
    tmp_path, field, forged_value
) -> None:
    trusted = run_result(tmp_path)
    forged = local_run_result_to_primitive(trusted)
    forged["source"][field] = forged_value

    with pytest.raises(ValueError, match="parent expectation: source"):
        local_run_result_from_primitive(forged, expected=parent_expectation())
