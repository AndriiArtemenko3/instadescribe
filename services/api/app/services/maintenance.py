"""Bounded, concurrency-safe beta lifecycle maintenance.

Every mutating selector uses ``FOR UPDATE SKIP LOCKED`` and an explicit
batch limit so rolling deployments may overlap without processing the same
tenant row.  Logs consume aggregate counters only; object identities and
tenant identifiers never leave this service.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.rfc3339 import utc_timestamp
from app.domain.retention import (
    AWAITING_UPLOAD_TTL,
    REVIEW_WARNING_LEAD,
)
from app.domain.states import JobState
from app.models import (
    Artifact,
    Asset,
    AuditEvent,
    Deliverable,
    IdempotencyRecord,
    Investigation,
    Job,
    JobEvent,
    OrganizationQuota,
    Project,
    Render,
    RenderAttemptArtifact,
    Review,
    TtsPreview,
    TtsPreviewArtifact,
    WebhookDelivery,
)
from app.repositories.jobs import transition_job
from app.services.quota import release_job_media

DEFAULT_BATCH_SIZE = 100
MAX_BATCH_SIZE = 500
METADATA_RETENTION_MAX = timedelta(days=365)

DeleteObjectVersion = Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class ObjectPurgeResult:
    purged: int = 0
    failed: int = 0
    unsafe: int = 0


@dataclass(frozen=True, slots=True)
class MaintenanceResult:
    expired_uploads: int = 0
    warned_reviews: int = 0
    expired_reviews: int = 0
    reaped_idempotency: int = 0
    purged_job_events: int = 0
    purged_audit_events: int = 0
    purged_assets: int = 0
    asset_purge_failures: int = 0
    unsafe_assets: int = 0
    purged_deliverables: int = 0
    deliverable_purge_failures: int = 0
    unsafe_deliverables: int = 0
    deleted_asset_metadata: int = 0
    deleted_deliverable_metadata: int = 0
    purged_legacy_artifacts: int = 0
    legacy_artifact_purge_failures: int = 0
    unsafe_legacy_artifacts: int = 0
    unrecoverable_legacy_artifacts: int = 0
    deleted_legacy_artifact_metadata: int = 0
    deleted_terminal_jobs: int = 0
    deleted_empty_projects: int = 0
    blocked_terminal_jobs_object_refs: int = 0
    blocked_terminal_jobs_pending_deliveries: int = 0


@dataclass(frozen=True, slots=True)
class TerminalMetadataPurgeResult:
    deleted_jobs: int = 0
    deleted_projects: int = 0
    blocked_object_refs: int = 0
    blocked_pending_deliveries: int = 0


def _instant(now: datetime | None) -> datetime:
    value = now or datetime.now(UTC)
    if value.tzinfo is None:
        raise ValueError("maintenance time must be timezone-aware")
    return value


def _limit(batch_size: int) -> int:
    if not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {MAX_BATCH_SIZE}")
    return batch_size


def _iso(value: datetime) -> str:
    return utc_timestamp(value)


def _policy_deadline(timestamp, retention_days):
    # PostgreSQL make_interval(years, months, weeks, days, hours, mins, secs).
    return timestamp + sa.func.make_interval(0, 0, 0, retention_days, 0, 0, 0)


def _effective_due(row_deadline, timestamp, retention_days, instant: datetime):
    # A manually shortened organization policy takes effect immediately but
    # can never extend a row's already-persisted retention deadline.
    return (
        sa.func.least(
            row_deadline,
            _policy_deadline(timestamp, retention_days),
        )
        <= instant
    )


def _clear_exact_job_source_identity(
    session: Session,
    *,
    organization_id: uuid.UUID,
    job_id: uuid.UUID,
    object_key: str,
    version_id: str,
) -> None:
    """Remove the Job's duplicate S3 pointer only after exact deletion.

    The conditional identity match prevents an old retention row from
    clearing a newer source generation.  Non-object metadata is cleared too
    so a later terminal Job purge cannot leave a misleading partial tuple.
    """

    session.execute(
        sa.update(Job)
        .where(
            Job.organization_id == organization_id,
            Job.id == job_id,
            Job.input_object_key == object_key,
            Job.source_version_id == version_id,
        )
        .values(
            input_object_key=None,
            input_content_type=None,
            input_size_bytes=None,
            source_etag=None,
            source_version_id=None,
            source_checksum_sha256=None,
            upload_verified_at=None,
            updated_at=sa.func.now(),
        )
    )


def _system_audit(
    *,
    organization_id: uuid.UUID,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID,
    outcome: str,
    occurred_at: datetime,
) -> AuditEvent:
    return AuditEvent(
        organization_id=organization_id,
        actor_principal_id=None,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id),
        request_id=None,
        details={"outcome": outcome},
        occurred_at=occurred_at,
        purge_after=occurred_at + METADATA_RETENTION_MAX,
    )


def _cancelled_event(job: Job, *, reason: str, occurred_at: datetime) -> dict:
    event_id = uuid.uuid4()
    return {
        "id": event_id,
        "organization_id": job.organization_id,
        "job_id": job.id,
        "event_type": "job.cancelled",
        "job_version": job.version,
        "payload": {
            "id": str(event_id),
            "type": "job.cancelled",
            "jobId": str(job.id),
            "state": "cancelled",
            "reason": reason,
            "occurredAt": _iso(occurred_at),
        },
        "occurred_at": occurred_at,
        "available_at": occurred_at,
        "purge_after": occurred_at + METADATA_RETENTION_MAX,
    }


def expire_awaiting_uploads(
    session: Session,
    *,
    now: datetime | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    """Cancel uploads abandoned for 24 hours and release their reservations."""

    instant = _instant(now)
    jobs = list(
        session.execute(
            sa.select(Job)
            .where(
                Job.status == JobState.AWAITING_UPLOAD.value,
                Job.created_at <= instant - AWAITING_UPLOAD_TTL,
            )
            .order_by(Job.created_at, Job.id)
            .limit(_limit(batch_size))
            .with_for_update(skip_locked=True)
        ).scalars()
    )
    expired = 0
    try:
        for locked_job in jobs:
            # This helper locks the reservation/usage rows and stages a
            # release. The capacity trigger remains the single owner of the
            # awaiting-upload counter when the transition below is flushed.
            release_job_media(
                session,
                locked_job.organization_id,
                locked_job.id,
                now=instant,
            )
            job = transition_job(
                session,
                locked_job.id,
                JobState.AWAITING_UPLOAD,
                JobState.CANCELLED,
                values={
                    "version": Job.version + 1,
                    "progress": 0,
                    "stage": "upload_expired",
                    "completed_at": instant,
                    "lease_expires_at": None,
                    "worker_id": None,
                    "error_code": "upload_expired",
                    "error_message": "The upload window expired.",
                },
            )
            if job is None:
                continue
            if job.workflow_kind == "video_investigation":
                mirrored = session.execute(
                    sa.update(Investigation)
                    .where(
                        Investigation.organization_id == job.organization_id,
                        Investigation.job_id == job.id,
                        Investigation.status == "awaiting_upload",
                    )
                    .values(status="cancelled", updated_at=instant)
                ).rowcount
                if mirrored != 1:
                    raise RuntimeError(
                        "video investigation upload expiry could not mirror its aggregate"
                    )
            # An abandoned reservation has no authoritative S3 VersionId to
            # retain or delete exactly. Remove only those uncompleted rows so
            # they cannot block the job/project metadata reaper forever. Any
            # bytes uploaded without a completion call remain covered by the
            # bucket's bounded lifecycle policy; no unsafe key-only delete is
            # attempted here.
            session.execute(
                sa.delete(Asset).where(
                    Asset.organization_id == job.organization_id,
                    Asset.job_id == job.id,
                    Asset.status == "awaiting_upload",
                    Asset.version_id.is_(None),
                )
            )
            session.execute(
                pg_insert(JobEvent)
                .values(**_cancelled_event(job, reason="upload_expired", occurred_at=instant))
                .on_conflict_do_nothing(index_elements=["organization_id", "job_id", "event_type"])
            )
            session.add(
                _system_audit(
                    organization_id=job.organization_id,
                    action="job.upload_expired",
                    resource_type="job",
                    resource_id=job.id,
                    outcome="expired",
                    occurred_at=instant,
                )
            )
            expired += 1
        session.commit()
    except Exception:
        session.rollback()
        raise
    return expired


def purge_cancelled_uncompleted_assets(
    session: Session,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    """Delete non-authoritative upload declarations left by cancellation.

    A row still in ``awaiting_upload`` with no S3 VersionId never completed
    the upload contract, so there is no exact object identity that could be
    deleted. Once its Job is durably cancelled the declaration serves no
    retention purpose and would otherwise block terminal Job/Project metadata
    deletion forever. Any unconfirmed bytes remain bounded by the bucket
    lifecycle policy; this function deliberately performs no key-only delete.
    """

    rows = list(
        session.execute(
            sa.select(Asset)
            .join(
                Job,
                sa.and_(
                    Job.organization_id == Asset.organization_id,
                    Job.id == Asset.job_id,
                ),
            )
            .where(
                Job.status == JobState.CANCELLED.value,
                Asset.status == "awaiting_upload",
                Asset.version_id.is_(None),
            )
            .order_by(Asset.created_at, Asset.id)
            .limit(_limit(batch_size))
            .with_for_update(of=Asset, skip_locked=True)
        ).scalars()
    )
    try:
        for row in rows:
            session.delete(row)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return len(rows)


def warn_reviews_nearing_expiry(
    session: Session,
    *,
    now: datetime | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    """Persist one internal warning per review activity window.

    No new public webhook event is invented. A later scene edit bumps
    ``Review.updated_at`` and makes the earlier warning stale, permitting one
    fresh warning for the extended inactivity window.
    """

    instant = _instant(now)
    already_warned = sa.exists(
        sa.select(AuditEvent.id).where(
            AuditEvent.organization_id == Review.organization_id,
            AuditEvent.action == "review.expiry_warning",
            AuditEvent.resource_type == "review",
            AuditEvent.resource_id == sa.cast(Review.id, sa.String),
            AuditEvent.occurred_at >= Review.updated_at,
        )
    )
    candidates = session.execute(
        sa.select(Job.id, Review.id)
        .join(
            Review,
            sa.and_(
                Review.organization_id == Job.organization_id,
                Review.job_id == Job.id,
            ),
        )
        .where(
            Job.status == JobState.READY_FOR_REVIEW.value,
            Review.state == "open",
            Review.inactivity_expires_at > instant,
            Review.inactivity_expires_at <= instant + REVIEW_WARNING_LEAD,
            ~already_warned,
        )
        .order_by(Review.inactivity_expires_at, Review.id)
        .limit(_limit(batch_size))
        .with_for_update(of=Job, skip_locked=True)
    ).all()
    warned = 0
    try:
        for job_id, review_id in candidates:
            review = session.execute(
                sa.select(Review)
                .where(
                    Review.id == review_id,
                    Review.job_id == job_id,
                    Review.state == "open",
                    Review.inactivity_expires_at > instant,
                    Review.inactivity_expires_at <= instant + REVIEW_WARNING_LEAD,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if review is None:
                continue
            exists_after_lock = session.scalar(
                sa.select(
                    sa.exists().where(
                        AuditEvent.organization_id == review.organization_id,
                        AuditEvent.action == "review.expiry_warning",
                        AuditEvent.resource_type == "review",
                        AuditEvent.resource_id == str(review.id),
                        AuditEvent.occurred_at >= review.updated_at,
                    )
                )
            )
            if exists_after_lock:
                continue
            session.add(
                _system_audit(
                    organization_id=review.organization_id,
                    action="review.expiry_warning",
                    resource_type="review",
                    resource_id=review.id,
                    outcome="warning",
                    occurred_at=instant,
                )
            )
            warned += 1
        session.commit()
    except Exception:
        session.rollback()
        raise
    return warned


def expire_inactive_reviews(
    session: Session,
    *,
    now: datetime | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    """Lock expired reviews and cancel their still-waiting jobs atomically."""

    instant = _instant(now)
    candidates = session.execute(
        sa.select(Job.id, Review.id)
        .join(
            Review,
            sa.and_(
                Review.organization_id == Job.organization_id,
                Review.job_id == Job.id,
            ),
        )
        .where(
            Job.status == JobState.READY_FOR_REVIEW.value,
            Review.state == "open",
            Review.inactivity_expires_at <= instant,
        )
        .order_by(Review.inactivity_expires_at, Review.id)
        .limit(_limit(batch_size))
        .with_for_update(of=Job, skip_locked=True)
    ).all()
    expired = 0
    try:
        for job_id, review_id in candidates:
            review = session.execute(
                sa.select(Review)
                .where(
                    Review.id == review_id,
                    Review.job_id == job_id,
                    Review.state == "open",
                    Review.inactivity_expires_at <= instant,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if review is None:
                continue
            job = transition_job(
                session,
                job_id,
                JobState.READY_FOR_REVIEW,
                JobState.CANCELLED,
                values={
                    "version": Job.version + 1,
                    "stage": "review_expired",
                    "completed_at": instant,
                    "lease_expires_at": None,
                    "worker_id": None,
                    "error_code": "review_expired",
                    "error_message": "The review inactivity window expired.",
                },
            )
            if job is None:
                continue
            review.state = "expired"
            review.locked_at = instant
            review.updated_at = instant
            review.version += 1
            session.execute(
                pg_insert(JobEvent)
                .values(**_cancelled_event(job, reason="review_expired", occurred_at=instant))
                .on_conflict_do_nothing(index_elements=["organization_id", "job_id", "event_type"])
            )
            session.add(
                _system_audit(
                    organization_id=review.organization_id,
                    action="review.expired",
                    resource_type="review",
                    resource_id=review.id,
                    outcome="expired",
                    occurred_at=instant,
                )
            )
            expired += 1
        session.commit()
    except Exception:
        session.rollback()
        raise
    return expired


def reap_expired_idempotency(
    session: Session,
    *,
    now: datetime | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    """Bound physical storage after on-demand key reuse semantics expire."""

    instant = _instant(now)
    records = list(
        session.execute(
            sa.select(IdempotencyRecord)
            .where(IdempotencyRecord.expires_at <= instant)
            .order_by(IdempotencyRecord.expires_at, IdempotencyRecord.id)
            .limit(_limit(batch_size))
            .with_for_update(skip_locked=True)
        ).scalars()
    )
    try:
        for record in records:
            session.delete(record)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return len(records)


def purge_expired_metadata(
    session: Session,
    *,
    now: datetime | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> tuple[int, int, int, int]:
    """Delete retained events in FK-safe order at the effective org deadline."""

    instant = _instant(now)
    limit = _limit(batch_size)
    pending_delivery = sa.exists(
        sa.select(WebhookDelivery.id).where(
            WebhookDelivery.organization_id == JobEvent.organization_id,
            WebhookDelivery.event_id == JobEvent.id,
            WebhookDelivery.state.in_(("pending", "in_flight", "retry_scheduled")),
        )
    ).correlate(JobEvent)
    events = list(
        session.execute(
            sa.select(JobEvent)
            .join(
                OrganizationQuota,
                OrganizationQuota.organization_id == JobEvent.organization_id,
            )
            .where(
                _effective_due(
                    JobEvent.purge_after,
                    JobEvent.occurred_at,
                    OrganizationQuota.metadata_retention_days,
                    instant,
                ),
                ~pending_delivery,
            )
            .order_by(JobEvent.purge_after, JobEvent.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).scalars()
    )
    audits = list(
        session.execute(
            sa.select(AuditEvent)
            .join(
                OrganizationQuota,
                OrganizationQuota.organization_id == AuditEvent.organization_id,
            )
            .where(
                _effective_due(
                    AuditEvent.purge_after,
                    AuditEvent.occurred_at,
                    OrganizationQuota.metadata_retention_days,
                    instant,
                )
            )
            .order_by(AuditEvent.purge_after, AuditEvent.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).scalars()
    )
    asset_metadata = list(
        session.execute(
            sa.select(Asset)
            .join(
                OrganizationQuota,
                OrganizationQuota.organization_id == Asset.organization_id,
            )
            .where(
                Asset.status == "deleted",
                _policy_deadline(
                    Asset.created_at,
                    OrganizationQuota.metadata_retention_days,
                )
                <= instant,
            )
            .order_by(Asset.created_at, Asset.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).scalars()
    )
    deliverable_metadata = list(
        session.execute(
            sa.select(Deliverable)
            .join(
                OrganizationQuota,
                OrganizationQuota.organization_id == Deliverable.organization_id,
            )
            .where(
                Deliverable.state == "purged",
                _policy_deadline(
                    Deliverable.created_at,
                    OrganizationQuota.metadata_retention_days,
                )
                <= instant,
            )
            .order_by(Deliverable.created_at, Deliverable.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).scalars()
    )
    try:
        if events:
            identities = [(row.organization_id, row.id) for row in events]
            session.execute(
                sa.delete(WebhookDelivery).where(
                    sa.tuple_(
                        WebhookDelivery.organization_id,
                        WebhookDelivery.event_id,
                    ).in_(identities)
                )
            )
            session.execute(
                sa.delete(JobEvent).where(
                    sa.tuple_(JobEvent.organization_id, JobEvent.id).in_(identities)
                )
            )
        for audit in audits:
            session.delete(audit)
        for asset in asset_metadata:
            session.delete(asset)
        affected_render_ids = {row.render_id for row in deliverable_metadata}
        for deliverable in deliverable_metadata:
            session.delete(deliverable)
        if affected_render_ids:
            session.flush()
            remaining_deliverable = sa.exists(
                sa.select(Deliverable.id).where(Deliverable.render_id == Render.id)
            ).correlate(Render)
            renders = list(
                session.execute(
                    sa.select(Render)
                    .where(
                        Render.id.in_(affected_render_ids),
                        ~remaining_deliverable,
                    )
                    .with_for_update(skip_locked=True)
                ).scalars()
            )
            for render in renders:
                # The integrity manifest contains version IDs.  Once the last
                # retained Deliverable tombstone is gone, scrub those duplicate
                # object references before terminal Job deletion is eligible.
                render.integrity_manifest = {}
                render.updated_at = instant
        session.commit()
    except Exception:
        session.rollback()
        raise
    return (
        len(events),
        len(audits),
        len(asset_metadata),
        len(deliverable_metadata),
    )


def _asset_identity_is_safe(asset: Asset) -> bool:
    branch = "source" if asset.asset_type == "source_video" else "transcript"
    expected = f"uploads/orgs/{asset.organization_id}/jobs/{asset.job_id}/{branch}/"
    return bool(
        asset.version_id and asset.object_key.startswith(expected) and ".." not in asset.object_key
    )


def _artifact_identity_is_safe(artifact: Artifact) -> bool:
    version_id = artifact.version_id
    if (
        not isinstance(version_id, str)
        or not version_id.strip()
        or len(version_id) > 1024
        or not isinstance(artifact.object_key, str)
        or not artifact.object_key
        or len(artifact.object_key) > 1024
        or ".." in artifact.object_key
    ):
        return False
    if artifact.artifact_type == "source_video":
        tenant_prefix = f"uploads/orgs/{artifact.organization_id}/jobs/{artifact.job_id}/source/"
        legacy_prefix = f"uploads/{artifact.job_id}/source/"
        return artifact.object_key.startswith(tenant_prefix) or (
            artifact.organization_id == uuid.UUID("00000000-0000-4000-8000-000000000001")
            and artifact.object_key.startswith(legacy_prefix)
        )
    prefix = f"jobs/{artifact.job_id}/attempts/"
    if not artifact.object_key.startswith(prefix):
        return False
    attempt, separator, remainder = artifact.object_key[len(prefix) :].partition("/")
    return bool(
        separator
        and attempt.isdigit()
        and int(attempt) >= 1
        and remainder
        and not remainder.startswith("/")
    )


def purge_due_legacy_artifacts(
    session: Session,
    delete_object_version: DeleteObjectVersion,
    *,
    now: datetime | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> ObjectPurgeResult:
    """Delete normalized legacy Artifact versions and retain tombstones.

    Rows without a recoverable VersionId are intentionally selected and
    counted as unsafe on every bounded pass.  They are never downgraded to a
    key-only delete and continue blocking terminal Job cascade deletion.
    """

    instant = _instant(now)
    rows = list(
        session.execute(
            sa.select(Artifact)
            .join(
                OrganizationQuota,
                OrganizationQuota.organization_id == Artifact.organization_id,
            )
            .where(
                Artifact.retention_state == "active",
                _effective_due(
                    Artifact.purge_after,
                    Artifact.created_at,
                    OrganizationQuota.source_retention_days,
                    instant,
                ),
            )
            .order_by(Artifact.purge_after, Artifact.id)
            .limit(_limit(batch_size))
            .with_for_update(skip_locked=True)
        ).scalars()
    )
    purged = failed = unsafe = 0
    try:
        for row in rows:
            if not _artifact_identity_is_safe(row):
                row.retention_state = "unrecoverable"
                unsafe += 1
                continue
            try:
                delete_object_version(row.object_key, row.version_id or "")
            except Exception:
                failed += 1
                continue
            row.retention_state = "purged"
            row.purged_at = instant
            if row.artifact_type == "source_video":
                _clear_exact_job_source_identity(
                    session,
                    organization_id=row.organization_id,
                    job_id=row.job_id,
                    object_key=row.object_key,
                    version_id=row.version_id or "",
                )
            purged += 1
        session.commit()
    except Exception:
        session.rollback()
        raise
    return ObjectPurgeResult(purged=purged, failed=failed, unsafe=unsafe)


def count_unrecoverable_legacy_artifacts(session: Session) -> int:
    """Return only a global aggregate; object and tenant identities stay private."""

    return int(
        session.scalar(
            sa.select(sa.func.count())
            .select_from(Artifact)
            .where(Artifact.retention_state == "unrecoverable")
        )
        or 0
    )


def purge_expired_legacy_artifact_metadata(
    session: Session,
    *,
    now: datetime | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    """Physically remove only exact-delete-proven Artifact tombstones."""

    instant = _instant(now)
    rows = list(
        session.execute(
            sa.select(Artifact)
            .join(
                OrganizationQuota,
                OrganizationQuota.organization_id == Artifact.organization_id,
            )
            .where(
                Artifact.retention_state == "purged",
                _policy_deadline(
                    Artifact.created_at,
                    OrganizationQuota.metadata_retention_days,
                )
                <= instant,
            )
            .order_by(Artifact.created_at, Artifact.id)
            .limit(_limit(batch_size))
            .with_for_update(skip_locked=True)
        ).scalars()
    )
    try:
        for row in rows:
            session.delete(row)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return len(rows)


def purge_due_assets(
    session: Session,
    delete_object_version: DeleteObjectVersion,
    *,
    now: datetime | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> ObjectPurgeResult:
    """Delete due source versions exactly and tombstone their existing row."""

    instant = _instant(now)
    assets = list(
        session.execute(
            sa.select(Asset)
            .join(
                OrganizationQuota,
                OrganizationQuota.organization_id == Asset.organization_id,
            )
            .where(
                Asset.status != "deleted",
                Asset.version_id.is_not(None),
                _effective_due(
                    Asset.purge_after,
                    Asset.created_at,
                    OrganizationQuota.source_retention_days,
                    instant,
                ),
            )
            .order_by(Asset.purge_after, Asset.id)
            .limit(_limit(batch_size))
            .with_for_update(skip_locked=True)
        ).scalars()
    )
    purged = failed = unsafe = 0
    try:
        for asset in assets:
            if not _asset_identity_is_safe(asset):
                unsafe += 1
                continue
            try:
                delete_object_version(asset.object_key, asset.version_id or "")
            except Exception:
                # The caller logs aggregate failure counts only. Retaining the
                # row makes the exact-version delete safely retryable.
                failed += 1
                continue
            asset.status = "deleted"
            asset.validated_at = None
            asset.updated_at = instant
            if asset.asset_type == "source_video":
                _clear_exact_job_source_identity(
                    session,
                    organization_id=asset.organization_id,
                    job_id=asset.job_id,
                    object_key=asset.object_key,
                    version_id=asset.version_id or "",
                )
            purged += 1
        session.commit()
    except Exception:
        session.rollback()
        raise
    return ObjectPurgeResult(purged=purged, failed=failed, unsafe=unsafe)


def purge_due_deliverables(
    session: Session,
    delete_object_version: DeleteObjectVersion,
    *,
    now: datetime | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> ObjectPurgeResult:
    """Delete due published versions exactly, then retain a metadata tombstone."""

    instant = _instant(now)
    rows = list(
        session.execute(
            sa.select(Deliverable)
            .join(
                OrganizationQuota,
                OrganizationQuota.organization_id == Deliverable.organization_id,
            )
            .where(
                Deliverable.state == "published",
                _effective_due(
                    Deliverable.purge_after,
                    Deliverable.created_at,
                    OrganizationQuota.deliverable_retention_days,
                    instant,
                ),
            )
            .order_by(Deliverable.purge_after, Deliverable.id)
            .limit(_limit(batch_size))
            .with_for_update(skip_locked=True)
        ).scalars()
    )
    purged = failed = unsafe = 0
    try:
        for row in rows:
            prefix = f"deliverables/orgs/{row.organization_id}/jobs/{row.job_id}/attempts/"
            if not (
                row.version_id and row.object_key.startswith(prefix) and ".." not in row.object_key
            ):
                unsafe += 1
                continue
            try:
                delete_object_version(row.object_key, row.version_id)
            except Exception:
                failed += 1
                continue
            row.state = "purged"
            row.purged_at = instant
            purged += 1
        session.commit()
    except Exception:
        session.rollback()
        raise
    return ObjectPurgeResult(purged=purged, failed=failed, unsafe=unsafe)


def _terminal_due(instant: datetime):
    # A cleanup write must not extend retention. Terminal transitions persist
    # completed_at; legacy terminal rows fall back to their original creation
    # time rather than a later janitor-updated timestamp.
    retained_from = sa.func.coalesce(Job.completed_at, Job.created_at)
    return sa.and_(
        Job.status.in_(
            (
                JobState.COMPLETED.value,
                JobState.FAILED.value,
                JobState.CANCELLED.value,
            )
        ),
        _policy_deadline(retained_from, OrganizationQuota.metadata_retention_days) <= instant,
    )


def purge_terminal_jobs_and_projects(
    session: Session,
    *,
    now: datetime | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> TerminalMetadataPurgeResult:
    """Delete terminal metadata only after every object/delivery reference is gone."""

    instant = _instant(now)
    limit = _limit(batch_size)
    artifact_ref = sa.exists(
        sa.select(Artifact.id).where(
            Artifact.organization_id == Job.organization_id,
            Artifact.job_id == Job.id,
        )
    ).correlate(Job)
    asset_ref = sa.exists(
        sa.select(Asset.id).where(
            Asset.organization_id == Job.organization_id,
            Asset.job_id == Job.id,
        )
    ).correlate(Job)
    deliverable_ref = sa.exists(
        sa.select(Deliverable.id).where(
            Deliverable.organization_id == Job.organization_id,
            Deliverable.job_id == Job.id,
        )
    ).correlate(Job)
    render_attempt_ref = sa.exists(
        sa.select(RenderAttemptArtifact.id).where(
            RenderAttemptArtifact.organization_id == Job.organization_id,
            RenderAttemptArtifact.job_id == Job.id,
        )
    ).correlate(Job)
    preview_ref = sa.exists(
        sa.select(TtsPreview.id).where(
            TtsPreview.organization_id == Job.organization_id,
            TtsPreview.job_id == Job.id,
        )
    ).correlate(Job)
    preview_artifact_ref = sa.exists(
        sa.select(TtsPreviewArtifact.id).where(
            TtsPreviewArtifact.organization_id == Job.organization_id,
            TtsPreviewArtifact.job_id == Job.id,
        )
    ).correlate(Job)
    render_manifest_ref = sa.exists(
        sa.select(Render.id).where(
            Render.organization_id == Job.organization_id,
            Render.job_id == Job.id,
            Render.integrity_manifest != {},
        )
    ).correlate(Job)
    event_ref = sa.exists(
        sa.select(JobEvent.id).where(
            JobEvent.organization_id == Job.organization_id,
            JobEvent.job_id == Job.id,
        )
    ).correlate(Job)
    pending_delivery_ref = sa.exists(
        sa.select(WebhookDelivery.id)
        .join(
            JobEvent,
            sa.and_(
                JobEvent.organization_id == WebhookDelivery.organization_id,
                JobEvent.id == WebhookDelivery.event_id,
            ),
        )
        .where(
            JobEvent.organization_id == Job.organization_id,
            JobEvent.job_id == Job.id,
            WebhookDelivery.state.in_(("pending", "in_flight", "retry_scheduled")),
        )
    ).correlate(Job)
    any_object_ref = sa.or_(
        Job.input_object_key.is_not(None),
        Job.source_version_id.is_not(None),
        artifact_ref,
        asset_ref,
        deliverable_ref,
        render_attempt_ref,
        preview_ref,
        preview_artifact_ref,
        render_manifest_ref,
    )
    due = _terminal_due(instant)
    blocked_object_refs = int(
        session.scalar(
            sa.select(sa.func.count())
            .select_from(Job)
            .join(
                OrganizationQuota,
                OrganizationQuota.organization_id == Job.organization_id,
            )
            .where(due, any_object_ref)
        )
        or 0
    )
    blocked_pending_deliveries = int(
        session.scalar(
            sa.select(sa.func.count())
            .select_from(Job)
            .join(
                OrganizationQuota,
                OrganizationQuota.organization_id == Job.organization_id,
            )
            .where(due, pending_delivery_ref)
        )
        or 0
    )
    jobs = list(
        session.execute(
            sa.select(Job)
            .join(
                OrganizationQuota,
                OrganizationQuota.organization_id == Job.organization_id,
            )
            .where(
                due,
                ~any_object_ref,
                ~event_ref,
                ~pending_delivery_ref,
            )
            .order_by(sa.func.coalesce(Job.completed_at, Job.created_at), Job.id)
            .limit(limit)
            .with_for_update(of=Job, skip_locked=True)
        ).scalars()
    )
    deleted_jobs = 0
    deleted_projects = 0
    try:
        for job in jobs:
            session.delete(job)
            deleted_jobs += 1
        if jobs:
            session.flush()

        remaining_job = sa.exists(
            sa.select(Job.id).where(
                Job.organization_id == Project.organization_id,
                Job.project_id == Project.id,
            )
        ).correlate(Project)
        projects = list(
            session.execute(
                sa.select(Project)
                .join(
                    OrganizationQuota,
                    OrganizationQuota.organization_id == Project.organization_id,
                )
                .where(
                    _policy_deadline(
                        Project.updated_at,
                        OrganizationQuota.metadata_retention_days,
                    )
                    <= instant,
                    ~remaining_job,
                )
                .order_by(Project.updated_at, Project.id)
                .limit(limit)
                .with_for_update(of=Project, skip_locked=True)
            ).scalars()
        )
        for project in projects:
            session.delete(project)
            deleted_projects += 1
        session.commit()
    except Exception:
        session.rollback()
        raise
    return TerminalMetadataPurgeResult(
        deleted_jobs=deleted_jobs,
        deleted_projects=deleted_projects,
        blocked_object_refs=blocked_object_refs,
        blocked_pending_deliveries=blocked_pending_deliveries,
    )


def run_maintenance_cycle(
    session: Session,
    delete_object_version: DeleteObjectVersion,
    *,
    now: datetime | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> MaintenanceResult:
    """Run one bounded cycle and return sanitized aggregate telemetry."""

    instant = _instant(now)
    expired_uploads = expire_awaiting_uploads(session, now=instant, batch_size=batch_size)
    uncompleted_asset_metadata = purge_cancelled_uncompleted_assets(
        session,
        batch_size=batch_size,
    )
    warned_reviews = warn_reviews_nearing_expiry(session, now=instant, batch_size=batch_size)
    expired_reviews = expire_inactive_reviews(session, now=instant, batch_size=batch_size)
    reaped_idempotency = reap_expired_idempotency(session, now=instant, batch_size=batch_size)
    asset_result = purge_due_assets(
        session,
        delete_object_version,
        now=instant,
        batch_size=batch_size,
    )
    deliverable_result = purge_due_deliverables(
        session,
        delete_object_version,
        now=instant,
        batch_size=batch_size,
    )
    artifact_result = purge_due_legacy_artifacts(
        session,
        delete_object_version,
        now=instant,
        batch_size=batch_size,
    )
    unrecoverable_artifacts = count_unrecoverable_legacy_artifacts(session)
    (
        purged_job_events,
        purged_audit_events,
        deleted_asset_metadata,
        deleted_deliverable_metadata,
    ) = purge_expired_metadata(session, now=instant, batch_size=batch_size)
    deleted_artifact_metadata = purge_expired_legacy_artifact_metadata(
        session,
        now=instant,
        batch_size=batch_size,
    )
    terminal_result = purge_terminal_jobs_and_projects(
        session,
        now=instant,
        batch_size=batch_size,
    )
    return MaintenanceResult(
        expired_uploads=expired_uploads,
        warned_reviews=warned_reviews,
        expired_reviews=expired_reviews,
        reaped_idempotency=reaped_idempotency,
        purged_job_events=purged_job_events,
        purged_audit_events=purged_audit_events,
        purged_assets=asset_result.purged,
        asset_purge_failures=asset_result.failed,
        unsafe_assets=asset_result.unsafe,
        purged_deliverables=deliverable_result.purged,
        deliverable_purge_failures=deliverable_result.failed,
        unsafe_deliverables=deliverable_result.unsafe,
        deleted_asset_metadata=deleted_asset_metadata + uncompleted_asset_metadata,
        deleted_deliverable_metadata=deleted_deliverable_metadata,
        purged_legacy_artifacts=artifact_result.purged,
        legacy_artifact_purge_failures=artifact_result.failed,
        unsafe_legacy_artifacts=artifact_result.unsafe,
        unrecoverable_legacy_artifacts=unrecoverable_artifacts,
        deleted_legacy_artifact_metadata=deleted_artifact_metadata,
        deleted_terminal_jobs=terminal_result.deleted_jobs,
        deleted_empty_projects=terminal_result.deleted_projects,
        blocked_terminal_jobs_object_refs=terminal_result.blocked_object_refs,
        blocked_terminal_jobs_pending_deliveries=terminal_result.blocked_pending_deliveries,
    )
