"""Atomic orchestration for review completion and five-format publication.

This module deliberately stops at persistence orchestration.  It neither
renders media nor performs network I/O.  A later render worker may claim the
durable render intent, stage version-pinned object rows and invoke the fenced
publication primitive.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import sqlalchemy as sa
from instadescribe_contracts.provider import (
    TTS_BETA_MAX_APPROVED_SCENES_PER_REVIEW,
    TTS_BETA_MAX_RENDER_ATTEMPTS_PER_REVIEW,
)
from sqlalchemy.orm import Session

from app.core.tenancy import PrincipalContext
from app.domain.states import JobState, validate_transition
from app.models import Deliverable, Job, JobEvent, Render, RenderAttemptArtifact, Review
from app.repositories import lifecycle as lifecycle_repository

DELIVERABLE_FORMATS = frozenset({"mp4", "mp3", "srt", "csv", "docx"})
DELIVERABLE_FILE_NAMES = {
    "mp4": "described_video.mp4",
    "mp3": "audio_description.mp3",
    "srt": "audio_description.srt",
    "csv": "audio_description.csv",
    "docx": "audio_description.docx",
}
DELIVERABLE_CONTENT_TYPES = {
    "mp4": "video/mp4",
    "mp3": "audio/mpeg",
    "srt": "application/x-subrip",
    "csv": "text/csv",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
DELIVERABLE_ORDER = {name: index for index, name in enumerate(("mp4", "mp3", "srt", "csv", "docx"))}
_SCENE_ID_RE = re.compile(r"^scene_[1-9][0-9]*$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


class LifecycleServiceError(Exception):
    """Stable, transport-neutral lifecycle failure."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


class LifecycleNotFound(LifecycleServiceError):
    def __init__(self, resource: str) -> None:
        super().__init__("not_found", f"{resource} was not found.")


class LifecycleConflict(LifecycleServiceError):
    pass


class LifecycleInvariantError(LifecycleServiceError):
    pass


class RenderAttemptsExhausted(LifecycleConflict):
    """A durable render reached its total claim budget and was failed."""


@dataclass(frozen=True, slots=True)
class ReviewSnapshot:
    review: Review
    scene_count: int | None
    decided_scene_count: int | None
    approved_scene_count: int | None
    rejected_scene_count: int | None


@dataclass(frozen=True, slots=True)
class FinishReviewResult:
    review: Review
    render: Render
    event: JobEvent | None
    idempotent: bool


@dataclass(frozen=True, slots=True)
class PublishResult:
    render: Render
    deliverables: tuple[Deliverable, ...]
    event: JobEvent
    idempotent: bool


@dataclass(frozen=True, slots=True)
class StagedDeliverableSpec:
    format: str
    object_key: str
    version_id: str
    content_type: str
    size_bytes: int
    checksum_sha256: str


def orphaned_render_attempt_condition():
    """SQL predicate for exact-version journals that no live fence may publish.

    Callers must join ``RenderAttemptArtifact`` to ``Render``.  The correlated
    published-identity exclusion is deliberately part of the predicate so a
    maintenance bug cannot select a customer-visible object for deletion.
    """

    published_identity = sa.exists(
        sa.select(Deliverable.id).where(
            Deliverable.organization_id == RenderAttemptArtifact.organization_id,
            Deliverable.render_id == RenderAttemptArtifact.render_id,
            Deliverable.object_key == RenderAttemptArtifact.object_key,
            Deliverable.version_id == RenderAttemptArtifact.version_id,
            # Published rows and later retention tombstones are both
            # authoritative proof that this version was customer-visible.
            Deliverable.state != "staged",
        )
    ).correlate(RenderAttemptArtifact)
    return sa.and_(
        ~published_identity,
        sa.or_(
            Render.state.in_(("queued", "completed", "failed", "cancelled")),
            Render.fence_token != RenderAttemptArtifact.fence_token,
            sa.and_(
                Render.state == "rendering",
                sa.or_(
                    Render.lease_expires_at.is_(None),
                    Render.lease_expires_at <= sa.func.now(),
                ),
            ),
        ),
    )


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _scene_ids(artifact) -> tuple[str, ...]:
    if artifact is None or not isinstance(artifact.meta, dict):
        raise LifecycleInvariantError(
            "scene_manifest_unavailable",
            "The immutable scene manifest is unavailable; review cannot be completed.",
        )
    raw_ids = artifact.meta.get("scene_ids")
    raw_count = artifact.meta.get("scene_count")
    if (
        not isinstance(raw_ids, list)
        or not raw_ids
        or isinstance(raw_count, bool)
        or not isinstance(raw_count, int)
        or raw_count != len(raw_ids)
        or any(not isinstance(item, str) or not _SCENE_ID_RE.fullmatch(item) for item in raw_ids)
        or len(set(raw_ids)) != len(raw_ids)
    ):
        raise LifecycleInvariantError(
            "scene_manifest_invalid",
            "The immutable scene manifest is inconsistent; review cannot be completed.",
        )
    return tuple(raw_ids)


