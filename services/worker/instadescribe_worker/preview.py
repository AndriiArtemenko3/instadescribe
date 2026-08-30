"""Fenced, worker-only execution for per-scene TTS previews.

The browser never receives provider credentials and the API never calls a TTS
provider. This worker polls durable PostgreSQL requests, synthesizes one line,
uploads an attempt-scoped object, journals the exact returned S3 VersionId and
publishes only while the same job review remains open.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import boto3
import sqlalchemy as sa
from app.models import TtsPreview, TtsPreviewArtifact
from app.services.tts_previews import (
    PREVIEW_CONTENT_TYPE,
    PREVIEW_MAX_OUTPUT_BYTES,
    PreviewArtifactSpec,
    PreviewConflict,
    cancel_invalid_previews,
    claim_preview,
    fail_exhausted_previews,
    fail_preview,
    orphaned_preview_artifact_condition,
    poll_preview_candidate,
    preview_object_key,
    publish_preview,
    record_preview_artifact,
    renew_preview_lease,
)
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from instadescribe_worker.config import WorkerSettings, get_worker_settings
from instadescribe_worker.db import get_sessionmaker
from instadescribe_worker.executor import shutdown_requested
from instadescribe_worker.logging import log

PreviewSynthesizer = Callable[[str, str, float, Path], Path]
SessionFactory = Callable[[], Session]
_CLEANUP_BATCH = 20


class PreviewOwnershipLost(Exception):
    pass


class PreviewWorkerFailure(Exception):
    def __init__(self, code: str, public_message: str, *, retryable: bool = False) -> None:
        self.code = code[:80]
        self.public_message = public_message[:200]
        self.retryable = retryable
        super().__init__(self.code)


@lru_cache
def _s3():
    settings = get_worker_settings()
    return boto3.client(
        "s3",
        region_name=settings.aws_region,
        endpoint_url=settings.s3_endpoint_internal,
    )


def reset_preview_caches() -> None:
    _s3.cache_clear()


def _lease_deadline(settings: WorkerSettings) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=settings.preview_lease_duration_secs)


def _assert_owned(
    session: Session,
    settings: WorkerSettings,
    preview: TtsPreview,
    *,
    worker_id: str,
    fence_token: int,
) -> None:
    if not renew_preview_lease(
        session,
        preview.organization_id,
        preview.id,
        worker_id=worker_id,
        fence_token=fence_token,
        lease_expires_at=_lease_deadline(settings),
    ):
        raise PreviewOwnershipLost


def _default_synthesizer(settings: WorkerSettings) -> PreviewSynthesizer:
    pipeline_path = str(Path(settings.pipeline_source).resolve())
    if pipeline_path not in sys.path:
        sys.path.insert(0, pipeline_path)
    try:
        from providers.factory import set_active_backend

        set_active_backend(settings.provider)
        from tts_render import adjust_speed, normalise_audio, render_line
    except Exception:
        raise PreviewWorkerFailure(
            "preview_engine_unavailable",
            "The TTS preview engine is unavailable.",
        ) from None

    def synthesize(text: str, voice: str, speed: float, destination: Path) -> Path:
        raw = destination.with_name("raw.mp3")
        normalised = destination.with_name("normalised.mp3")
        try:
            render_line(text, voice, raw)
            if settings.provider == "fake":
                # FakeTTSProvider is self-contained and normally returns a
                # valid MP3. Keep a fail-safe canary fallback for an empty or
                # replaced test provider; pure silence cannot pass two-pass
                # loudnorm, so fake previews apply only the bounded tempo step.
                if not raw.is_file() or raw.stat().st_size == 0:
                    subprocess.run(
                        [
                            "ffmpeg",
                            "-y",
                            "-f",
                            "lavfi",
                            "-i",
                            "anullsrc=r=44100:cl=mono",
                            "-t",
                            "0.4",
                            "-codec:a",
                            "libmp3lame",
                            "-b:a",
                            "56k",
                            str(raw),
                        ],
                        check=True,
                        capture_output=True,
                        timeout=30,
                    )
                adjust_speed(raw, destination, speed)
            else:
                normalise_audio(raw, normalised)
                adjust_speed(normalised, destination, speed)
        except Exception as exc:
            raise PreviewWorkerFailure(
                "preview_generation_failed",
                "The TTS preview could not be generated.",
                retryable=bool(getattr(exc, "retryable", False)),
            ) from None
        return destination

    return synthesize


def _hash_output(path: Path, expected: Path) -> tuple[int, str]:
    if (
        not isinstance(path, Path)
        or path.is_symlink()
        or path.resolve() != expected.resolve()
        or path.parent.resolve() != expected.parent.resolve()
        or not path.is_file()
    ):
        raise PreviewWorkerFailure(
            "preview_output_invalid",
            "The TTS preview engine returned an invalid output.",
        )
    size = 0
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > PREVIEW_MAX_OUTPUT_BYTES:
                raise PreviewWorkerFailure(
                    "preview_output_invalid",
                    "The TTS preview output exceeded the product limit.",
                )
            digest.update(chunk)
    if size < 1:
        raise PreviewWorkerFailure(
            "preview_output_invalid",
            "The TTS preview engine returned an empty output.",
        )
    return size, digest.hexdigest()


def _published_identity(preview: TtsPreview, artifact: TtsPreviewArtifact) -> bool:
    return (
        preview.state == "completed"
        and preview.object_key == artifact.object_key
        and preview.version_id == artifact.version_id
    )


def _delete_artifact_version(
    session: Session,
    settings: WorkerSettings,
    s3,
    preview: TtsPreview,
    artifact: TtsPreviewArtifact,
) -> str:
    if _published_identity(preview, artifact):
        session.delete(artifact)
        session.commit()
        return "published"
    s3.delete_object(
        Bucket=settings.media_bucket,
        Key=artifact.object_key,
        VersionId=artifact.version_id,
    )
    session.delete(artifact)
    session.commit()
    return "deleted"


def cleanup_orphaned_preview_artifacts(
    session: Session,
    settings: WorkerSettings,
    s3,
    *,
    limit: int = _CLEANUP_BATCH,
) -> int:
    """Delete a bounded number of exact orphan versions, never a key/prefix."""

    if not 1 <= limit <= 100:
        raise ValueError("preview cleanup limit must be between 1 and 100")
    cleaned = 0
    for _ in range(limit):
        row = session.execute(
            sa.select(TtsPreviewArtifact, TtsPreview)
            .join(
                TtsPreview,
                sa.and_(
                    TtsPreview.organization_id == TtsPreviewArtifact.organization_id,
                    TtsPreview.id == TtsPreviewArtifact.preview_id,
                ),
            )
            .where(orphaned_preview_artifact_condition())
            .order_by(TtsPreviewArtifact.created_at, TtsPreviewArtifact.id)
            .limit(1)
            .with_for_update(of=(TtsPreview, TtsPreviewArtifact), skip_locked=True)
        ).one_or_none()
        if row is None:
            session.rollback()
            break
        artifact, preview = row
        try:
            result = _delete_artifact_version(session, settings, s3, preview, artifact)
        except Exception as exc:
            session.rollback()
            log(
                "preview_artifact_cleanup_deferred",
                level="warning",
                category="preview-cleanup",
                error_class=type(exc).__name__,
            )
            break
        if result == "deleted":
            cleaned += 1
    return cleaned


def cleanup_expired_previews(
    session: Session,
    settings: WorkerSettings,
    s3,
    *,
    limit: int = _CLEANUP_BATCH,
) -> int:
    """Purge expired published bytes first, then their metadata row."""

    if not 1 <= limit <= 100:
        raise ValueError("preview expiry limit must be between 1 and 100")
    cleaned = 0
    for _ in range(limit):
        preview = session.execute(
            sa.select(TtsPreview)
            .where(
                TtsPreview.state == "completed",
                TtsPreview.expires_at <= sa.func.now(),
                sa.not_(
                    sa.exists().where(
                        TtsPreviewArtifact.organization_id == TtsPreview.organization_id,
                        TtsPreviewArtifact.preview_id == TtsPreview.id,
                    )
                ),
            )
            .order_by(TtsPreview.expires_at, TtsPreview.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if preview is None:
            session.rollback()
            break
        try:
            s3.delete_object(
                Bucket=settings.media_bucket,
                Key=preview.object_key,
                VersionId=preview.version_id,
            )
            session.delete(preview)
            session.commit()
            cleaned += 1
        except Exception as exc:
            session.rollback()
            log(
                "preview_expiry_cleanup_deferred",
                level="warning",
                category="preview-retention",
                error_class=type(exc).__name__,
            )
            break

    # Terminal rows without media are safe to remove only after proving no
    # journal still references an exact version that needs deletion.
    remaining = max(0, limit - cleaned)
    if remaining:
        ids = list(
            session.scalars(
                sa.select(TtsPreview.id)
                .where(
                    TtsPreview.state.in_(("failed", "cancelled")),
                    TtsPreview.expires_at <= sa.func.now(),
                    sa.not_(
                        sa.exists().where(
                            TtsPreviewArtifact.organization_id == TtsPreview.organization_id,
                            TtsPreviewArtifact.preview_id == TtsPreview.id,
                        )
                    ),
                )
                .order_by(TtsPreview.expires_at, TtsPreview.id)
                .limit(remaining)
                .with_for_update(skip_locked=True)
            )
        )
        if ids:
            session.execute(sa.delete(TtsPreview).where(TtsPreview.id.in_(ids)))
            session.commit()
            cleaned += len(ids)
        else:
            session.rollback()
    return cleaned


def _cleanup_uploaded(
    session: Session,
    settings: WorkerSettings,
    s3,
    organization_id: uuid.UUID,
    preview_id: uuid.UUID,
    fence_token: int,
    artifact: PreviewArtifactSpec | None,
) -> None:
    if artifact is None:
        return
    try:
        session.rollback()
        # Keep the same lock order as publish_preview (parent then journal) so
        # stale cleanup cannot deadlock or race a successful publication.
        fresh = session.execute(
            sa.select(TtsPreview)
            .where(
                TtsPreview.organization_id == organization_id,
                TtsPreview.id == preview_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        journal = session.execute(
            sa.select(TtsPreviewArtifact)
            .where(
                TtsPreviewArtifact.organization_id == organization_id,
                TtsPreviewArtifact.preview_id == preview_id,
                TtsPreviewArtifact.fence_token == fence_token,
                TtsPreviewArtifact.object_key == artifact.object_key,
                TtsPreviewArtifact.version_id == artifact.version_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if journal is not None and fresh is not None:
            _delete_artifact_version(session, settings, s3, fresh, journal)
            return
        # A DB journal failure after S3 acknowledged the write still has an
        # in-memory exact identity in this process; delete that version now.
        if fresh is None or not (
            fresh.state == "completed"
            and fresh.object_key == artifact.object_key
            and fresh.version_id == artifact.version_id
        ):
            s3.delete_object(
                Bucket=settings.media_bucket,
                Key=artifact.object_key,
                VersionId=artifact.version_id,
            )
        session.rollback()
    except Exception as exc:
        session.rollback()
        log(
            "preview_artifact_cleanup_deferred",
            level="warning",
            category="preview-cleanup",
            error_class=type(exc).__name__,
        )


def _execute_claimed(
    settings: WorkerSettings,
    session: Session,
    preview: TtsPreview,
    *,
    s3,
    synthesizer: PreviewSynthesizer | None,
) -> str:
    organization_id = preview.organization_id
    preview_id = preview.id
    worker_id = preview.worker_id or ""
    fence_token = preview.fence_token
    artifact: PreviewArtifactSpec | None = None
    try:
        with tempfile.TemporaryDirectory(
            prefix=f"instadescribe-preview-{str(preview.id)[:8]}-",
            dir=settings.workspace_root,
        ) as workspace_name:
            destination = Path(workspace_name) / "narration.mp3"
            _assert_owned(
                session,
                settings,
                preview,
                worker_id=worker_id,
                fence_token=fence_token,
            )
            active_synthesizer = synthesizer or _default_synthesizer(settings)
            output = active_synthesizer(
                preview.text,
                preview.voice,
                float(preview.speed),
                destination,
            )
            _assert_owned(
                session,
                settings,
                preview,
                worker_id=worker_id,
                fence_token=fence_token,
            )
            size, checksum = _hash_output(output, destination)
            object_key = preview_object_key(preview, fence_token)
            try:
                with destination.open("rb") as body:
                    response = s3.put_object(
                        Bucket=settings.media_bucket,
                        Key=object_key,
                        Body=body,
                        ContentType=PREVIEW_CONTENT_TYPE,
                        ServerSideEncryption="AES256",
                    )
            except Exception:
                raise PreviewWorkerFailure(
                    "preview_upload_failed",
                    "The TTS preview could not be stored.",
                    retryable=True,
                ) from None
            version_id = response.get("VersionId") if isinstance(response, dict) else None
            if not isinstance(version_id, str) or not version_id.strip():
                raise PreviewWorkerFailure(
                    "preview_upload_failed",
                    "The TTS preview storage did not return a pinned version.",
                    retryable=True,
                )
            artifact = PreviewArtifactSpec(
                object_key=object_key,
                version_id=version_id,
                size_bytes=size,
                checksum_sha256=checksum,
            )
            record_preview_artifact(
                session,
                preview,
                fence_token=fence_token,
                object_key=object_key,
                version_id=version_id,
            )
            _assert_owned(
                session,
                settings,
                preview,
                worker_id=worker_id,
                fence_token=fence_token,
            )
            if not publish_preview(
                session,
                preview.organization_id,
                preview.id,
                worker_id=worker_id,
                fence_token=fence_token,
                artifact=artifact,
            ):
                raise PreviewOwnershipLost
            return "success"
    except PreviewOwnershipLost:
        _cleanup_uploaded(session, settings, s3, organization_id, preview_id, fence_token, artifact)
        return "stale_preview"
    except PreviewWorkerFailure as exc:
        if exc.retryable:
            _cleanup_uploaded(
                session, settings, s3, organization_id, preview_id, fence_token, artifact
            )
            log(
                "preview_retry_deferred",
                level="warning",
                preview_id=preview_id,
                error_code=exc.code,
            )
            return "infra_error"
        persisted = fail_preview(
            session,
            organization_id,
            preview_id,
            worker_id=worker_id,
            fence_token=fence_token,
            error_code=exc.code,
            error_message=exc.public_message,
        )
        _cleanup_uploaded(session, settings, s3, organization_id, preview_id, fence_token, artifact)
        return "failed" if persisted else "stale_preview"
    except (SQLAlchemyError, BotoCoreError, ClientError) as exc:
        session.rollback()
        _cleanup_uploaded(session, settings, s3, organization_id, preview_id, fence_token, artifact)
        log(
            "preview_infrastructure_unavailable",
            level="error",
            preview_id=preview_id,
            category="preview-infrastructure",
            error_class=type(exc).__name__,
        )
        return "infra_error"
    except Exception as exc:
        session.rollback()
        persisted = fail_preview(
            session,
            organization_id,
            preview_id,
            worker_id=worker_id,
            fence_token=fence_token,
            error_code="preview_internal_error",
            error_message="An unexpected TTS preview failure occurred.",
        )
        _cleanup_uploaded(session, settings, s3, organization_id, preview_id, fence_token, artifact)
        log(
            "preview_internal_error",
            level="error",
            preview_id=preview_id,
            category="preview",
            error_class=type(exc).__name__,
        )
        return "failed" if persisted else "stale_preview"


def run_preview_once(
    settings: WorkerSettings | None = None,
    *,
    session_factory: SessionFactory | None = None,
    s3=None,
    synthesizer: PreviewSynthesizer | None = None,
) -> str:
    """Run maintenance and at most one provider-backed preview request."""

    if shutdown_requested():
        return "shutdown"
    settings = settings or get_worker_settings()
    session_factory = session_factory or get_sessionmaker()
    session = None
    active_s3 = s3 if s3 is not None else _s3()
    try:
        session = session_factory()
        cancel_invalid_previews(session)
        fail_exhausted_previews(session)
        cleanup_orphaned_preview_artifacts(session, settings, active_s3)
        cleanup_expired_previews(session, settings, active_s3)
        candidate = poll_preview_candidate(session)
        if candidate is None:
            session.rollback()
            return "empty"
        organization_id, preview_id = candidate
        worker_id = f"{settings.worker_id}:preview:{uuid.uuid4()}"
        try:
            preview = claim_preview(
                session,
                organization_id,
                preview_id,
                worker_id=worker_id,
                lease_expires_at=_lease_deadline(settings),
            )
        except PreviewConflict:
            return "claim_lost"
        log(
            "preview_claimed",
            preview_id=preview.id,
            worker_label=settings.worker_id,
            fence_token=preview.fence_token,
            attempt=preview.attempt_count,
        )
        return _execute_claimed(
            settings,
            session,
            preview,
            s3=active_s3,
            synthesizer=synthesizer,
        )
    except (SQLAlchemyError, BotoCoreError, ClientError) as exc:
        if session is not None:
            session.rollback()
        log(
            "preview_poll_unavailable",
            level="error",
            category="preview-poll",
            error_class=type(exc).__name__,
        )
        return "infra_error"
    except Exception as exc:
        if session is not None:
            session.rollback()
        log(
            "preview_cycle_internal_error",
            level="error",
            category="preview-cycle",
            error_class=type(exc).__name__,
        )
        return "infra_error"
    finally:
        if session is not None:
            session.close()
