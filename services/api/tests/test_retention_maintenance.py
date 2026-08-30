"""PostgreSQL proofs for bounded lifecycle expiry and retention maintenance."""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from app.api.scenes import patch_scene_for_organization
from app.core.tenancy import PORTFOLIO_ORGANIZATION_ID
from app.domain.states import JobState
from app.models import (
    Artifact,
    Asset,
    AuditEvent,
    Deliverable,
    IdempotencyRecord,
    Job,
    JobEvent,
    OrganizationJobCapacity,
    OrganizationQuota,
    OrganizationUsagePeriod,
    Project,
    QuotaReservation,
    Render,
    Review,
    TtsPreview,
    WebhookDelivery,
    WebhookEndpoint,
)
from app.schemas.scenes import SceneOverridePatch
from app.services import s3 as s3_service
from app.services.idempotency import IdempotencyError, claim, complete, request_fingerprint
from app.services.maintenance import (
    expire_awaiting_uploads,
    expire_inactive_reviews,
    purge_cancelled_uncompleted_assets,
    purge_due_assets,
    purge_due_deliverables,
    purge_due_legacy_artifacts,
    purge_expired_legacy_artifact_metadata,
    purge_expired_metadata,
    purge_terminal_jobs_and_projects,
    reap_expired_idempotency,
    run_maintenance_cycle,
    warn_reviews_nearing_expiry,
)
from app.services.quota import reserve_job_media
from conftest import requires_db
from sqlalchemy.orm import Session


def test_s3_retention_delete_requires_and_sends_exact_version(monkeypatch):
    calls: list[dict] = []
    client = SimpleNamespace(delete_object=lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(s3_service, "_internal_client", lambda: client)
    monkeypatch.setattr(
        s3_service,
        "get_settings",
        lambda: SimpleNamespace(media_bucket="private-media"),
    )

    with pytest.raises(ValueError):
        s3_service.delete_versioned_object("tenant/object.mp4", "")
    assert calls == []

    s3_service.delete_versioned_object("tenant/object.mp4", "version-7")
    assert calls == [
        {
            "Bucket": "private-media",
            "Key": "tenant/object.mp4",
            "VersionId": "version-7",
        }
    ]


def _job(
    session: Session,
    status: JobState,
    *,
    created_at: datetime | None = None,
) -> Job:
    project = Project(
        organization_id=PORTFOLIO_ORGANIZATION_ID,
        name=f"maintenance-{uuid.uuid4()}",
    )
    session.add(project)
    session.flush()
    job = Job(
        organization_id=PORTFOLIO_ORGANIZATION_ID,
        project_id=project.id,
        pipeline_revision="maintenance-test",
        status=status.value,
        settings={},
        created_at=created_at or datetime.now(UTC),
    )
    session.add(job)
    session.flush()
    return job


@requires_db
def test_upload_expiry_skips_locked_rows_and_releases_capacity_and_quota(db_engine):
    now = datetime.now(UTC)
    with Session(db_engine) as seed:
        first = _job(seed, JobState.AWAITING_UPLOAD, created_at=now - timedelta(hours=26))
        second = _job(seed, JobState.AWAITING_UPLOAD, created_at=now - timedelta(hours=25))
        recent = _job(seed, JobState.AWAITING_UPLOAD, created_at=now - timedelta(hours=23))
        reserve_job_media(seed, first, estimated_seconds=60)
        reserve_job_media(seed, second, estimated_seconds=90)
        reserve_job_media(seed, recent, estimated_seconds=30)
        for job in (first, second, recent):
            seed.add(
                Asset(
                    organization_id=job.organization_id,
                    job_id=job.id,
                    asset_type="source_video",
                    status="awaiting_upload",
                    object_key=(
                        f"uploads/orgs/{job.organization_id}/jobs/{job.id}/source/input.mp4"
                    ),
                    content_type="video/mp4",
                    size_bytes=1024,
                    purge_after=now + timedelta(days=30),
                )
            )
        seed.commit()
        first_id, second_id, recent_id = first.id, second.id, recent.id

    with Session(db_engine) as blocker:
        blocker.execute(sa.select(Job).where(Job.id == first_id).with_for_update()).scalar_one()
        with Session(db_engine) as worker:
            assert expire_awaiting_uploads(worker, now=now, batch_size=10) == 1
        blocker.rollback()

    with Session(db_engine) as worker:
        assert expire_awaiting_uploads(worker, now=now, batch_size=10) == 1

    with Session(db_engine) as session:
        jobs = {
            row.id: row
            for row in session.scalars(
                sa.select(Job).where(Job.id.in_([first_id, second_id, recent_id]))
            )
        }
        assert {jobs[first_id].status, jobs[second_id].status} == {JobState.CANCELLED.value}
        assert {jobs[first_id].error_code, jobs[second_id].error_code} == {"upload_expired"}
        assert {jobs[first_id].stage, jobs[second_id].stage} == {"upload_expired"}
        assert jobs[recent_id].status == JobState.AWAITING_UPLOAD.value
        remaining_assets = list(session.scalars(sa.select(Asset)))
        assert [(row.job_id, row.status, row.version_id) for row in remaining_assets] == [
            (recent_id, "awaiting_upload", None)
        ]
        reservations = list(
            session.scalars(
                sa.select(QuotaReservation).where(
                    QuotaReservation.job_id.in_([first_id, second_id, recent_id])
                )
            )
        )
        assert sorted(row.state for row in reservations) == ["released", "released", "reserved"]
        usage = session.scalar(sa.select(OrganizationUsagePeriod))
        assert usage.reserved_media_seconds == Decimal("30.000")
        capacity = session.get(OrganizationJobCapacity, PORTFOLIO_ORGANIZATION_ID)
        assert capacity.awaiting_upload_jobs == 1
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(JobEvent)
                .where(JobEvent.event_type == "job.cancelled")
            )
            == 2
        )
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "job.upload_expired")
            )
            == 2
        )


