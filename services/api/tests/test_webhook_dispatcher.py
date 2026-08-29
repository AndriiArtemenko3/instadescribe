"""PostgreSQL proofs for terminal webhook fanout and at-least-once attempts."""

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from app.domain.states import JobState
from app.models import (
    Job,
    JobEvent,
    Organization,
    Principal,
    Project,
    Render,
    RenderAttemptArtifact,
    Review,
    TtsPreview,
    TtsPreviewArtifact,
    WebhookDelivery,
    WebhookEndpoint,
)
from app.services.webhook_dispatcher import (
    claim_due_delivery,
    collect_operational_metrics,
    complete_delivery,
    dispatch_one,
    materialize_public_deliveries,
    render_backlog_count,
)
from sqlalchemy.orm import Session

pytestmark = pytest.mark.skipif(
    not os.environ.get("INSTADESCRIBE_TEST_DATABASE_URL"),
    reason="INSTADESCRIBE_TEST_DATABASE_URL not set (use `make cloud-test` or CI)",
)


def _job(session: Session) -> tuple[Organization, Job]:
    organization = Organization(
        slug=f"hook-{uuid.uuid4().hex[:12]}",
        name="Webhook beta",
    )
    session.add(organization)
    session.flush()
    project = Project(organization_id=organization.id, name="Webhook source")
    session.add(project)
    session.flush()
    job = Job(
        organization_id=organization.id,
        project_id=project.id,
        pipeline_revision="test",
        status=JobState.READY_FOR_REVIEW.value,
        settings={},
    )
    session.add(job)
    session.flush()
    return organization, job


def _endpoint(session: Session, organization: Organization) -> WebhookEndpoint:
    endpoint = WebhookEndpoint(
        organization_id=organization.id,
        endpoint_url="https://hooks.customer.test/instadescribe",
        signing_secret_ref="arn:aws:secretsmanager:eu-west-2:123:secret:hook",
    )
    session.add(endpoint)
    session.flush()
    return endpoint


def _event(session: Session, job: Job, event_type: str) -> JobEvent:
    instant = datetime.now(UTC)
    event_id = uuid.uuid4()
    event = JobEvent(
        id=event_id,
        organization_id=job.organization_id,
        job_id=job.id,
        event_type=event_type,
        job_version=job.version,
        payload={
            "id": str(event_id),
            "type": event_type,
            "jobId": str(job.id),
            "state": "needs_review",
            "occurredAt": instant.isoformat().replace("+00:00", "Z"),
        },
        occurred_at=instant,
        available_at=instant,
    )
    session.add(event)
    session.flush()
    return event


def test_fanout_uses_exact_public_allowlist(db_engine):
    with Session(db_engine) as session:
        organization, first_job = _job(session)
        endpoint = _endpoint(session, organization)
        public = _event(session, first_job, "job.needs_review")
        project = Project(organization_id=organization.id, name="Internal intent")
        session.add(project)
        session.flush()
        second_job = Job(
            organization_id=organization.id,
            project_id=project.id,
            pipeline_revision="test",
            status=JobState.EXPORT_QUEUED.value,
            settings={},
        )
        session.add(second_job)
        session.flush()
        internal = _event(session, second_job, "render.requested")
        session.commit()

        assert materialize_public_deliveries(session) == 1
        deliveries = list(session.scalars(sa.select(WebhookDelivery)))
        assert len(deliveries) == 1
        assert deliveries[0].event_id == public.id
        assert deliveries[0].endpoint_id == endpoint.id
        session.refresh(public)
        session.refresh(internal)
        assert public.dispatched_at is not None
        assert internal.dispatched_at is None