def _deliverable_identity_valid(
    row: Deliverable,
    principal: PrincipalContext,
    job_id: uuid.UUID,
) -> bool:
    prefix = f"deliverables/orgs/{principal.organization_id}/jobs/{job_id}/"
    return (
        row.organization_id == principal.organization_id
        and row.job_id == job_id
        and isinstance(row.object_key, str)
        and len(prefix) < len(row.object_key) <= 1024
        and row.object_key.startswith(prefix)
        and ".." not in row.object_key
        and isinstance(row.version_id, str)
        and bool(row.version_id.strip())
    )


def _require_deliverable_identities(
    rows: list[Deliverable] | tuple[Deliverable, ...],
    principal: PrincipalContext,
    job_id: uuid.UUID,
) -> None:
    if any(not _deliverable_identity_valid(row, principal, job_id) for row in rows):
        raise LifecycleInvariantError(
            "deliverable_identity_invalid",
            "A deliverable has an invalid tenant-scoped object identity.",
        )


def _validate_staged_spec(
    item: StagedDeliverableSpec,
    principal: PrincipalContext,
    job_id: uuid.UUID,
    fence_token: int,
) -> None:
    filename = DELIVERABLE_FILE_NAMES.get(item.format)
    prefix = f"deliverables/orgs/{principal.organization_id}/jobs/{job_id}/attempts/{fence_token}/"
    if (
        filename is None
        or item.object_key != f"{prefix}{filename}"
        or not isinstance(item.version_id, str)
        or not item.version_id.strip()
        or len(item.version_id) > 1024
        or item.content_type != DELIVERABLE_CONTENT_TYPES[item.format]
        or isinstance(item.size_bytes, bool)
        or not isinstance(item.size_bytes, int)
        or item.size_bytes < 0
        or not isinstance(item.checksum_sha256, str)
        or not _SHA256_RE.fullmatch(item.checksum_sha256)
    ):
        raise LifecycleInvariantError(
            "deliverable_identity_invalid",
            "A staged deliverable has invalid attempt-scoped identity metadata.",
        )


def record_render_attempt_artifact(
    session: Session,
    principal: PrincipalContext,
    job_id: uuid.UUID,
    *,
    worker_id: str,
    fence_token: int,
    artifact: StagedDeliverableSpec,
) -> RenderAttemptArtifact:
    """Durably journal one exact S3 version under the current live fence."""

    _validate_staged_spec(artifact, principal, job_id, fence_token)
    try:
        job = lifecycle_repository.get_job(session, principal, job_id, for_update=True)
        render = lifecycle_repository.get_render(session, principal, job_id, for_update=True)
        now = datetime.now(UTC)
        if (
            job is None
            or render is None
            or job.status != JobState.EXPORTING.value
            or render.state != "rendering"
            or render.worker_id != worker_id
            or render.fence_token != fence_token
            or render.lease_expires_at is None
            or render.lease_expires_at <= now
        ):
            raise LifecycleConflict(
                "render_fence_lost",
                "The render lease was superseded; uploaded output cannot be journaled.",
            )
        row = RenderAttemptArtifact(
            organization_id=principal.organization_id,
            job_id=job_id,
            render_id=render.id,
            fence_token=fence_token,
            format=artifact.format,
            object_key=artifact.object_key,
            version_id=artifact.version_id,
        )
        session.add(row)
        session.commit()
        return row
    except Exception:
        session.rollback()
        raise