@requires_db
def test_cancelled_uncompleted_asset_cleanup_unblocks_terminal_metadata_without_s3_delete(
    db_engine,
):
    now = datetime.now(UTC)
    created = now - timedelta(days=2)
    with Session(db_engine) as session:
        quota = session.get(OrganizationQuota, PORTFOLIO_ORGANIZATION_ID)
        quota.metadata_retention_days = 1
        job = _job(session, JobState.CANCELLED, created_at=created)
        job.completed_at = created
        session.execute(
            sa.update(Project)
            .where(Project.id == job.project_id)
            .values(created_at=created, updated_at=created)
        )
        asset = Asset(
            organization_id=job.organization_id,
            job_id=job.id,
            asset_type="source_video",
            status="awaiting_upload",
            object_key=f"uploads/orgs/{job.organization_id}/jobs/{job.id}/source/input.mp4",
            content_type="video/mp4",
            size_bytes=1024,
            created_at=created,
            purge_after=created + timedelta(days=30),
        )
        session.add(asset)
        session.commit()
        job_id, project_id, asset_id = job.id, job.project_id, asset.id

    with Session(db_engine) as session:
        blocked = purge_terminal_jobs_and_projects(session, now=now)
        assert blocked.deleted_jobs == 0
        assert blocked.blocked_object_refs == 1

        result = run_maintenance_cycle(
            session,
            lambda *_args: pytest.fail("unversioned reservation triggered an S3 delete"),
            now=now,
            batch_size=1,
        )
        assert result.deleted_asset_metadata == 1
        assert (result.deleted_terminal_jobs, result.deleted_empty_projects) == (1, 1)
        assert session.get(Asset, asset_id) is None
        assert session.get(Job, job_id) is None
        assert session.get(Project, project_id) is None


@requires_db
def test_cancelled_uncompleted_asset_cleanup_preserves_version_pinned_source(db_engine):
    now = datetime.now(UTC)
    with Session(db_engine) as session:
        job = _job(session, JobState.CANCELLED)
        asset = Asset(
            organization_id=job.organization_id,
            job_id=job.id,
            asset_type="source_video",
            status="validated",
            object_key=f"uploads/orgs/{job.organization_id}/jobs/{job.id}/source/input.mp4",
            content_type="video/mp4",
            size_bytes=1024,
            version_id="source-version-1",
            etag="source-etag-1",
            validated_at=now,
            purge_after=now + timedelta(days=30),
        )
        session.add(asset)
        session.commit()
        asset_id = asset.id

        assert purge_cancelled_uncompleted_assets(session) == 0
        retained = session.get(Asset, asset_id)
        assert retained is not None
        assert (retained.status, retained.version_id) == ("validated", "source-version-1")


