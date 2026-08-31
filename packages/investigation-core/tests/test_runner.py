from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest
from conftest import FIXED_TIME, evidence_item

from instadescribe_investigation_core import (
    BeliefConfig,
    CandidatePrior,
    ConnectivityPolicy,
    DeterministicLocalRunner,
    InvestigationKind,
    InvestigationStatus,
    MediaMetadata,
    ModelProvenance,
    SourceRecord,
    StaticObservationAdapter,
    TraceEventType,
    canonical_json,
    read_trace_jsonl,
)


def test_local_runner_reaches_review_and_exports_replayable_trace(tmp_path) -> None:
    media_path = tmp_path / "fixture.mp4"
    media_path.write_bytes(b"licensed fixture")
    digest = hashlib.sha256(b"licensed fixture").hexdigest()

    def inspect(_path):
        return MediaMetadata(
            path=str(media_path),
            content_sha256=digest,
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
        candidates=(
            CandidatePrior("pl", "Poland", 0.5),
            CandidatePrior("sk", "Slovakia", 0.5),
        ),
        media_inspector=inspect,
        clock=lambda: FIXED_TIME,
    )

    result = runner.run(
        media_path,
        source_id="source-1",
        license_basis="CC-BY-4.0",
    )
    destination = tmp_path / "replay.jsonl"
    result.export_trace(destination)

    assert result.investigation.status is InvestigationStatus.NEEDS_REVIEW
    assert result.investigation.final_hypothesis_id == "pl"
    assert result.belief.abstained is False
    assert all(item.source_id == result.source.source_id for item in result.evidence)
    assert read_trace_jsonl(destination) == result.trace
    assert result.trace[-1].event_type is TraceEventType.INVESTIGATION_NEEDS_REVIEW
    assert result.media.path == str(media_path)
    assert str(media_path.resolve()) not in destination.read_text(encoding="utf-8")
    assert str(media_path.resolve()) not in canonical_json(result.steps)


def test_local_runner_refuses_every_egress_policy(tmp_path) -> None:
    media_path = tmp_path / "fixture.mp4"
    media_path.write_bytes(b"fixture")
    runner = DeterministicLocalRunner(
        observer=StaticObservationAdapter((evidence_item("one", group="one"),)),
        candidates=(CandidatePrior("pl", "Poland", 1),),
    )

    for policy in (
        ConnectivityPolicy.TEXT_ONLY,
        ConnectivityPolicy.APPROVED_CROPS,
        ConnectivityPolicy.CONNECTED,
    ):
        with pytest.raises(ValueError, match="only the local policy"):
            runner.run(
                media_path,
                connectivity_policy=policy,
                license_basis="testFixture",
            )


def test_local_runner_refuses_network_observer(tmp_path) -> None:
    media_path = tmp_path / "fixture.mp4"
    media_path.write_bytes(b"fixture")

    class NetworkObserver(StaticObservationAdapter):
        @property
        def network_access(self) -> bool:
            return True

    runner = DeterministicLocalRunner(
        observer=NetworkObserver((evidence_item("one", group="one"),)),
        candidates=(CandidatePrior("pl", "Poland", 1),),
    )

    with pytest.raises(ValueError, match="network-free"):
        runner.run(media_path, license_basis="testFixture")


def test_local_runner_refuses_unimplemented_investigation_kind(tmp_path) -> None:
    media_path = tmp_path / "fixture.mp4"
    media_path.write_bytes(b"fixture")
    runner = DeterministicLocalRunner(
        observer=StaticObservationAdapter((evidence_item("one", group="one"),)),
        candidates=(CandidatePrior("pl", "Poland", 1),),
    )

    with pytest.raises(ValueError, match="only the geolocateProvenance kind"):
        runner.run(
            media_path,
            kind=InvestigationKind.DAMAGE_CHANGE,
            license_basis="testFixture",
        )