def get_review_snapshot(
    session: Session,
    principal: PrincipalContext,
    job_id: uuid.UUID,
) -> ReviewSnapshot:
    job = lifecycle_repository.get_job(session, principal, job_id)
    if job is None:
        raise LifecycleNotFound("Review")
    review = lifecycle_repository.get_review(session, principal, job_id)
    if review is None:
        raise LifecycleConflict(
            "review_not_available",
            "Review is not available for this job yet.",
        )
    if review.state == "completed":
        return ReviewSnapshot(
            review=review,
            scene_count=review.scene_count,
            decided_scene_count=review.scene_count,
            approved_scene_count=review.approved_scene_count,
            rejected_scene_count=review.rejected_scene_count,
        )
    if review.state == "expired":
        return ReviewSnapshot(review, None, None, None, None)

    scene_ids = _scene_ids(lifecycle_repository.get_scene_manifest(session, principal, job_id))
    expected = set(scene_ids)
    decisions = {
        row.scene_id: row.review_status
        for row in lifecycle_repository.list_scene_overrides(session, principal, job_id)
        if row.scene_id in expected and row.review_status in {"approved", "rejected"}
    }
    approved = sum(value == "approved" for value in decisions.values())
    rejected = sum(value == "rejected" for value in decisions.values())
    return ReviewSnapshot(
        review=review,
        scene_count=len(scene_ids),
        decided_scene_count=len(decisions),
        approved_scene_count=approved,
        rejected_scene_count=rejected,
    )


def get_render(
    session: Session,
    principal: PrincipalContext,
    job_id: uuid.UUID,
) -> Render:
    if lifecycle_repository.get_job(session, principal, job_id) is None:
        raise LifecycleNotFound("Render")
    render = lifecycle_repository.get_render(session, principal, job_id)
    if render is None:
        raise LifecycleConflict(
            "render_not_started",
            "Rendering has not started for this job.",
        )
    return render


def list_published_deliverables(
    session: Session,
    principal: PrincipalContext,
    job_id: uuid.UUID,
) -> tuple[Deliverable, ...]:
    job = lifecycle_repository.get_job(session, principal, job_id)
    if job is None:
        raise LifecycleNotFound("Job")
    render = lifecycle_repository.get_render(session, principal, job_id)
    if job.status != JobState.COMPLETED.value or render is None or render.state != "completed":
        raise LifecycleConflict(
            "deliverables_not_ready",
            "The complete deliverable set is not available yet.",
        )
    rows = lifecycle_repository.list_render_deliverables(session, principal, render.id)
    if len(rows) != len(DELIVERABLE_FORMATS) or {row.format for row in rows} != DELIVERABLE_FORMATS:
        raise LifecycleInvariantError(
            "deliverables_unavailable",
            "The completed deliverable set is temporarily unavailable.",
        )
    _require_deliverable_identities(rows, principal, job_id)
    if any(row.state != "published" or row.published_at is None for row in rows):
        raise LifecycleInvariantError(
            "deliverables_unavailable",
            "The completed deliverable set is temporarily unavailable.",
        )
    return tuple(sorted(rows, key=lambda row: DELIVERABLE_ORDER[row.format]))


def get_download_target(
    session: Session,
    principal: PrincipalContext,
    deliverable_id: uuid.UUID,
) -> Deliverable:
    deliverable = lifecycle_repository.get_published_deliverable(
        session,
        principal,
        deliverable_id,
    )
    if deliverable is None:
        raise LifecycleNotFound("Deliverable")
    _require_deliverable_identities([deliverable], principal, deliverable.job_id)
    return deliverable