def test_render_backlog_counts_all_media_work_and_orphan_cleanup(db_engine):
    with Session(db_engine) as session:
        organization, _ = _job(session)
        instant = datetime.now(UTC)
        for state in ("queued", "rendering", "completed", "failed", "cancelled"):
            project = Project(organization_id=organization.id, name=f"Render {state}")
            session.add(project)
            session.flush()
            render_job = Job(
                organization_id=organization.id,
                project_id=project.id,
                pipeline_revision="test",
                status=(
                    JobState.EXPORT_QUEUED.value
                    if state == "queued"
                    else JobState.EXPORTING.value
                    if state == "rendering"
                    else JobState.COMPLETED.value
                    if state == "completed"
                    else JobState.FAILED.value
                    if state == "failed"
                    else JobState.CANCELLED.value
                ),
                settings={},
            )
            session.add(render_job)
            session.flush()
            review = Review(
                organization_id=organization.id,
                job_id=render_job.id,
            )
            session.add(review)
            session.flush()
            render = Render(
                organization_id=organization.id,
                job_id=render_job.id,
                review_id=review.id,
                state=state,
                started_at=instant if state == "rendering" else None,
                completed_at=(instant if state in {"completed", "failed", "cancelled"} else None),
            )
            session.add(render)
            session.flush()
            if state == "failed":
                session.add(
                    RenderAttemptArtifact(
                        organization_id=organization.id,
                        job_id=render_job.id,
                        render_id=render.id,
                        fence_token=render.fence_token,
                        format="mp4",
                        object_key=(
                            f"deliverables/orgs/{organization.id}/jobs/{render_job.id}/"
                            f"attempts/{render.fence_token}/described_video.mp4"
                        ),
                        version_id="orphan-version",
                    )
                )
        principal = Principal(kind="human", display_name="Preview reviewer")
        session.add(principal)
        session.flush()
        preview_job = session.scalar(
            sa.select(Job).where(Job.organization_id == organization.id).order_by(Job.created_at)
        )
        assert preview_job is not None
        queued_preview = TtsPreview(
            organization_id=organization.id,
            job_id=preview_job.id,
            requested_by_principal_id=principal.id,
            scene_id="scene_1",
            text="A person enters.",
            voice="onyx",
            speed=1,
            request_hash="1" * 64,
        )
        rendering_preview = TtsPreview(
            organization_id=organization.id,
            job_id=preview_job.id,
            requested_by_principal_id=principal.id,
            scene_id="scene_2",
            text="A door closes.",
            voice="nova",
            speed=1,
            request_hash="2" * 64,
            state="rendering",
            fence_token=1,
            attempt_count=1,
            worker_id="worker:preview",
            lease_expires_at=instant + timedelta(minutes=2),
            started_at=instant,
        )
        failed_preview = TtsPreview(
            organization_id=organization.id,
            job_id=preview_job.id,
            requested_by_principal_id=principal.id,
            scene_id="scene_3",
            text="The screen fades.",
            voice="alloy",
            speed=1,
            request_hash="3" * 64,
            state="failed",
            fence_token=1,
            attempt_count=1,
            error_code="preview_generation_failed",
            error_message="The TTS preview could not be generated.",
            started_at=instant,
            finished_at=instant,
        )
        expired_failed_preview = TtsPreview(
            organization_id=organization.id,
            job_id=preview_job.id,
            requested_by_principal_id=principal.id,
            scene_id="scene_4",
            text="A preview expired.",
            voice="echo",
            speed=1,
            request_hash="4" * 64,
            state="failed",
            fence_token=1,
            attempt_count=1,
            error_code="preview_generation_failed",
            error_message="The TTS preview could not be generated.",
            created_at=instant - timedelta(days=2),
            started_at=instant - timedelta(days=2),
            finished_at=instant - timedelta(days=2),
            expires_at=instant - timedelta(days=1),
        )
        session.add_all((queued_preview, rendering_preview, failed_preview, expired_failed_preview))
        session.flush()
        session.add(
            TtsPreviewArtifact(
                organization_id=organization.id,
                job_id=preview_job.id,
                preview_id=failed_preview.id,
                fence_token=1,
                object_key=(
                    f"previews/orgs/{organization.id}/jobs/{preview_job.id}/requests/"
                    f"{failed_preview.id}/attempts/1/narration.mp3"
                ),
                version_id="orphan-preview-version",
            )
        )
        session.commit()

        # Two render states + one render cleanup + two preview states + one
        # preview cleanup + one due terminal-preview expiry. A terminal row
        # with an orphan journal is counted only by the cleanup selector.
        assert render_backlog_count(session) == 7