@requires_db
def test_review_warning_is_durable_once_per_activity_window_and_expiry_cancels_job(db_engine):
    now = datetime.now(UTC)
    with Session(db_engine) as session:
        job = _job(session, JobState.READY_FOR_REVIEW, created_at=now - timedelta(days=25))
        review = Review(
            organization_id=PORTFOLIO_ORGANIZATION_ID,
            job_id=job.id,
            state="open",
            created_at=now - timedelta(days=25),
            updated_at=now - timedelta(days=25),
            inactivity_expires_at=now + timedelta(days=6),
        )
        session.add(review)
        session.commit()
        job_id, review_id = job.id, review.id

    with Session(db_engine) as session:
        assert warn_reviews_nearing_expiry(session, now=now) == 1
        assert warn_reviews_nearing_expiry(session, now=now) == 0
        assert session.scalar(sa.select(sa.func.count()).select_from(JobEvent)) == 0

    extended = now + timedelta(days=1)
    with Session(db_engine) as session:
        review = session.get(Review, review_id)
        review.updated_at = extended
        review.inactivity_expires_at = extended + timedelta(days=6)
        session.commit()
        assert warn_reviews_nearing_expiry(session, now=extended) == 1

    expiry = extended + timedelta(days=7)
    with Session(db_engine) as session:
        assert expire_inactive_reviews(session, now=expiry) == 1
        review = session.get(Review, review_id)
        job = session.get(Job, job_id)
        assert (review.state, review.locked_at, review.version) == ("expired", expiry, 2)
        assert (job.status, job.stage, job.error_code) == (
            JobState.CANCELLED.value,
            "review_expired",
            "review_expired",
        )
        warning_events = list(
            session.scalars(
                sa.select(AuditEvent).where(AuditEvent.action == "review.expiry_warning")
            )
        )
        assert len(warning_events) == 2
        assert all(event.details == {"outcome": "warning"} for event in warning_events)
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(JobEvent)
                .where(JobEvent.job_id == job_id, JobEvent.event_type == "job.cancelled")
            )
            == 1
        )


@requires_db
def test_every_scene_edit_extends_open_review_deadline_atomically(db_engine):
    before = datetime.now(UTC)
    with Session(db_engine) as session:
        job = _job(session, JobState.READY_FOR_REVIEW)
        review = Review(
            organization_id=PORTFOLIO_ORGANIZATION_ID,
            job_id=job.id,
            state="open",
            inactivity_expires_at=before + timedelta(hours=1),
        )
        session.add(review)
        session.commit()
        job_id, review_id = job.id, review.id

    with Session(db_engine) as session:
        patch_scene_for_organization(
            str(job_id),
            "scene_1",
            SceneOverridePatch(ad="A concise accessible description."),
            PORTFOLIO_ORGANIZATION_ID,
            session,
        )

    after = datetime.now(UTC)
    with Session(db_engine) as session:
        review = session.get(Review, review_id)
        assert review.version == 2
        assert before + timedelta(days=30) <= review.inactivity_expires_at
        assert review.inactivity_expires_at <= after + timedelta(days=30)


@requires_db
def test_expired_idempotency_key_is_reusable_and_reaper_is_bounded(db_engine):
    path = "/api/integrations/v1/projects"
    with Session(db_engine) as session:
        original = claim(
            session,
            PORTFOLIO_ORGANIZATION_ID,
            key="reusable-after-expiry",
            method="POST",
            path=path,
            body={"name": "Old request"},
        )
        old_id = original.record.id
        complete(session, original, status=201, body={"id": "old"})
        original.record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

        replacement = claim(
            session,
            PORTFOLIO_ORGANIZATION_ID,
            key="reusable-after-expiry",
            method="POST",
            path=path,
            body={"name": "New request"},
        )
        assert not replacement.is_replay
        assert replacement.record.id != old_id
        complete(session, replacement, status=201, body={"id": "new"})
        assert session.scalar(sa.select(sa.func.count()).select_from(IdempotencyRecord)) == 1

        for index in range(3):
            session.add(
                IdempotencyRecord(
                    organization_id=PORTFOLIO_ORGANIZATION_ID,
                    key=f"expired-{index}",
                    method="POST",
                    path=path,
                    request_hash="a" * 64,
                    state="processing",
                    expires_at=datetime.now(UTC) - timedelta(minutes=1),
                )
            )
        session.commit()
        assert reap_expired_idempotency(session, batch_size=2) == 2
        assert reap_expired_idempotency(session, batch_size=2) == 1