def finish_review(
    session: Session,
    principal: PrincipalContext,
    job_id: uuid.UUID,
    *,
    zero_ad_confirmed: bool,
    commit_transaction: bool = True,
) -> FinishReviewResult:
    """Lock a complete review, enqueue its render and persist one outbox intent.

    The Job row is locked first, matching the existing scene-edit write path.
    Consequently a scene decision either commits before this snapshot or waits
    and then observes a non-editable job; no decision can cross the boundary.
    """

    try:
        job = lifecycle_repository.get_job(session, principal, job_id, for_update=True)
        if job is None:
            raise LifecycleNotFound("Job")
        review = lifecycle_repository.get_review(session, principal, job_id, for_update=True)
        if review is None:
            raise LifecycleConflict(
                "review_not_available",
                "Review is not available for this job yet.",
            )

        if review.state == "completed":
            render = lifecycle_repository.get_render(session, principal, job_id, for_update=True)
            if render is None:
                raise LifecycleInvariantError(
                    "render_intent_missing",
                    "The completed review has no render intent.",
                )
            persisted_zero_ad = review.approved_scene_count == 0
            if zero_ad_confirmed != persisted_zero_ad:
                raise LifecycleConflict(
                    "review_already_completed",
                    "Review is already completed with a different zero-AD decision.",
                )
            event = session.execute(
                sa.select(JobEvent).where(
                    JobEvent.organization_id == principal.organization_id,
                    JobEvent.job_id == job_id,
                    JobEvent.event_type == "render.requested",
                )
            ).scalar_one_or_none()
            if event is None:
                raise LifecycleInvariantError(
                    "render_intent_missing",
                    "The completed review has no render outbox intent.",
                )
            if commit_transaction:
                session.commit()
            else:
                session.flush()
            return FinishReviewResult(review, render, event, True)

        if review.state != "open" or job.status != JobState.READY_FOR_REVIEW.value:
            raise LifecycleConflict(
                "review_not_editable",
                "Review cannot be completed in the job's current state.",
            )

        scene_ids = _scene_ids(lifecycle_repository.get_scene_manifest(session, principal, job_id))
        expected = set(scene_ids)
        overrides = lifecycle_repository.list_scene_overrides(session, principal, job_id)
        by_scene = {row.scene_id: row for row in overrides}
        if set(by_scene) - expected:
            raise LifecycleConflict(
                "scene_decisions_invalid",
                "Review contains a decision for a scene outside the immutable manifest.",
            )
        undecided = [
            scene_id
            for scene_id in scene_ids
            if scene_id not in by_scene
            or by_scene[scene_id].review_status not in {"approved", "rejected"}
        ]
        if undecided:
            raise LifecycleConflict(
                "scene_decisions_incomplete",
                "Every scene must be explicitly approved or rejected before finishing review.",
            )

        approved_count = sum(
            by_scene[scene_id].review_status == "approved" for scene_id in scene_ids
        )
        rejected_count = len(scene_ids) - approved_count
        if approved_count == 0 and not zero_ad_confirmed:
            raise LifecycleConflict(
                "zero_ad_confirmation_required",
                "Explicit zero-AD confirmation is required when no scene is approved.",
            )
        if approved_count > 0 and zero_ad_confirmed:
            raise LifecycleConflict(
                "zero_ad_confirmation_invalid",
                "Zero-AD confirmation is valid only when no scene is approved.",
            )
        if approved_count > TTS_BETA_MAX_APPROVED_SCENES_PER_REVIEW:
            raise LifecycleConflict(
                "tts_review_limit_exceeded",
                "A beta review may approve at most "
                f"{TTS_BETA_MAX_APPROVED_SCENES_PER_REVIEW} scenes.",
            )

        now = datetime.now(UTC)
        review.state = "completed"
        review.version += 1
        review.scene_count = len(scene_ids)
        review.approved_scene_count = approved_count
        review.rejected_scene_count = rejected_count
        review.locked_at = now
        review.completed_at = now
        review.completed_by_principal_id = principal.principal_id
        review.zero_ad_confirmed_at = now if approved_count == 0 else None
        review.updated_at = now

        render = Render(
            organization_id=principal.organization_id,
            job_id=job.id,
            review_id=review.id,
            state="queued",
        )
        session.add(render)
        session.flush()

        validate_transition(JobState(job.status), JobState.EXPORT_QUEUED)
        job.status = JobState.EXPORT_QUEUED.value
        job.version += 1
        job.progress = 0
        job.stage = "render_queued"
        job.completed_at = None
        job.worker_id = None
        job.lease_expires_at = None
        job.updated_at = now

        event_id = uuid.uuid4()
        event = JobEvent(
            id=event_id,
            organization_id=principal.organization_id,
            job_id=job.id,
            event_type="render.requested",
            job_version=job.version,
            payload={
                "id": str(event_id),
                "type": "render.requested",
                "jobId": str(job.id),
                "reviewId": str(review.id),
                "renderId": str(render.id),
                "occurredAt": _iso(now),
            },
            occurred_at=now,
            available_at=now,
        )
        session.add(event)
        if commit_transaction:
            session.commit()
        else:
            # Browser Finish Review owns an outer transaction containing its
            # idempotency claim and exact response body.  Flushing here keeps
            # Review + Render + Job + outbox atomic with that record; the
            # caller performs the one commit after completing the claim.
            session.flush()
        return FinishReviewResult(review, render, event, False)
    except Exception:
        if commit_transaction:
            session.rollback()
        raise