def test_default_run_identity_covers_source_candidates_config_and_model(tmp_path) -> None:
    media_path = tmp_path / "fixture.mp4"
    media_path.write_bytes(b"licensed fixture")

    def run(
        *,
        publisher: str = "Publisher A",
        source_url: str = "https://publisher.example/video",
        license_basis: str = "CC-BY-4.0",
        candidates: tuple[CandidatePrior, ...] = (
            CandidatePrior("pl", "Poland", 0.5),
            CandidatePrior("sk", "Slovakia", 0.5),
        ),
        config: BeliefConfig | None = None,
        model_digest: str = "a" * 64,
    ):
        runner = DeterministicLocalRunner(
            observer=StaticObservationAdapter(
                (evidence_item("one", group="one"),),
                provenance=ModelProvenance(
                    name="fixture-observer",
                    version="1",
                    digest=model_digest,
                    runtime="local-test",
                ),
            ),
            candidates=candidates,
            belief_config=config,
            clock=lambda: FIXED_TIME,
        )
        return runner.run(
            media_path,
            publisher=publisher,
            source_url=source_url,
            license_basis=license_basis,
        )

    baseline = run()
    repeated = run()
    assert baseline.source.source_id == repeated.source.source_id
    assert baseline.investigation.investigation_id == repeated.investigation.investigation_id
    assert baseline.investigation.trace_id == repeated.investigation.trace_id

    publisher_changed = run(publisher="Publisher B")
    source_url_changed = run(source_url="https://publisher.example/other-video")
    license_changed = run(license_basis="public-domain")
    for changed in (publisher_changed, source_url_changed, license_changed):
        assert changed.source.source_id != baseline.source.source_id
        assert changed.investigation.investigation_id != baseline.investigation.investigation_id
        assert changed.investigation.trace_id != baseline.investigation.trace_id

    candidates_changed = run(
        candidates=(
            CandidatePrior("pl", "Poland", 0.6),
            CandidatePrior("sk", "Slovakia", 0.4),
        )
    )
    config_changed = run(config=BeliefConfig(minimum_confidence=0.7))
    model_changed = run(model_digest="b" * 64)
    for changed in (candidates_changed, config_changed, model_changed):
        assert changed.source.source_id == baseline.source.source_id
        assert changed.investigation.investigation_id != baseline.investigation.investigation_id
        assert changed.investigation.trace_id != baseline.investigation.trace_id


def test_parent_source_is_exact_and_fully_bound_into_default_run_identity(tmp_path) -> None:
    media_path = tmp_path / "fixture.mp4"
    media_path.write_bytes(b"licensed fixture")
    digest = hashlib.sha256(b"licensed fixture").hexdigest()
    baseline_source = SourceRecord(
        source_id="source-1",
        content_sha256=digest,
        collected_at=FIXED_TIME,
        license_basis="CC-BY-4.0",
        publisher="Publisher A",
        source_url="https://publisher.example/video",
        published_at=FIXED_TIME,
        consent_basis="writtenFixturePermission",
        redistribution_policy="redistributableFixture",
        retention_policy="deleteAfterEvaluation",
    )

    def run(source: SourceRecord):
        runner = DeterministicLocalRunner(
            observer=StaticObservationAdapter((evidence_item("one", group="one"),)),
            candidates=(CandidatePrior("pl", "Poland", 1),),
            clock=lambda: FIXED_TIME,
        )
        return runner.run(media_path, source=source)

    baseline = run(baseline_source)
    assert baseline.source is baseline_source

    changed_sources = (
        replace(baseline_source, collected_at=FIXED_TIME.replace(hour=13)),
        replace(baseline_source, license_basis="public-domain"),
        replace(baseline_source, publisher="Publisher B"),
        replace(baseline_source, source_url="https://publisher.example/other-video"),
        replace(baseline_source, published_at=None),
        replace(baseline_source, consent_basis="designPartnerAgreement"),
        replace(baseline_source, redistribution_policy="noRedistribution"),
        replace(baseline_source, retention_policy="retainFor30Days"),
    )
    for changed_source in changed_sources:
        changed = run(changed_source)
        assert changed.source is changed_source
        assert changed.investigation.investigation_id != baseline.investigation.investigation_id
        assert changed.investigation.trace_id != baseline.investigation.trace_id


@pytest.mark.parametrize(
    ("advertised", "returned"),
    (
        (
            ModelProvenance("observer", "1", "a" * 64, "local-test"),
            ModelProvenance("observer", "2", "b" * 64, "local-test"),
        ),
        (ModelProvenance("observer", "1", "a" * 64, "local-test"), None),
        (None, ModelProvenance("observer", "1", "a" * 64, "local-test")),
    ),
)
def test_local_runner_rejects_observer_provenance_drift(
    tmp_path,
    advertised,
    returned,
) -> None:
    media_path = tmp_path / "fixture.mp4"
    media_path.write_bytes(b"licensed fixture")

    class DriftingObserver(StaticObservationAdapter):
        def observe(self, media_path, *, source, media):
            batch = super().observe(media_path, source=source, media=media)
            return replace(batch, model=returned)

    runner = DeterministicLocalRunner(
        observer=DriftingObserver(
            (evidence_item("one", group="one"),),
            provenance=advertised,
        ),
        candidates=(CandidatePrior("pl", "Poland", 1),),
        clock=lambda: FIXED_TIME,
    )

    with pytest.raises(ValueError, match="provenance that differs"):
        runner.run(media_path, license_basis="CC-BY-4.0")