@requires_db
def test_concurrent_expired_key_replacement_has_one_winner(db_engine):
    key = "concurrent-expired-reuse"
    path = "/api/integrations/v1/jobs"
    body = {"project": "new"}
    with Session(db_engine) as session:
        session.add(
            IdempotencyRecord(
                organization_id=PORTFOLIO_ORGANIZATION_ID,
                key=key,
                method="POST",
                path=path,
                request_hash="f" * 64,
                state="completed",
                response_status=201,
                response_body={"id": "expired"},
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        )
        session.commit()

    def compete() -> str:
        with Session(db_engine) as session:
            try:
                result = claim(
                    session,
                    PORTFOLIO_ORGANIZATION_ID,
                    key=key,
                    method="POST",
                    path=path,
                    body=body,
                )
                session.commit()
                return "winner" if not result.is_replay else "replay"
            except IdempotencyError as exc:
                session.rollback()
                return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _index: compete(), range(2)))
    assert sorted(outcomes) == ["idempotency_in_progress", "winner"]
    with Session(db_engine) as session:
        rows = list(
            session.scalars(sa.select(IdempotencyRecord).where(IdempotencyRecord.key == key))
        )
        assert len(rows) == 1
        assert rows[0].request_hash == request_fingerprint("POST", path, body)


@requires_db
def test_shortened_org_retention_exactly_purges_objects_then_metadata(db_engine):
    now = datetime.now(UTC)
    created = now - timedelta(days=2)
    with Session(db_engine) as session:
        quota = session.get(OrganizationQuota, PORTFOLIO_ORGANIZATION_ID)
        quota.source_retention_days = 1
        quota.deliverable_retention_days = 1
        quota.metadata_retention_days = 3
        job = _job(session, JobState.COMPLETED, created_at=created)
        review = Review(
            organization_id=PORTFOLIO_ORGANIZATION_ID,
            job_id=job.id,
            state="open",
            created_at=created,
            inactivity_expires_at=now + timedelta(days=1),
        )
        session.add(review)
        session.flush()
        render = Render(
            organization_id=PORTFOLIO_ORGANIZATION_ID,
            job_id=job.id,
            review_id=review.id,
            state="completed",
            completed_at=created,
            created_at=created,
        )
        session.add(render)
        session.flush()
        asset = Asset(
            organization_id=PORTFOLIO_ORGANIZATION_ID,
            job_id=job.id,
            asset_type="source_video",
            status="validated",
            object_key=(f"uploads/orgs/{PORTFOLIO_ORGANIZATION_ID}/jobs/{job.id}/source/video.mp4"),
            content_type="video/mp4",
            size_bytes=10,
            version_id="source-version",
            etag="source-etag",
            checksum_sha256="a" * 64,
            validated_at=created,
            created_at=created,
            purge_after=now + timedelta(days=20),
        )
        deliverable = Deliverable(
            organization_id=PORTFOLIO_ORGANIZATION_ID,
            job_id=job.id,
            render_id=render.id,
            format="mp4",
            state="published",
            object_key=(
                f"deliverables/orgs/{PORTFOLIO_ORGANIZATION_ID}/jobs/{job.id}/"
                "attempts/1/described_video.mp4"
            ),
            version_id="deliverable-version",
            content_type="video/mp4",
            size_bytes=20,
            checksum_sha256="b" * 64,
            created_at=created,
            published_at=created,
            purge_after=now + timedelta(days=80),
        )
        session.add_all([asset, deliverable])
        session.commit()
        asset_id, deliverable_id = asset.id, deliverable.id

    deleted: list[tuple[str, str]] = []
    with Session(db_engine) as session:
        asset_result = purge_due_assets(
            session,
            lambda key, version: deleted.append((key, version)),
            now=now,
        )
        deliverable_result = purge_due_deliverables(
            session,
            lambda key, version: deleted.append((key, version)),
            now=now,
        )
        assert (asset_result.purged, deliverable_result.purged) == (1, 1)
        asset = session.get(Asset, asset_id)
        deliverable = session.get(Deliverable, deliverable_id)
        assert (asset.status, asset.validated_at, asset.version_id) == (
            "deleted",
            None,
            "source-version",
        )
        assert (deliverable.state, deliverable.purged_at, deliverable.version_id) == (
            "purged",
            now,
            "deliverable-version",
        )
    assert {version for _key, version in deleted} == {
        "source-version",
        "deliverable-version",
    }

    with Session(db_engine) as session:
        quota = session.get(OrganizationQuota, PORTFOLIO_ORGANIZATION_ID)
        quota.metadata_retention_days = 1
        session.commit()
        counts = purge_expired_metadata(session, now=now)
        assert counts[2:] == (1, 1)
        assert session.get(Asset, asset_id) is None
        assert session.get(Deliverable, deliverable_id) is None