def claim_render(
    session: Session,
    principal: PrincipalContext,
    job_id: uuid.UUID,
    *,
    worker_id: str,
    lease_expires_at: datetime,
) -> Render:
    """Fence one render worker and atomically enter EXPORTING."""

    if not 1 <= len(worker_id) <= 255:
        raise ValueError("worker_id must contain 1-255 characters")
    now = datetime.now(UTC)
    if lease_expires_at.tzinfo is None or lease_expires_at <= now:
        raise ValueError("lease_expires_at must be a future timezone-aware timestamp")
    try:
        job = lifecycle_repository.get_job(session, principal, job_id, for_update=True)
        if job is None:
            raise LifecycleNotFound("Job")
        render = lifecycle_repository.get_render(session, principal, job_id, for_update=True)
        if render is None:
            raise LifecycleConflict("render_not_started", "Rendering has not started for this job.")

        if (
            render.state == "rendering"
            and render.worker_id == worker_id
            and render.lease_expires_at is not None
            and render.lease_expires_at > now
        ):
            session.commit()
            return render
        reclaim = (
            render.state == "rendering"
            and render.lease_expires_at is not None
            and render.lease_expires_at <= now
            and job.status == JobState.EXPORTING.value
        )
        first_claim = render.state == "queued" and job.status == JobState.EXPORT_QUEUED.value
        if not first_claim and not reclaim:
            raise LifecycleConflict(
                "render_claim_conflict",
                "Render cannot be claimed in its current state.",
            )
        if render.attempt_count >= TTS_BETA_MAX_RENDER_ATTEMPTS_PER_REVIEW:
            # attempt_count is the durable paid-TTS reservation ledger: one
            # claim reserves at most one synthesis call per approved scene.
            # An expired attempt is never granted an unbounded third claim.
            validate_transition(JobState(job.status), JobState.FAILED)
            error_code = "render_attempt_limit_exceeded"
            error_message = "The render reached the beta attempt limit."
            render.state = "failed"
            render.worker_id = None
            render.lease_expires_at = None
            render.error_code = error_code
            render.error_message = error_message
            render.completed_at = now
            render.updated_at = now

            job.status = JobState.FAILED.value
            job.version += 1
            job.stage = "render_failed"
            job.worker_id = None
            job.lease_expires_at = None
            job.error_code = error_code
            job.error_message = error_message
            job.completed_at = now
            job.updated_at = now

            event_id = uuid.uuid4()
            session.add(
                JobEvent(
                    id=event_id,
                    organization_id=principal.organization_id,
                    job_id=job.id,
                    event_type="job.failed",
                    job_version=job.version,
                    payload={
                        "id": str(event_id),
                        "type": "job.failed",
                        "jobId": str(job.id),
                        "state": "failed",
                        "errorCode": error_code,
                        "occurredAt": _iso(now),
                    },
                    occurred_at=now,
                    available_at=now,
                )
            )
            session.commit()
            raise RenderAttemptsExhausted(error_code, error_message)
        if first_claim:
            validate_transition(JobState(job.status), JobState.EXPORTING)
            job.status = JobState.EXPORTING.value
            job.version += 1
            job.stage = "rendering"
            job.updated_at = now
            render.started_at = now
        render.state = "rendering"
        render.fence_token += 1
        render.attempt_count += 1
        render.worker_id = worker_id
        render.lease_expires_at = lease_expires_at
        render.error_code = None
        render.error_message = None
        render.updated_at = now
        session.commit()
        return render
    except RenderAttemptsExhausted:
        # The terminal transition was deliberately committed above.
        raise
    except Exception:
        session.rollback()
        raise


