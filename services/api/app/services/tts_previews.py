"""Tenant-safe lifecycle for asynchronous per-scene TTS previews.

The API only persists bounded inputs. Provider execution and S3 writes happen
in the worker. Publication is fenced by worker/fence identity and by the job
remaining in an open review. Attempt artifacts are journaled by exact S3
version so stale and failed work can be cleaned without a bucket listing or a
key-only delete.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import sqlalchemy as sa
from instadescribe_contracts.provider import (
    TTS_BETA_MAX_ACTIVE_PREVIEWS_PER_ORGANIZATION,
    TTS_BETA_MAX_PREVIEW_ATTEMPTS_PER_REQUEST,
    TTS_BETA_MAX_PREVIEW_REQUESTS_PER_JOB,
    TTS_BETA_MAX_PREVIEW_REQUESTS_PER_ORGANIZATION,
    TTS_BETA_PREVIEW_WINDOW_SECS,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.tenancy import PrincipalContext
from app.domain.states import JobState
from app.models import (
    Artifact,
    Job,
    Organization,
    Review,
    TtsPreview,
    TtsPreviewArtifact,
)

PREVIEW_RETENTION = timedelta(seconds=TTS_BETA_PREVIEW_WINDOW_SECS)
PREVIEW_MAX_ACTIVE_PER_ORGANIZATION = TTS_BETA_MAX_ACTIVE_PREVIEWS_PER_ORGANIZATION
PREVIEW_MAX_ATTEMPTS = TTS_BETA_MAX_PREVIEW_ATTEMPTS_PER_REQUEST
PREVIEW_MAX_REQUESTS_PER_JOB = TTS_BETA_MAX_PREVIEW_REQUESTS_PER_JOB
PREVIEW_MAX_REQUESTS_PER_ORGANIZATION = TTS_BETA_MAX_PREVIEW_REQUESTS_PER_ORGANIZATION
PREVIEW_CONTENT_TYPE = "audio/mpeg"
PREVIEW_MAX_OUTPUT_BYTES = 10 * 1024 * 1024
PREVIEW_VOICES = frozenset({"onyx", "nova", "alloy", "shimmer", "echo", "fable"})


class PreviewServiceError(Exception):
    pass


class PreviewNotFound(PreviewServiceError):
    pass


class PreviewConflict(PreviewServiceError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


class PreviewInvariantError(PreviewServiceError):
    pass


@dataclass(frozen=True, slots=True)
class PreviewArtifactSpec:
    object_key: str
    version_id: str
    size_bytes: int
    checksum_sha256: str


def preview_request_hash(*, scene_id: str, text: str, voice: str, speed: Decimal) -> str:
    canonical = json.dumps(
        {
            "sceneId": scene_id,
            "speed": format(speed, ".2f"),
            "text": text,
            "voice": voice,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def preview_object_key(preview: TtsPreview, fence_token: int) -> str:
    return (
        f"previews/orgs/{preview.organization_id}/jobs/{preview.job_id}/"
        f"requests/{preview.id}/attempts/{fence_token}/narration.mp3"
    )


def _valid_open_review(preview_table=TtsPreview):
    return sa.and_(
        sa.exists().where(
            Job.organization_id == preview_table.organization_id,
            Job.id == preview_table.job_id,
            Job.status == JobState.READY_FOR_REVIEW.value,
        ),
        sa.exists().where(
            Review.organization_id == preview_table.organization_id,
            Review.job_id == preview_table.job_id,
            Review.state == "open",
        ),
    )


def _open_review_exists(
    session: Session,
    organization_id: uuid.UUID,
    job_id: uuid.UUID,
) -> bool:
    return bool(
        session.scalar(
            sa.select(
                sa.and_(
                    sa.exists().where(
                        Job.organization_id == organization_id,
                        Job.id == job_id,
                        Job.status == JobState.READY_FOR_REVIEW.value,
                    ),
                    sa.exists().where(
                        Review.organization_id == organization_id,
                        Review.job_id == job_id,
                        Review.state == "open",
                    ),
                )
            )
        )
    )


def create_preview(
    session: Session,
    principal: PrincipalContext,
    job_id: uuid.UUID,
    *,
    scene_id: str,
    text: str,
    voice: str,
    speed: Decimal,
) -> TtsPreview:
    """Create one queued request while serializing organization capacity."""

    # The organization row is a stable tenant mutex. It makes the small
    # active-preview capacity check race-safe without a count-then-insert gap.
    organization = session.execute(
        sa.select(Organization.id)
        .where(
            Organization.id == principal.organization_id,
            Organization.is_active.is_(True),
        )
        .with_for_update()
    ).scalar_one_or_none()
    if organization is None:
        raise PreviewNotFound

    job = session.execute(
        sa.select(Job)
        .where(
            Job.organization_id == principal.organization_id,
            Job.id == job_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if job is None:
        raise PreviewNotFound
    review = session.execute(
        sa.select(Review).where(
            Review.organization_id == principal.organization_id,
            Review.job_id == job_id,
        )
    ).scalar_one_or_none()
    if job.status != JobState.READY_FOR_REVIEW.value or review is None or review.state != "open":
        raise PreviewConflict(
            "preview_not_available",
            "A TTS preview is available only while this job review is open.",
        )

    scenes_artifact = session.execute(
        sa.select(Artifact).where(
            Artifact.organization_id == principal.organization_id,
            Artifact.job_id == job_id,
            Artifact.artifact_type == "scenes_json",
        )
    ).scalar_one_or_none()
    metadata = scenes_artifact.meta if scenes_artifact is not None else None
    scene_ids = metadata.get("scene_ids") if isinstance(metadata, dict) else None
    if not isinstance(scene_ids, list) or scene_id not in scene_ids:
        # A canonical-but-foreign scene is masked exactly like a missing one.
        raise PreviewNotFound

    # Count every durable request, including terminal failures and cancelled
    # work. Provider outcome must not refund spend capacity. The organization
    # mutex above serializes both rolling counts with the insert, closing the
    # count-then-insert race across every job in this tenant.
    now = datetime.now(UTC)
    rolling_start = now - PREVIEW_RETENTION
    job_request_count = session.scalar(
        sa.select(sa.func.count())
        .select_from(TtsPreview)
        .where(
            TtsPreview.organization_id == principal.organization_id,
            TtsPreview.job_id == job_id,
            TtsPreview.created_at >= rolling_start,
        )
    )
    if int(job_request_count or 0) >= PREVIEW_MAX_REQUESTS_PER_JOB:
        raise PreviewConflict(
            "tts_preview_job_limit_exceeded",
            "This job has reached its rolling 24-hour TTS preview request limit.",
        )
    organization_request_count = session.scalar(
        sa.select(sa.func.count())
        .select_from(TtsPreview)
        .where(
            TtsPreview.organization_id == principal.organization_id,
            TtsPreview.created_at >= rolling_start,
        )
    )
    if int(organization_request_count or 0) >= PREVIEW_MAX_REQUESTS_PER_ORGANIZATION:
        raise PreviewConflict(
            "tts_preview_organization_limit_exceeded",
            "This organization has reached its rolling 24-hour TTS preview request limit.",
        )

    active_count = session.scalar(
        sa.select(sa.func.count())
        .select_from(TtsPreview)
        .where(
            TtsPreview.organization_id == principal.organization_id,
            TtsPreview.state.in_(("queued", "rendering")),
        )
    )
    if int(active_count or 0) >= PREVIEW_MAX_ACTIVE_PER_ORGANIZATION:
        raise PreviewConflict(
            "preview_capacity_exceeded",
            "This organization already has the maximum number of active previews.",
        )
    if session.scalar(
        sa.select(
            sa.exists().where(
                TtsPreview.organization_id == principal.organization_id,
                TtsPreview.job_id == job_id,
                TtsPreview.scene_id == scene_id,
                TtsPreview.state.in_(("queued", "rendering")),
            )
        )
    ):
        raise PreviewConflict(
            "preview_in_progress",
            "A TTS preview for this scene is already in progress.",
        )

    preview = TtsPreview(
        organization_id=principal.organization_id,
        job_id=job_id,
        requested_by_principal_id=principal.principal_id,
        scene_id=scene_id,
        text=text,
        voice=voice,
        speed=speed,
        request_hash=preview_request_hash(
            scene_id=scene_id,
            text=text,
            voice=voice,
            speed=speed,
        ),
        expires_at=now + PREVIEW_RETENTION,
    )
    session.add(preview)
    session.flush()
    return preview


def get_preview(
    session: Session,
    principal: PrincipalContext,
    preview_id: uuid.UUID,
) -> TtsPreview:
    preview = session.execute(
        sa.select(TtsPreview).where(
            TtsPreview.organization_id == principal.organization_id,
            TtsPreview.id == preview_id,
            TtsPreview.expires_at > sa.func.now(),
        )
    ).scalar_one_or_none()
    if preview is None:
        raise PreviewNotFound
    return preview


def get_preview_content(
    session: Session,
    principal: PrincipalContext,
    preview_id: uuid.UUID,
) -> TtsPreview:
    preview = get_preview(session, principal, preview_id)
    if (
        preview.state != "completed"
        or not preview.object_key
        or not preview.version_id
        or preview.content_type != PREVIEW_CONTENT_TYPE
    ):
        raise PreviewConflict("preview_not_ready", "The TTS preview is not ready.")
    if not _open_review_exists(session, principal.organization_id, preview.job_id):
        raise PreviewConflict(
            "preview_not_available",
            "A TTS preview is available only while this job review is open.",
        )
    return preview


def poll_preview_candidate(session: Session) -> tuple[uuid.UUID, uuid.UUID] | None:
    row = session.execute(
        sa.select(TtsPreview.organization_id, TtsPreview.id)
        .where(
            TtsPreview.expires_at > sa.func.now(),
            TtsPreview.attempt_count < PREVIEW_MAX_ATTEMPTS,
            _valid_open_review(),
            sa.or_(
                TtsPreview.state == "queued",
                sa.and_(
                    TtsPreview.state == "rendering",
                    TtsPreview.lease_expires_at.is_not(None),
                    TtsPreview.lease_expires_at <= sa.func.now(),
                ),
            ),
        )
        .order_by(TtsPreview.created_at, TtsPreview.id)
        .limit(1)
    ).one_or_none()
    return None if row is None else (row.organization_id, row.id)


def claim_preview(
    session: Session,
    organization_id: uuid.UUID,
    preview_id: uuid.UUID,
    *,
    worker_id: str,
    lease_expires_at: datetime,
) -> TtsPreview:
    now = datetime.now(UTC)
    preview = session.execute(
        sa.update(TtsPreview)
        .where(
            TtsPreview.organization_id == organization_id,
            TtsPreview.id == preview_id,
            TtsPreview.expires_at > now,
            TtsPreview.attempt_count < PREVIEW_MAX_ATTEMPTS,
            _valid_open_review(),
            sa.or_(
                TtsPreview.state == "queued",
                sa.and_(
                    TtsPreview.state == "rendering",
                    TtsPreview.lease_expires_at.is_not(None),
                    TtsPreview.lease_expires_at <= now,
                ),
            ),
        )
        .values(
            state="rendering",
            fence_token=TtsPreview.fence_token + 1,
            attempt_count=TtsPreview.attempt_count + 1,
            worker_id=worker_id,
            lease_expires_at=lease_expires_at,
            started_at=sa.func.coalesce(TtsPreview.started_at, now),
            updated_at=now,
        )
        .returning(TtsPreview)
    ).scalar_one_or_none()
    if preview is None:
        session.rollback()
        raise PreviewConflict("preview_claim_lost", "The TTS preview claim was lost.")
    session.commit()
    return preview


def renew_preview_lease(
    session: Session,
    organization_id: uuid.UUID,
    preview_id: uuid.UUID,
    *,
    worker_id: str,
    fence_token: int,
    lease_expires_at: datetime,
) -> bool:
    result = session.execute(
        sa.update(TtsPreview)
        .where(
            TtsPreview.organization_id == organization_id,
            TtsPreview.id == preview_id,
            TtsPreview.state == "rendering",
            TtsPreview.worker_id == worker_id,
            TtsPreview.fence_token == fence_token,
            TtsPreview.expires_at > sa.func.now(),
            _valid_open_review(),
        )
        .values(lease_expires_at=lease_expires_at, updated_at=sa.func.now())
    )
    if result.rowcount != 1:
        session.rollback()
        return False
    session.commit()
    return True


def record_preview_artifact(
    session: Session,
    preview: TtsPreview,
    *,
    fence_token: int,
    object_key: str,
    version_id: str,
) -> None:
    expected_key = preview_object_key(preview, fence_token)
    if object_key != expected_key or not version_id or fence_token < 1:
        raise PreviewInvariantError("invalid preview artifact identity")
    values = {
        "id": uuid.uuid4(),
        "organization_id": preview.organization_id,
        "job_id": preview.job_id,
        "preview_id": preview.id,
        "fence_token": fence_token,
        "object_key": object_key,
        "version_id": version_id,
    }
    statement = (
        pg_insert(TtsPreviewArtifact)
        .values(**values)
        .on_conflict_do_nothing(index_elements=("organization_id", "preview_id", "fence_token"))
        .returning(TtsPreviewArtifact.id)
    )
    inserted = session.execute(statement).scalar_one_or_none()
    if inserted is None:
        existing = session.execute(
            sa.select(TtsPreviewArtifact).where(
                TtsPreviewArtifact.organization_id == preview.organization_id,
                TtsPreviewArtifact.preview_id == preview.id,
                TtsPreviewArtifact.fence_token == fence_token,
            )
        ).scalar_one_or_none()
        if (
            existing is None
            or existing.object_key != object_key
            or existing.version_id != version_id
        ):
            session.rollback()
            raise PreviewInvariantError("conflicting preview artifact identity")
    session.commit()


def publish_preview(
    session: Session,
    organization_id: uuid.UUID,
    preview_id: uuid.UUID,
    *,
    worker_id: str,
    fence_token: int,
    artifact: PreviewArtifactSpec,
) -> bool:
    preview = session.execute(
        sa.select(TtsPreview)
        .where(
            TtsPreview.organization_id == organization_id,
            TtsPreview.id == preview_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if (
        preview is None
        or preview.state != "rendering"
        or preview.worker_id != worker_id
        or preview.fence_token != fence_token
        or preview.expires_at <= datetime.now(UTC)
        or artifact.object_key != preview_object_key(preview, fence_token)
        or artifact.size_bytes < 1
        or artifact.size_bytes > PREVIEW_MAX_OUTPUT_BYTES
    ):
        session.rollback()
        return False
    if not _open_review_exists(session, organization_id, preview.job_id):
        session.rollback()
        return False
    journal = session.execute(
        sa.select(TtsPreviewArtifact).where(
            TtsPreviewArtifact.organization_id == organization_id,
            TtsPreviewArtifact.preview_id == preview_id,
            TtsPreviewArtifact.fence_token == fence_token,
            TtsPreviewArtifact.object_key == artifact.object_key,
            TtsPreviewArtifact.version_id == artifact.version_id,
        )
    ).scalar_one_or_none()
    if journal is None:
        session.rollback()
        raise PreviewInvariantError("preview artifact was not journaled")
    now = datetime.now(UTC)
    preview.state = "completed"
    preview.worker_id = None
    preview.lease_expires_at = None
    preview.object_key = artifact.object_key
    preview.version_id = artifact.version_id
    preview.content_type = PREVIEW_CONTENT_TYPE
    preview.size_bytes = artifact.size_bytes
    preview.checksum_sha256 = artifact.checksum_sha256
    preview.finished_at = now
    preview.updated_at = now
    session.delete(journal)
    session.commit()
    return True


def fail_preview(
    session: Session,
    organization_id: uuid.UUID,
    preview_id: uuid.UUID,
    *,
    worker_id: str,
    fence_token: int,
    error_code: str,
    error_message: str,
) -> bool:
    now = datetime.now(UTC)
    result = session.execute(
        sa.update(TtsPreview)
        .where(
            TtsPreview.organization_id == organization_id,
            TtsPreview.id == preview_id,
            TtsPreview.state == "rendering",
            TtsPreview.worker_id == worker_id,
            TtsPreview.fence_token == fence_token,
        )
        .values(
            state="failed",
            worker_id=None,
            lease_expires_at=None,
            error_code=error_code[:80],
            error_message=error_message[:200],
            finished_at=now,
            updated_at=now,
        )
    )
    if result.rowcount != 1:
        session.rollback()
        return False
    session.commit()
    return True


def cancel_invalid_previews(session: Session) -> int:
    """Invalidate expired or no-longer-reviewable work in one transaction."""

    now = datetime.now(UTC)
    result = session.execute(
        sa.update(TtsPreview)
        .where(
            TtsPreview.state.in_(("queued", "rendering")),
            sa.or_(
                TtsPreview.expires_at <= now,
                sa.not_(_valid_open_review()),
            ),
        )
        .values(
            state="cancelled",
            fence_token=TtsPreview.fence_token + 1,
            worker_id=None,
            lease_expires_at=None,
            finished_at=now,
            updated_at=now,
        )
    )
    session.commit()
    return int(result.rowcount or 0)


def fail_exhausted_previews(session: Session) -> int:
    now = datetime.now(UTC)
    result = session.execute(
        sa.update(TtsPreview)
        .where(
            TtsPreview.state == "rendering",
            TtsPreview.attempt_count >= PREVIEW_MAX_ATTEMPTS,
            TtsPreview.lease_expires_at.is_not(None),
            TtsPreview.lease_expires_at <= now,
        )
        .values(
            state="failed",
            worker_id=None,
            lease_expires_at=None,
            error_code="preview_retry_exhausted",
            error_message="The TTS preview could not be generated.",
            finished_at=now,
            updated_at=now,
        )
    )
    session.commit()
    return int(result.rowcount or 0)


def orphaned_preview_artifact_condition():
    """Rows that are neither the current live attempt nor published output."""

    return sa.not_(
        sa.or_(
            sa.and_(
                TtsPreview.state == "rendering",
                TtsPreview.fence_token == TtsPreviewArtifact.fence_token,
                TtsPreview.worker_id.is_not(None),
                TtsPreview.lease_expires_at.is_not(None),
                TtsPreview.lease_expires_at > sa.func.now(),
            ),
            sa.and_(
                TtsPreview.state == "completed",
                TtsPreview.object_key == TtsPreviewArtifact.object_key,
                TtsPreview.version_id == TtsPreviewArtifact.version_id,
            ),
        )
    )