@requires_db
def test_metadata_purge_deletes_webhook_delivery_before_job_event(db_engine):
    now = datetime.now(UTC)
    occurred = now - timedelta(days=2)
    with Session(db_engine) as session:
        quota = session.get(OrganizationQuota, PORTFOLIO_ORGANIZATION_ID)
        quota.metadata_retention_days = 1
        job = _job(session, JobState.CANCELLED, created_at=occurred)
        endpoint = WebhookEndpoint(
            organization_id=PORTFOLIO_ORGANIZATION_ID,
            endpoint_url="https://webhook.example.invalid/events",
            signing_secret_ref="secret/ref",
        )
        event = JobEvent(
            organization_id=PORTFOLIO_ORGANIZATION_ID,
            job_id=job.id,
            event_type="job.cancelled",
            job_version=job.version,
            payload={"state": "cancelled"},
            occurred_at=occurred,
            available_at=occurred,
            purge_after=now + timedelta(days=300),
        )
        audit = AuditEvent(
            organization_id=PORTFOLIO_ORGANIZATION_ID,
            action="retention-test",
            resource_type="job",
            resource_id=str(job.id),
            details={},
            occurred_at=occurred,
            purge_after=now + timedelta(days=300),
        )
        session.add_all([endpoint, event, audit])
        session.flush()
        delivery = WebhookDelivery(
            organization_id=PORTFOLIO_ORGANIZATION_ID,
            endpoint_id=endpoint.id,
            event_id=event.id,
            state="exhausted",
            next_attempt_at=now,
        )
        session.add(delivery)
        session.commit()
        event_id, audit_id, delivery_id = event.id, audit.id, delivery.id

        assert purge_expired_metadata(session, now=now)[:2] == (1, 1)
        assert session.get(WebhookDelivery, delivery_id) is None
        assert session.get(JobEvent, event_id) is None
        assert session.get(AuditEvent, audit_id) is None


@requires_db
def test_legacy_artifact_retention_deletes_only_exact_versions_and_blocks_unsafe_job_purge(
    db_engine,
):
    now = datetime.now(UTC)
    created = now - timedelta(days=40)
    with Session(db_engine) as session:
        quota = session.get(OrganizationQuota, PORTFOLIO_ORGANIZATION_ID)
        quota.source_retention_days = 30
        quota.metadata_retention_days = 30
        job = _job(session, JobState.COMPLETED, created_at=created)
        job.completed_at = created
        job.input_object_key = f"uploads/{job.id}/source/video.mp4"
        job.input_content_type = "video/mp4"
        job.input_size_bytes = 10
        job.source_etag = "source-etag"
        job.source_version_id = "source-version"
        job.upload_verified_at = created
        session.execute(
            sa.update(Project)
            .where(Project.id == job.project_id)
            .values(created_at=created, updated_at=created)
        )
        session.add_all(
            [
                Artifact(
                    organization_id=job.organization_id,
                    job_id=job.id,
                    artifact_type="source_video",
                    object_key=job.input_object_key,
                    version_id="source-version",
                    content_type="video/mp4",
                    size_bytes=10,
                    checksum_sha256="a" * 64,
                    meta={"version_id": "source-version", "etag": "source-etag"},
                    created_at=created,
                    purge_after=created + timedelta(days=30),
                ),
                Artifact(
                    organization_id=job.organization_id,
                    job_id=job.id,
                    artifact_type="scenes_json",
                    object_key=f"jobs/{job.id}/attempts/1/analysis/scenes.json",
                    version_id="scenes-version",
                    content_type="application/json",
                    size_bytes=10,
                    checksum_sha256="b" * 64,
                    meta={"version_id": "scenes-version"},
                    created_at=created,
                    purge_after=created + timedelta(days=30),
                ),
                Artifact(
                    organization_id=job.organization_id,
                    job_id=job.id,
                    artifact_type="entities_json",
                    object_key=f"jobs/{job.id}/attempts/1/analysis/entities.json",
                    version_id=None,
                    content_type="application/json",
                    size_bytes=10,
                    checksum_sha256="c" * 64,
                    meta={},
                    created_at=created,
                    purge_after=created + timedelta(days=30),
                ),
            ]
        )
        session.commit()
        job_id, project_id = job.id, job.project_id

    deleted: list[tuple[str, str]] = []
    with Session(db_engine) as session:
        result = purge_due_legacy_artifacts(
            session,
            lambda key, version: deleted.append((key, version)),
            now=now,
        )
        assert result == type(result)(purged=2, failed=0, unsafe=1)
        stored_job = session.get(Job, job_id)
        assert stored_job.input_object_key is None
        assert stored_job.source_version_id is None
        assert stored_job.upload_verified_at is None
        assert {version for _key, version in deleted} == {
            "source-version",
            "scenes-version",
        }

        # Exact-delete tombstones may age out; the unrecoverable row remains
        # an explicit blocker rather than triggering a key-only delete.
        assert purge_expired_legacy_artifact_metadata(session, now=now) == 2
        remaining = session.scalar(sa.select(Artifact).where(Artifact.job_id == job_id))
        assert remaining is not None and remaining.version_id is None
        terminal = purge_terminal_jobs_and_projects(session, now=now)
        assert terminal.deleted_jobs == 0
        assert terminal.blocked_object_refs == 1
        assert session.get(Job, job_id) is not None
        assert session.get(Project, project_id) is not None