def renew_render_lease(
    session: Session,
    principal: PrincipalContext,
    job_id: uuid.UUID,
    *,
    worker_id: str,
    fence_token: int,
    lease_expires_at: datetime,
) -> bool:
    """Extend only a live render lease owned by the exact current fence."""

    now = datetime.now(UTC)
    if lease_expires_at.tzinfo is None or lease_expires_at <= now:
        raise ValueError("lease_expires_at must be a future timezone-aware timestamp")
    try:
        owns_exporting_job = sa.exists(
            sa.select(1).where(
                Job.organization_id == principal.organization_id,
                Job.id == job_id,
                Job.status == JobState.EXPORTING.value,
            )
        )
        result = session.execute(
            sa.update(Render)
            .where(
                Render.organization_id == principal.organization_id,
                Render.job_id == job_id,
                Render.state == "rendering",
                Render.worker_id == worker_id,
                Render.fence_token == fence_token,
                Render.lease_expires_at.is_not(None),
                Render.lease_expires_at > sa.func.now(),
                owns_exporting_job,
            )
            .values(lease_expires_at=lease_expires_at, updated_at=sa.func.now())
        )
        session.commit()
        return result.rowcount == 1
    except Exception:
        session.rollback()
        raise


def cancel_render_if_job_cancelled(
    session: Session,
    principal: PrincipalContext,
    job_id: uuid.UUID,
    *,
    worker_id: str,
    fence_token: int,
) -> bool:
    """Close the matching render after its owning Job was durably cancelled."""

    try:
        job = lifecycle_repository.get_job(session, principal, job_id, for_update=True)
        if job is None or job.status != JobState.CANCELLED.value:
            session.rollback()
            return False
        render = lifecycle_repository.get_render(session, principal, job_id, for_update=True)
        if (
            render is None
            or render.state != "rendering"
            or render.worker_id != worker_id
            or render.fence_token != fence_token
        ):
            session.rollback()
            return False
        now = datetime.now(UTC)
        render.state = "cancelled"
        render.worker_id = None
        render.lease_expires_at = None
        render.completed_at = now
        render.updated_at = now
        session.commit()
        return True
    except Exception:
        session.rollback()
        raise