def test_operational_metrics_are_exact_sanitized_aggregates(db_engine):
    now = datetime.now(UTC)
    window_start = now - timedelta(minutes=1)
    with Session(db_engine) as session:
        outbox_organization, outbox_job = _job(session)
        endpoint = _endpoint(session, outbox_organization)
        oldest_public = _event(session, outbox_job, "job.needs_review")
        oldest_public.occurred_at = now - timedelta(seconds=125)
        oldest_public.available_at = now - timedelta(seconds=125)

        delivery_project = Project(
            organization_id=outbox_organization.id,
            name="Exhausted delivery",
        )
        session.add(delivery_project)
        session.flush()
        delivery_job = Job(
            organization_id=outbox_organization.id,
            project_id=delivery_project.id,
            pipeline_revision="test",
            status=JobState.FAILED.value,
            settings={},
            completed_at=now,
        )
        session.add(delivery_job)
        session.flush()
        exhausted_event = _event(session, delivery_job, "job.failed")
        exhausted_event.occurred_at = now - timedelta(seconds=1)
        exhausted_event.available_at = now - timedelta(seconds=1)
        exhausted_event.dispatched_at = now
        session.add(
            WebhookDelivery(
                organization_id=outbox_organization.id,
                endpoint_id=endpoint.id,
                event_id=exhausted_event.id,
                state="exhausted",
                next_attempt_at=now,
                attempt_count=8,
                last_error_code="delivery_exhausted",
            )
        )

        # An older event without an active endpoint is not actionable outbox
        # backlog and must not make the aggregate tenant-dependent.
        no_endpoint_organization, no_endpoint_job = _job(session)
        no_endpoint_event = _event(session, no_endpoint_job, "job.cancelled")
        no_endpoint_event.occurred_at = now - timedelta(minutes=20)
        no_endpoint_event.available_at = now - timedelta(minutes=20)

        for lease in (None, now - timedelta(seconds=1), now + timedelta(minutes=1)):
            _, processing_job = _job(session)
            processing_job.status = JobState.PROCESSING.value
            processing_job.worker_id = f"worker:{uuid.uuid4()}"
            processing_job.lease_expires_at = lease
            processing_job.started_at = now - timedelta(minutes=2)

        for completed_at, error_code in (
            (now - timedelta(seconds=30), "quota_exceeded"),
            (window_start, "quota_exceeded"),
            (now - timedelta(seconds=10), "invalid_media"),
        ):
            _, failed_job = _job(session)
            failed_job.status = JobState.FAILED.value
            failed_job.error_code = error_code
            failed_job.completed_at = completed_at
        session.commit()

        metrics = collect_operational_metrics(
            session,
            now=now,
            quota_window_start=window_start,
        )

        assert metrics.render_backlog == 0
        assert metrics.outbox_oldest_seconds == pytest.approx(125.0)
        assert metrics.webhook_delivery_exhausted == 1
        assert metrics.expired_processing_leases == 2
        # The lower boundary is exclusive, so adjacent heartbeat windows do
        # not double-count a durable quota rejection.
        assert metrics.quota_rejected == 1


def test_retry_then_success_retains_event_and_increments_attempt(db_engine):
    with Session(db_engine) as session:
        organization, job = _job(session)
        _endpoint(session, organization)
        event = _event(session, job, "job.needs_review")
        session.commit()
        now = datetime.now(UTC)
        assert materialize_public_deliveries(session, now=now) == 1

        first = claim_due_delivery(session, now=now)
        assert first is not None
        assert first.event_id == event.id
        assert first.attempt == 1
        retried = complete_delivery(
            session,
            first,
            status_code=503,
            error_code="http_error",
            now=now,
            random_value=1,
        )
        assert retried.action == "retry"
        assert claim_due_delivery(session, now=now + timedelta(seconds=29)) is None

        second = claim_due_delivery(session, now=now + timedelta(seconds=31))
        assert second is not None
        assert second.event_id == event.id
        assert second.attempt == 2
        delivered = complete_delivery(
            session,
            second,
            status_code=204,
            now=now + timedelta(seconds=31),
        )
        assert delivered.action == "success"
        row = session.get(WebhookDelivery, second.delivery_id)
        assert row is not None
        assert row.state == "succeeded"
        assert row.attempt_count == 2
        assert row.delivered_at is not None


def test_gone_response_disables_endpoint(db_engine):
    with Session(db_engine) as session:
        organization, job = _job(session)
        endpoint = _endpoint(session, organization)
        _event(session, job, "job.failed")
        session.commit()
        now = datetime.now(UTC)
        materialize_public_deliveries(session, now=now)
        claim = claim_due_delivery(session, now=now)
        assert claim is not None
        result = complete_delivery(
            session,
            claim,
            status_code=410,
            error_code="http_error",
            now=now,
        )
        assert result.action == "disable"
        session.refresh(endpoint)
        assert endpoint.is_active is False
        assert endpoint.disabled_at == now


def test_dispatch_fails_closed_before_secret_or_network_for_unapproved_host(db_engine):
    with Session(db_engine) as session:
        organization, job = _job(session)
        endpoint = _endpoint(session, organization)
        _event(session, job, "job.cancelled")
        session.commit()
        now = datetime.now(UTC)
        materialize_public_deliveries(session, now=now)

        def forbidden_secret(_reference: str) -> bytes:
            raise AssertionError("secret resolution must not run for an unsafe endpoint")

        result = dispatch_one(
            session,
            allowed_hosts=(),
            secret_resolver=forbidden_secret,
            sender=lambda *_args: 200,
            now=now,
        )
        assert result is not None
        assert result.action == "disable"
        session.refresh(endpoint)
        assert endpoint.is_active is False