@requires_db
def test_terminal_job_purge_waits_for_pending_delivery_and_preview_rows(db_engine):
    now = datetime.now(UTC)
    created = now - timedelta(days=2)
    with Session(db_engine) as session:
        quota = session.get(OrganizationQuota, PORTFOLIO_ORGANIZATION_ID)
        quota.metadata_retention_days = 1
        job = _job(session, JobState.CANCELLED, created_at=created)
        job.completed_at = created
        session.execute(
            sa.update(Project)
            .where(Project.id == job.project_id)
            .values(created_at=created, updated_at=created)
        )
        endpoint = WebhookEndpoint(
            organization_id=PORTFOLIO_ORGANIZATION_ID,
            endpoint_url="https://webhook.example.invalid/retention",
            signing_secret_ref="secret/ref",
        )
        event = JobEvent(
            organization_id=PORTFOLIO_ORGANIZATION_ID,
            job_id=job.id,
            event_type="job.cancelled",
            job_version=job.version,
            payload={"state": "cancelled"},
            occurred_at=created,
            available_at=created,
            purge_after=now + timedelta(days=300),
        )
        session.add_all([endpoint, event])
        session.flush()
        delivery = WebhookDelivery(
            organization_id=PORTFOLIO_ORGANIZATION_ID,
            endpoint_id=endpoint.id,
            event_id=event.id,
            state="pending",
            next_attempt_at=now,
        )
        preview = TtsPreview(
            organization_id=PORTFOLIO_ORGANIZATION_ID,
            job_id=job.id,
            requested_by_principal_id=uuid.UUID("00000000-0000-4000-8000-000000000002"),
            scene_id="scene_1",
            text="Preview",
            voice="onyx",
            speed=Decimal("1.00"),
            request_hash="d" * 64,
            state="cancelled",
            finished_at=created,
            created_at=created,
            expires_at=created + timedelta(days=1),
        )
        session.add_all([delivery, preview])
        session.commit()
        job_id, project_id, event_id, delivery_id, preview_id = (
            job.id,
            job.project_id,
            event.id,
            delivery.id,
            preview.id,
        )

    with Session(db_engine) as session:
        assert purge_expired_metadata(session, now=now)[0] == 0
        blocked = purge_terminal_jobs_and_projects(session, now=now)
        assert blocked.deleted_jobs == 0
        assert blocked.blocked_pending_deliveries == 1
        assert blocked.blocked_object_refs == 1

        delivery = session.get(WebhookDelivery, delivery_id)
        delivery.state = "exhausted"
        session.delete(session.get(TtsPreview, preview_id))
        session.commit()
        assert purge_expired_metadata(session, now=now)[0] == 1
        assert session.get(WebhookDelivery, delivery_id) is None
        assert session.get(JobEvent, event_id) is None

        purged = purge_terminal_jobs_and_projects(session, now=now)
        assert (purged.deleted_jobs, purged.deleted_projects) == (1, 1)
        assert session.get(Job, job_id) is None
        assert session.get(Project, project_id) is None
