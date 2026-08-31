"""PostgreSQL contract tests for fenced investigation publication."""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from app.core.tenancy import PORTFOLIO_ORGANIZATION_ID
from app.domain.states import JobState
from app.models import (
    BeliefSnapshot,
    EvidenceItem,
    Investigation,
    InvestigationStep,
    Job,
    JobEvent,
    Project,
    SourceRecord,
    WebhookDelivery,
    WebhookEndpoint,
)
from app.repositories.investigations import InvestigationRow
from app.services.investigations import belief_body, detail_body
from app.services.webhook_dispatcher import materialize_public_deliveries
from instadescribe_investigation_core import InvestigationKind
from instadescribe_worker.claim import claim_job, guarded_transition
from instadescribe_worker.investigation import (
    finalize_investigation,
    mark_investigation_stage,
    parent_source_record,
    parent_trace_id,
)
from instadescribe_worker.investigation_runtime import run_local_observation

pytestmark = pytest.mark.skipif(
    not os.environ.get("INSTADESCRIBE_TEST_DATABASE_URL"),
    reason="INSTADESCRIBE_TEST_DATABASE_URL not set (use `make g5-test`)",
)


def _seed_investigation(db_session, *, worker_id: str = "investigation-fence"):
    project = Project(
        organization_id=PORTFOLIO_ORGANIZATION_ID,
        name="Local investigation",
    )
    db_session.add(project)
    db_session.flush()
    investigation_id = uuid.uuid4()
    job = Job(
        organization_id=PORTFOLIO_ORGANIZATION_ID,
        project_id=project.id,
        workflow_kind="video_investigation",
        pipeline_revision="test",
        status=JobState.PROCESSING.value,
        stage="investigating",
        settings={
            "workflow_kind": "video_investigation",
            "investigation_id": str(investigation_id),
            "investigation_kind": "geolocate_provenance",
            "connectivity_policy": "local",
        },
        provider="local",
        model=None,
        max_attempts=3,
        worker_id=worker_id,
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    db_session.add(job)
    db_session.flush()
    investigation = Investigation(
        id=investigation_id,
        organization_id=PORTFOLIO_ORGANIZATION_ID,
        job_id=job.id,
        kind="geolocate_provenance",
        connectivity_policy="local",
        status="investigating",
        model_provenance={"executedLocally": True},
        runtime_provenance={"runtime": "fixture", "platform": "localWorker"},
    )
    db_session.add(investigation)
    db_session.flush()
    source = SourceRecord(
        organization_id=PORTFOLIO_ORGANIZATION_ID,
        job_id=job.id,
        investigation_id=investigation.id,
        publisher_url="https://publisher.example.test/rights-cleared-fixture",
        published_at=datetime(2026, 8, 30, 10, 0, 0, 654321, tzinfo=UTC),
        collected_at=datetime(2026, 8, 30, 12, 0, 0, 123456, tzinfo=UTC),
        legal_basis="licensed",
        license_name="Test fixture licence",
        redistribution_policy="metadata_only",
        retention_days=30,
        purge_after=datetime(2026, 9, 29, 12, 0, 0, 123456, tzinfo=UTC),
    )
    db_session.add(source)
    db_session.commit()
    return project, job, investigation, source


def test_expired_investigation_claim_atomically_resets_stage_for_reclaimer(db_session):
    project = Project(
        organization_id=PORTFOLIO_ORGANIZATION_ID,
        name="Reclaimable investigation",
    )
    db_session.add(project)
    db_session.flush()
    investigation_id = uuid.uuid4()
    message_id = uuid.uuid4()
    requested_at = datetime.now(UTC).replace(microsecond=0)
    job = Job(
        organization_id=PORTFOLIO_ORGANIZATION_ID,
        project_id=project.id,
        workflow_kind="video_investigation",
        pipeline_revision="test",
        status=JobState.QUEUED.value,
        settings={
            "workflow_kind": "video_investigation",
            "investigation_id": str(investigation_id),
            "investigation_kind": "geolocate_provenance",
            "connectivity_policy": "local",
        },
        provider="local",
        max_attempts=3,
        enqueue_message_id=message_id,
        enqueue_requested_at=requested_at,
    )
    db_session.add(job)
    db_session.flush()
    db_session.add(
        Investigation(
            id=investigation_id,
            organization_id=PORTFOLIO_ORGANIZATION_ID,
            job_id=job.id,
            kind="geolocate_provenance",
            connectivity_policy="local",
            status="queued",
            model_provenance={"executedLocally": True},
            runtime_provenance={},
        )
    )
    db_session.commit()

    expected_trace_id = parent_trace_id(investigation_id)

    first = claim_job(
        db_session,
        job.id,
        message_id,
        requested_at,
        configured_max_attempts=3,
        provider="local",
    )
    assert first is not None
    assert parent_trace_id(investigation_id) == expected_trace_id
    first_token = first.worker_id
    assert mark_investigation_stage(db_session, first, first_token, "preprocessing")
    assert mark_investigation_stage(db_session, first, first_token, "investigating")
    db_session.execute(
        sa.update(Job)
        .where(Job.id == job.id)
        .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    db_session.commit()

    reclaimed = claim_job(
        db_session,
        job.id,
        message_id,
        requested_at,
        configured_max_attempts=3,
        provider="local",
    )

    assert reclaimed is not None
    assert parent_trace_id(investigation_id) == expected_trace_id
    assert reclaimed.worker_id != first_token
    assert db_session.get(Investigation, investigation_id).status == "queued"
    assert mark_investigation_stage(
        db_session,
        reclaimed,
        reclaimed.worker_id,
        "preprocessing",
    )


def test_fenced_fixture_publication_matches_strict_browser_projection(db_session, tmp_path):
    project, job, investigation, source = _seed_investigation(db_session)
    media = tmp_path / "source.mp4"
    media.write_bytes(b"rights-cleared-investigation-fixture")
    source_sha256 = hashlib.sha256(media.read_bytes()).hexdigest()
    durable_source = parent_source_record(source, content_sha256=source_sha256)
    trace_id = parent_trace_id(investigation.id)
    assert durable_source.collected_at == datetime(2026, 8, 30, 12, 0, 0, 123000, tzinfo=UTC)
    assert durable_source.published_at == datetime(2026, 8, 30, 10, 0, 0, 654000, tzinfo=UTC)
    assert durable_source.license_basis == "licensed:Test fixture licence"
    assert durable_source.source_url == "https://publisher.example.test/rights-cleared-fixture"
    assert durable_source.redistribution_policy == "metadata_only"
    assert durable_source.retention_policy == ("retentionDays=30;purgeAfter=2026-09-29T12:00:00Z")
    result = run_local_observation(
        media,
        tmp_path,
        source=durable_source,
        duration_seconds=30,
        settings=SimpleNamespace(
            investigation_runtime="fixture",
            investigation_test_fixture_enabled=True,
            investigation_test_fixture_scenario="supportive",
        ),
        investigation_id=str(investigation.id),
        trace_id=str(trace_id),
        kind=InvestigationKind.GEOLOCATE_PROVENANCE,
    )

    assert not finalize_investigation(
        db_session,
        job,
        "stale-fence",
        result,
        source_sha256=source_sha256,
        runtime_provenance={"runtime": "fixture", "platform": "localWorker"},
    )
    assert db_session.scalar(sa.select(sa.func.count(EvidenceItem.id))) == 0

    db_session.expire_all()
    job = db_session.get(Job, job.id)
    assert finalize_investigation(
        db_session,
        job,
        "investigation-fence",
        result,
        source_sha256=source_sha256,
        runtime_provenance={"runtime": "fixture", "platform": "localWorker"},
    )
    db_session.expire_all()

    stored_job = db_session.get(Job, job.id)
    stored_investigation = db_session.get(Investigation, investigation.id)
    stored_source = db_session.get(SourceRecord, source.id)
    evidence = db_session.scalars(
        sa.select(EvidenceItem).order_by(EvidenceItem.created_at, EvidenceItem.id)
    ).all()
    belief = db_session.scalar(sa.select(BeliefSnapshot))
    steps = db_session.scalars(
        sa.select(InvestigationStep).order_by(InvestigationStep.sequence)
    ).all()
    event = db_session.scalar(sa.select(JobEvent).where(JobEvent.job_id == job.id))

    assert stored_job.status == JobState.READY_FOR_REVIEW.value
    assert stored_investigation.status == "needs_review"
    assert stored_investigation.trace_id == trace_id
    assert result.investigation.trace_id == str(trace_id)
    assert stored_source.media_sha256 == source_sha256
    assert sum(item.kind == "keyframe" for item in evidence) == 2
    assert len(steps) == 3
    assert event.event_type == "job.needs_review"
    assert event.payload["workflowKind"] == "videoInvestigation"

    # The pivot is Browser-API-only this month. Reusing the durable JobEvent
    # table must not widen the stable Integration webhook contract.
    db_session.add(
        WebhookEndpoint(
            organization_id=PORTFOLIO_ORGANIZATION_ID,
            endpoint_url="https://hooks.example.test/investigations",
            signing_secret_ref="test://investigation-webhook-boundary",
        )
    )
    db_session.commit()
    assert materialize_public_deliveries(db_session) == 0
    assert db_session.scalar(sa.select(sa.func.count(WebhookDelivery.id))) == 0

    detail = detail_body(InvestigationRow(stored_investigation, stored_job, project))
    public_belief = belief_body(belief)
    assert detail["traceId"] == str(trace_id)
    assert detail["modelProvenance"] == {
        "modelId": "deterministic-fixture",
        "modelDigest": hashlib.sha256(b"deterministic-fixture-v1").hexdigest(),
        "promptDigest": hashlib.sha256(b"fixture-observation-v1").hexdigest(),
        "executedLocally": True,
    }
    assert detail["runtimeProvenance"] == {
        "runtime": "fixture",
        "runtimeVersion": "1",
        "platform": "localWorker",
    }
    assert all(
        set(candidate) == {"id", "label", "probability"}
        for candidate in public_belief["candidates"]
    )
    assert sum(
        candidate["probability"] for candidate in public_belief["candidates"]
    ) == pytest.approx(1)


def test_investigation_failure_mirrors_in_same_fenced_transaction(db_session):
    _, job, investigation, _ = _seed_investigation(db_session)

    assert not guarded_transition(
        db_session,
        job.id,
        "stale-fence",
        JobState.FAILED,
        error_code="invalid_settings",
        completed_at=sa.func.now(),
        worker_id=None,
    )
    db_session.expire_all()
    assert db_session.get(Investigation, investigation.id).status == "investigating"

    assert guarded_transition(
        db_session,
        job.id,
        "investigation-fence",
        JobState.FAILED,
        error_code="invalid_settings",
        completed_at=sa.func.now(),
        worker_id=None,
    )
    db_session.expire_all()
    assert db_session.get(Job, job.id).status == JobState.FAILED.value
    assert db_session.get(Investigation, investigation.id).status == "failed"
    assert (
        db_session.scalar(sa.select(JobEvent.event_type).where(JobEvent.job_id == job.id))
        == "job.failed"
    )