def stage_render_deliverables(
    session: Session,
    principal: PrincipalContext,
    job_id: uuid.UUID,
    *,
    worker_id: str,
    fence_token: int,
    deliverables: tuple[StagedDeliverableSpec, ...],
) -> tuple[Deliverable, ...]:
    """Persist one complete attempt-scoped set under the current live fence."""

    by_format = {item.format: item for item in deliverables}
    if len(deliverables) != len(DELIVERABLE_FORMATS) or set(by_format) != DELIVERABLE_FORMATS:
        raise LifecycleInvariantError(
            "deliverable_set_incomplete",
            "Exactly one MP4, MP3, SRT, CSV and DOCX is required.",
        )
    for item in by_format.values():
        _validate_staged_spec(item, principal, job_id, fence_token)

    try:
        job = lifecycle_repository.get_job(session, principal, job_id, for_update=True)
        render = lifecycle_repository.get_render(session, principal, job_id, for_update=True)
        now = datetime.now(UTC)
        if (
            job is None
            or render is None
            or job.status != JobState.EXPORTING.value
            or render.state != "rendering"
            or render.worker_id != worker_id
            or render.fence_token != fence_token
            or render.lease_expires_at is None
            or render.lease_expires_at <= now
        ):
            raise LifecycleConflict(
                "render_fence_lost",
                "The render lease was superseded; output cannot be staged.",
            )

        existing = lifecycle_repository.list_render_deliverables(
            session,
            principal,
            render.id,
            for_update=True,
        )
        if any(row.state != "staged" or row.published_at is not None for row in existing):
            raise LifecycleInvariantError(
                "deliverable_set_inconsistent",
                "Published deliverables cannot be replaced by a render retry.",
            )
        if existing:
            session.execute(
                sa.delete(Deliverable).where(
                    Deliverable.organization_id == principal.organization_id,
                    Deliverable.render_id == render.id,
                )
            )

        rows = tuple(
            Deliverable(
                organization_id=principal.organization_id,
                job_id=job_id,
                render_id=render.id,
                format=format_name,
                state="staged",
                object_key=item.object_key,
                version_id=item.version_id,
                content_type=item.content_type,
                size_bytes=item.size_bytes,
                checksum_sha256=item.checksum_sha256,
            )
            for format_name, item in sorted(
                by_format.items(), key=lambda pair: DELIVERABLE_ORDER[pair[0]]
            )
        )
        session.add_all(rows)
        session.commit()
        return rows
    except Exception:
        session.rollback()
        raise


def fail_render(
    session: Session,
    principal: PrincipalContext,
    job_id: uuid.UUID,
    *,
    worker_id: str,
    fence_token: int,
    error_code: str,
    error_message: str,
) -> JobEvent | None:
    """Atomically fail only a still-live current render fence and emit outbox."""

    if not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", error_code):
        raise ValueError("error_code must be a bounded stable identifier")
    if not 1 <= len(error_message) <= 200:
        raise ValueError("error_message must contain 1-200 characters")
    try:
        job = lifecycle_repository.get_job(session, principal, job_id, for_update=True)
        render = lifecycle_repository.get_render(session, principal, job_id, for_update=True)
        now = datetime.now(UTC)
        if (
            job is None
            or render is None
            or job.status != JobState.EXPORTING.value
            or render.state != "rendering"
            or render.worker_id != worker_id
            or render.fence_token != fence_token
            or render.lease_expires_at is None
            or render.lease_expires_at <= now
        ):
            session.rollback()
            return None

        validate_transition(JobState(job.status), JobState.FAILED)
        render.state = "failed"
        render.worker_id = None
        render.lease_expires_at = None
        render.error_code = error_code
        render.error_message = error_message
        render.completed_at = now
        render.updated_at = now

        job.status = JobState.FAILED.value
        job.version += 1
        job.stage = "render_failed"
        job.worker_id = None
        job.lease_expires_at = None
        job.error_code = error_code
        job.error_message = error_message
        job.completed_at = now
        job.updated_at = now

        event_id = uuid.uuid4()
        event = JobEvent(
            id=event_id,
            organization_id=principal.organization_id,
            job_id=job.id,
            event_type="job.failed",
            job_version=job.version,
            payload={
                "id": str(event_id),
                "type": "job.failed",
                "jobId": str(job.id),
                "state": "failed",
                "errorCode": error_code,
                "occurredAt": _iso(now),
            },
            occurred_at=now,
            available_at=now,
        )
        session.add(event)
        session.commit()
        return event
    except Exception:
        session.rollback()
        raise


def publish_staged_deliverables(
    session: Session,
    principal: PrincipalContext,
    job_id: uuid.UUID,
    *,
    worker_id: str,
    fence_token: int,
) -> PublishResult:
    """Atomically publish exactly MP4/MP3/SRT/CSV/DOCX and complete the job."""

    try:
        job = lifecycle_repository.get_job(session, principal, job_id, for_update=True)
        if job is None:
            raise LifecycleNotFound("Job")
        render = lifecycle_repository.get_render(session, principal, job_id, for_update=True)
        if render is None:
            raise LifecycleConflict("render_not_started", "Rendering has not started for this job.")
        rows = lifecycle_repository.list_render_deliverables(
            session,
            principal,
            render.id,
            for_update=True,
        )

        if render.fence_token != fence_token:
            raise LifecycleConflict(
                "render_fence_lost",
                "The render lease was superseded; staged output cannot be published.",
            )

        formats = {row.format for row in rows}
        if len(rows) != len(DELIVERABLE_FORMATS) or formats != DELIVERABLE_FORMATS:
            raise LifecycleInvariantError(
                "deliverable_set_incomplete",
                "Exactly one staged MP4, MP3, SRT, CSV and DOCX is required.",
            )
        _require_deliverable_identities(rows, principal, job_id)

        if render.state == "completed" and job.status == JobState.COMPLETED.value:
            if any(row.state != "published" or row.published_at is None for row in rows):
                raise LifecycleInvariantError(
                    "deliverable_set_inconsistent",
                    "The completed render does not have five published deliverables.",
                )
            event = session.execute(
                sa.select(JobEvent).where(
                    JobEvent.organization_id == principal.organization_id,
                    JobEvent.job_id == job_id,
                    JobEvent.event_type == "job.completed",
                )
            ).scalar_one_or_none()
            if event is None:
                raise LifecycleInvariantError(
                    "completion_event_missing",
                    "The completed render has no completion outbox event.",
                )
            ordered = tuple(sorted(rows, key=lambda row: DELIVERABLE_ORDER[row.format]))
            session.commit()
            return PublishResult(render, ordered, event, True)

        now = datetime.now(UTC)
        if (
            render.state != "rendering"
            or job.status != JobState.EXPORTING.value
            or render.worker_id != worker_id
            or render.lease_expires_at is None
            or render.lease_expires_at <= now
        ):
            raise LifecycleConflict(
                "render_publish_conflict",
                "Render output cannot be published in its current state.",
            )
        if any(row.state != "staged" or row.published_at is not None for row in rows):
            raise LifecycleInvariantError(
                "deliverable_set_inconsistent",
                "Only one complete staged deliverable set can be published.",
            )

        ordered = tuple(sorted(rows, key=lambda row: DELIVERABLE_ORDER[row.format]))
        for row in ordered:
            row.state = "published"
            row.published_at = now

        render.state = "completed"
        render.completed_at = now
        render.worker_id = None
        render.lease_expires_at = None
        render.integrity_manifest = {
            "schemaVersion": 1,
            "deliverableCount": len(ordered),
            "deliverables": [
                {
                    "id": str(row.id),
                    "format": row.format,
                    "versionId": row.version_id,
                    "sizeBytes": row.size_bytes,
                    "sha256": row.checksum_sha256,
                }
                for row in ordered
            ],
        }
        render.updated_at = now

        validate_transition(JobState(job.status), JobState.COMPLETED)
        job.status = JobState.COMPLETED.value
        job.version += 1
        job.progress = 100
        job.stage = "complete"
        job.completed_at = now
        job.worker_id = None
        job.lease_expires_at = None
        job.updated_at = now

        event_id = uuid.uuid4()
        event = JobEvent(
            id=event_id,
            organization_id=principal.organization_id,
            job_id=job.id,
            event_type="job.completed",
            job_version=job.version,
            payload={
                "id": str(event_id),
                "type": "job.completed",
                "jobId": str(job.id),
                "state": "completed",
                "occurredAt": _iso(now),
            },
            occurred_at=now,
            available_at=now,
        )
        session.add(event)
        # Publication and journal release share one commit.  A failed commit
        # therefore leaves the exact-version cleanup identities retryable;
        # a successful commit leaves the published Deliverable rows as the
        # sole durable references and the janitor can never select them.
        session.execute(
            sa.delete(RenderAttemptArtifact).where(
                RenderAttemptArtifact.organization_id == principal.organization_id,
                RenderAttemptArtifact.render_id == render.id,
                RenderAttemptArtifact.fence_token == fence_token,
            )
        )
        session.commit()
        return PublishResult(render, ordered, event, False)
    except Exception:
        session.rollback()
        raise
