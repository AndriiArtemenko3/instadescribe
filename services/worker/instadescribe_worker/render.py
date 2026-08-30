"""Database-authoritative, fenced review-to-deliverables render execution.

There is deliberately no queue or public event consumer here.  One bounded
cycle polls PostgreSQL for a queued render (or an expired render lease), claims
it through the shared lifecycle service, renders into a disposable workspace,
uploads an attempt-scoped five-file set, and asks the service to publish it
atomically.  S3 bytes from a stale or partial attempt are never public because
only version-pinned Deliverable rows selected by the current fence can publish.
Each acknowledged output version is also durably journaled: terminal/stale
paths delete only those exact versions and a bounded SKIP LOCKED janitor retries
failed cleanup without bucket listing or key-only deletion.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any

import boto3
import instadescribe_contracts
import sqlalchemy as sa
from app.core.tenancy import PrincipalContext
from app.domain.states import JobState
from app.models import (
    Artifact,
    Deliverable,
    Job,
    Project,
    Render,
    RenderAttemptArtifact,
    Review,
    SceneOverride,
)
from app.services.lifecycle import (
    DELIVERABLE_CONTENT_TYPES,
    DELIVERABLE_FILE_NAMES,
    DELIVERABLE_FORMATS,
    LifecycleConflict,
    LifecycleServiceError,
    RenderAttemptsExhausted,
    StagedDeliverableSpec,
    cancel_render_if_job_cancelled,
    claim_render,
    fail_render,
    orphaned_render_attempt_condition,
    publish_staged_deliverables,
    record_render_attempt_artifact,
    renew_render_lease,
    stage_render_deliverables,
)
from botocore.exceptions import BotoCoreError, ClientError
from instadescribe_contracts.provider import TTS_BETA_MAX_APPROVED_SCENES_PER_REVIEW
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from instadescribe_worker.config import WorkerSettings, get_worker_settings
from instadescribe_worker.db import get_sessionmaker
from instadescribe_worker.executor import (
    WorkerShutdownRequested,
    raise_if_shutdown_requested,
    register_current_child,
    shutdown_requested,
    terminate_tree,
    unregister_current_child,
)
from instadescribe_worker.failures import FailureCode, JobFailure
from instadescribe_worker.logging import log
from instadescribe_worker.source import download_source, download_versioned_artifact

Renderer = Callable[..., dict[str, Path]]
SessionFactory = Callable[[], Session]

_JSON_ARTIFACT_LIMIT = 20 * 1024 * 1024
_RENDER_CLEANUP_BATCH = 20
_RENDER_PRINCIPAL_ID = uuid.UUID("00000000-0000-4000-8000-000000000003")
_VOICE_ALIASES = frozenset({"onyx", "nova", "alloy", "shimmer", "echo", "fable"})
_RENDER_CHILD_POLL_SECS = 0.2


class RenderFailureCode(StrEnum):
    INPUT_INVALID = "render_input_invalid"
    SOURCE_UNAVAILABLE = "render_source_unavailable"
    RENDER_FAILED = "render_failed"
    UPLOAD_FAILED = "deliverable_upload_failed"
    INTERNAL_ERROR = "render_internal_error"


class RenderWorkerFailure(Exception):
    def __init__(self, code: RenderFailureCode, public_message: str) -> None:
        self.code = code
        self.public_message = public_message[:200]
        super().__init__(code.value)


class RenderOwnershipLost(Exception):
    pass


class RenderCancelled(RenderOwnershipLost):
    pass


class RenderHeartbeatUnavailable(Exception):
    """The render lease could not be renewed authoritatively."""


class RenderLeaseHeartbeat:
    """Renew a render fence while the renderer is blocked in media tools.

    The renderer can spend many minutes inside a single TTS or FFmpeg call and
    therefore cannot be the clock that protects its own lease. This helper
    uses a dedicated SQLAlchemy session in a daemon thread; the foreground
    session is never shared across threads. Any failed or rejected renewal is
    surfaced before a byte can be uploaded or published.
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        settings: WorkerSettings,
        principal: PrincipalContext,
        job_id: uuid.UUID,
        *,
        worker_id: str,
        fence_token: int,
        interval_secs: float | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._principal = principal
        self._job_id = job_id
        self._worker_id = worker_id
        self._fence_token = fence_token
        self._interval_secs = (
            interval_secs
            if interval_secs is not None
            else float(settings.render_heartbeat_interval_secs)
        )
        if self._interval_secs <= 0:
            raise ValueError("render heartbeat interval must be positive")
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._unavailable = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def lost(self) -> bool:
        return self._lost.is_set()

    @property
    def unavailable(self) -> bool:
        return self._unavailable.is_set()

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("render heartbeat already started")
        self._thread = threading.Thread(
            target=self._run,
            name=f"render-heartbeat-{str(self._job_id)[:8]}",
            daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self._interval_secs):
            session: Session | None = None
            try:
                session = self._session_factory()
                # Bound both row-lock waiting and the heartbeat statement so
                # stop() cannot leave a renewal thread alive past the worker
                # cycle. PostgreSQL is the only supported cloud database.
                session.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
                session.execute(sa.text("SET LOCAL statement_timeout = '5s'"))
                renewed = renew_render_lease(
                    session,
                    self._principal,
                    self._job_id,
                    worker_id=self._worker_id,
                    fence_token=self._fence_token,
                    lease_expires_at=_lease_deadline(self._settings),
                )
            except Exception:
                if session is not None:
                    try:
                        session.rollback()
                    except Exception:
                        pass
                self._unavailable.set()
                return
            finally:
                if session is not None:
                    session.close()
            if not renewed:
                self._lost.set()
                return

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10.0)
            if self._thread.is_alive():
                self._unavailable.set()

    def assert_healthy(self, session: Session) -> None:
        if self.unavailable:
            raise RenderHeartbeatUnavailable
        if self.lost:
            # Resolve cancellation versus a replacement fence through the
            # canonical lifecycle service on the foreground session.
            _assert_owned(
                session,
                self._settings,
                self._principal,
                self._job_id,
                self._worker_id,
                self._fence_token,
            )
            raise RenderOwnershipLost


@dataclass(frozen=True, slots=True)
class RenderCandidate:
    organization_id: uuid.UUID
    job_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class ReviewedSnapshot:
    source_artifact: Artifact
    scenes_artifact: Artifact
    entities_artifact: Artifact
    scenes: list[dict[str, Any]]
    entities_by_id: dict[str, dict[str, Any]]
    project_name: str
    default_voice: str


@lru_cache
def _s3():
    settings = get_worker_settings()
    return boto3.client(
        "s3",
        region_name=settings.aws_region,
        endpoint_url=settings.s3_endpoint_internal,
    )


def reset_render_caches() -> None:
    _s3.cache_clear()


def _principal(organization_id: uuid.UUID) -> PrincipalContext:
    return PrincipalContext(
        organization_id=organization_id,
        principal_id=_RENDER_PRINCIPAL_ID,
        principal_type="worker",
        scopes=frozenset(),
    )


def poll_render_candidate(session: Session) -> RenderCandidate | None:
    """Select one valid tenant/job pair; the following fenced claim arbitrates."""

    row = session.execute(
        sa.select(Render.organization_id, Render.job_id)
        .join(
            Job,
            sa.and_(
                Render.organization_id == Job.organization_id,
                Render.job_id == Job.id,
            ),
        )
        .join(
            Review,
            sa.and_(
                Render.organization_id == Review.organization_id,
                Render.job_id == Review.job_id,
                Render.review_id == Review.id,
            ),
        )
        .where(
            Review.state == "completed",
            sa.or_(
                sa.and_(
                    Render.state == "queued",
                    Job.status == JobState.EXPORT_QUEUED.value,
                ),
                sa.and_(
                    Render.state == "rendering",
                    Job.status == JobState.EXPORTING.value,
                    Render.lease_expires_at.is_not(None),
                    Render.lease_expires_at <= sa.func.now(),
                ),
            ),
        )
        .order_by(Render.created_at, Render.id)
        .limit(1)
    ).one_or_none()
    if row is None:
        return None
    return RenderCandidate(row.organization_id, row.job_id)


def _strict_json(path: Path, label: str):
    def reject_constant(_value: str):
        raise ValueError("non-standard JSON constant")

    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant)
    except Exception:
        raise RenderWorkerFailure(
            RenderFailureCode.INPUT_INVALID,
            f"The immutable {label} artifact is invalid.",
        ) from None


def _artifact_map(
    session: Session, principal: PrincipalContext, job_id: uuid.UUID
) -> dict[str, Artifact]:
    rows = list(
        session.execute(
            sa.select(Artifact)
            .join(Job, Artifact.job_id == Job.id)
            .where(
                Artifact.organization_id == principal.organization_id,
                Job.organization_id == principal.organization_id,
                Job.id == job_id,
                Artifact.retention_state == "active",
                Artifact.artifact_type.in_({"source_video", "scenes_json", "entities_json"}),
            )
        ).scalars()
    )
    by_type = {row.artifact_type: row for row in rows}
    if set(by_type) != {"source_video", "scenes_json", "entities_json"}:
        raise RenderWorkerFailure(
            RenderFailureCode.INPUT_INVALID,
            "The render input artifact set is incomplete.",
        )
    return by_type


def _reviewed_snapshot(
    session: Session,
    principal: PrincipalContext,
    claimed: Render,
    *,
    scenes_payload,
    entities_payload,
) -> ReviewedSnapshot:
    job = session.execute(
        sa.select(Job).where(
            Job.organization_id == principal.organization_id,
            Job.id == claimed.job_id,
            Job.status == JobState.EXPORTING.value,
        )
    ).scalar_one_or_none()
    review = session.execute(
        sa.select(Review).where(
            Review.organization_id == principal.organization_id,
            Review.job_id == claimed.job_id,
            Review.id == claimed.review_id,
            Review.state == "completed",
        )
    ).scalar_one_or_none()
    project = (
        session.execute(
            sa.select(Project)
            .join(
                Job,
                sa.and_(
                    Project.organization_id == Job.organization_id,
                    Project.id == Job.project_id,
                ),
            )
            .where(
                Project.organization_id == principal.organization_id,
                Job.organization_id == principal.organization_id,
                Job.id == claimed.job_id,
            )
        )
        .scalars()
        .one_or_none()
    )
    if job is None or review is None or project is None:
        raise RenderOwnershipLost

    artifacts = _artifact_map(session, principal, claimed.job_id)
    source_artifact = artifacts["source_video"]
    source_meta = source_artifact.meta if isinstance(source_artifact.meta, dict) else {}
    if (
        source_artifact.object_key != job.input_object_key
        or source_artifact.size_bytes != job.input_size_bytes
        or source_artifact.version_id != job.source_version_id
        or source_meta.get("version_id") != job.source_version_id
        or source_meta.get("etag") != job.source_etag
    ):
        raise RenderWorkerFailure(
            RenderFailureCode.INPUT_INVALID,
            "The version-pinned source identity is inconsistent.",
        )

    scenes_artifact = artifacts["scenes_json"]
    manifest = scenes_artifact.meta if isinstance(scenes_artifact.meta, dict) else {}
    scene_ids = manifest.get("scene_ids")
    scene_count = manifest.get("scene_count")
    if (
        not isinstance(scenes_payload, list)
        or not isinstance(scene_ids, list)
        or not scene_ids
        or isinstance(scene_count, bool)
        or not isinstance(scene_count, int)
        or scene_count != len(scene_ids)
        or review.scene_count != scene_count
        or len(scenes_payload) != scene_count
    ):
        raise RenderWorkerFailure(
            RenderFailureCode.INPUT_INVALID,
            "The locked review does not match the immutable scene manifest.",
        )

    parsed_by_id: dict[str, dict[str, Any]] = {}
    for scene in scenes_payload:
        if not isinstance(scene, dict) or not isinstance(scene.get("scene_id"), str):
            raise RenderWorkerFailure(
                RenderFailureCode.INPUT_INVALID,
                "The immutable scene artifact has an invalid scene.",
            )
        scene_id = scene["scene_id"]
        start, end = scene.get("start"), scene.get("end")
        if (
            scene_id in parsed_by_id
            or isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int | float)
            or not isinstance(end, int | float)
            or not math.isfinite(float(start))
            or not math.isfinite(float(end))
            or not float(end) > float(start)
        ):
            raise RenderWorkerFailure(
                RenderFailureCode.INPUT_INVALID,
                "The immutable scene artifact has invalid bounds or identity.",
            )
        parsed_by_id[scene_id] = scene
    if list(parsed_by_id) != scene_ids or len(set(scene_ids)) != len(scene_ids):
        raise RenderWorkerFailure(
            RenderFailureCode.INPUT_INVALID,
            "The immutable scene ordering does not match its manifest.",
        )

    overrides = list(
        session.execute(
            sa.select(SceneOverride)
            .join(Job, SceneOverride.job_id == Job.id)
            .where(
                Job.organization_id == principal.organization_id,
                Job.id == claimed.job_id,
                SceneOverride.job_id == claimed.job_id,
            )
        ).scalars()
    )
    by_scene = {row.scene_id: row for row in overrides}
    if set(by_scene) != set(scene_ids) or any(
        row.review_status not in {"approved", "rejected"} or row.reviewed_at is None
        for row in overrides
    ):
        raise RenderWorkerFailure(
            RenderFailureCode.INPUT_INVALID,
            "The locked review decisions are incomplete or inconsistent.",
        )
    approved = sum(by_scene[scene_id].review_status == "approved" for scene_id in scene_ids)
    rejected = len(scene_ids) - approved
    if approved > TTS_BETA_MAX_APPROVED_SCENES_PER_REVIEW:
        raise RenderWorkerFailure(
            RenderFailureCode.INPUT_INVALID,
            "The locked review exceeds the beta TTS synthesis limit.",
        )
    if (
        review.approved_scene_count != approved
        or review.rejected_scene_count != rejected
        or (approved == 0) != (review.zero_ad_confirmed_at is not None)
    ):
        raise RenderWorkerFailure(
            RenderFailureCode.INPUT_INVALID,
            "The locked review counts are inconsistent.",
        )

    reviewed: list[dict[str, Any]] = []
    for scene_id in scene_ids:
        source_scene = parsed_by_id[scene_id]
        override = by_scene[scene_id]
        reviewed.append(
            {
                **source_scene,
                "text": override.text
                if override.text is not None
                else (source_scene.get("text") or source_scene.get("caption") or ""),
                "voice": override.voice,
                "speed": float(override.speed) if override.speed is not None else 1.0,
                "review_state": override.review_status,
            }
        )

    if not isinstance(entities_payload, list):
        raise RenderWorkerFailure(
            RenderFailureCode.INPUT_INVALID,
            "The immutable entities artifact is invalid.",
        )
    entities_by_id: dict[str, dict[str, Any]] = {}
    for entity in entities_payload:
        entity_id = entity.get("id") if isinstance(entity, dict) else None
        if not isinstance(entity_id, str) or not entity_id or entity_id in entities_by_id:
            raise RenderWorkerFailure(
                RenderFailureCode.INPUT_INVALID,
                "The immutable entities artifact has invalid identity.",
            )
        entities_by_id[entity_id] = entity

    job_settings = job.settings if isinstance(job.settings, dict) else {}
    project_name = job_settings.get("project_name")
    if not isinstance(project_name, str) or not project_name.strip():
        project_name = project.name
    default_voice = job_settings.get("voice", "onyx")
    if not isinstance(default_voice, str) or default_voice not in _VOICE_ALIASES:
        raise RenderWorkerFailure(
            RenderFailureCode.INPUT_INVALID,
            "The locked render voice alias is invalid.",
        )
    return ReviewedSnapshot(
        source_artifact=source_artifact,
        scenes_artifact=scenes_artifact,
        entities_artifact=artifacts["entities_json"],
        scenes=reviewed,
        entities_by_id=entities_by_id,
        project_name=project_name[:200],
        default_voice=default_voice,
    )


def _render_child_command(request_path: Path) -> list[str]:
    """Return the isolated renderer command (a test seam, never client input)."""

    return [
        sys.executable,
        "-I",
        str(Path(__file__).with_name("render_child.py")),
        str(request_path),
    ]


def _render_child_environment(settings: WorkerSettings, workspace: Path) -> dict[str, str]:
    """Build an explicit renderer environment without DB/AWS credentials."""

    environment = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", "/tmp"),
        "TMPDIR": str(workspace),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "PYTHONUNBUFFERED": "1",
        "INSTADESCRIBE_BACKEND": settings.provider,
    }
    if settings.provider == "openai":
        key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else ""
        if not key or key != key.strip():
            raise RenderWorkerFailure(
                RenderFailureCode.RENDER_FAILED,
                "The render engine is unavailable.",
            )
        environment["OPENAI_API_KEY"] = key
    return environment


def _write_render_request(path: Path, payload: dict[str, Any]) -> None:
    """Create the reviewed snapshot request without a world-readable window."""

    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _run_default_renderer(
    settings: WorkerSettings,
    session: Session,
    heartbeat: RenderLeaseHeartbeat,
    *,
    source_video: Path,
    scenes: list[dict[str, Any]],
    entities_by_id: dict[str, dict[str, Any]],
    output_dir: Path,
    project_name: str,
    default_voice: str,
) -> dict[str, Path]:
    """Run the production renderer in a bounded, killable process group.

    The heartbeat can renew only until ``render_timeout_secs`` elapses. A lost
    fence, cancellation, unavailable heartbeat or deadline terminates the
    whole process group (including an active FFmpeg child), escalates from
    TERM to KILL after the configured grace, and reaps the direct child before
    the disposable workspace may be removed.
    """

    raise_if_shutdown_requested()
    workspace = output_dir.parent
    request_path = workspace / "render-request.json"
    stdout_path = workspace / "render-stdout.log"
    stderr_path = workspace / "render-stderr.log"
    _write_render_request(
        request_path,
        {
            "pipelineSource": str(Path(settings.pipeline_source).resolve()),
            "contractsSource": str(Path(instadescribe_contracts.__file__).resolve().parent.parent),
            "provider": settings.provider,
            "sourceVideo": str(source_video),
            "scenes": scenes,
            "entitiesById": entities_by_id,
            "outputDir": str(output_dir),
            "projectName": project_name,
            "defaultVoice": default_voice,
        },
    )

    deadline = time.monotonic() + settings.render_timeout_secs
    exit_code: int | None = None
    timed_out = False
    child: subprocess.Popen | None = None
    try:
        with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
            child = subprocess.Popen(
                _render_child_command(request_path),
                cwd=str(workspace),
                env=_render_child_environment(settings, workspace),
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
            registered = False
            try:
                register_current_child(child)
                registered = True
                # Closes SIGTERM landing after Popen but before registration:
                # the handler's sticky flag survives even if it saw no child.
                raise_if_shutdown_requested()
                while True:
                    raise_if_shutdown_requested()
                    # This check is deliberately outside the child. The
                    # renderer cannot extend or decide its own DB authority.
                    heartbeat.assert_healthy(session)
                    exit_code = child.poll()
                    if exit_code is not None:
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        timed_out = True
                        break
                    time.sleep(min(_RENDER_CHILD_POLL_SECS, remaining))
            finally:
                terminate_tree(child, settings.grace_secs)
                if registered:
                    unregister_current_child(child)
        raise_if_shutdown_requested()
    except (RenderOwnershipLost, RenderHeartbeatUnavailable, WorkerShutdownRequested):
        raise
    except RenderWorkerFailure:
        raise
    except Exception:
        raise RenderWorkerFailure(
            RenderFailureCode.RENDER_FAILED,
            "The render engine is unavailable.",
        ) from None

    if timed_out:
        log(
            "render_deadline_exceeded",
            level="warning",
            timeout_secs=settings.render_timeout_secs,
            category="render-deadline",
        )
        raise RenderWorkerFailure(
            RenderFailureCode.RENDER_FAILED,
            "The five-format render exceeded its processing deadline.",
        )
    if exit_code != 0:
        raise RenderWorkerFailure(
            RenderFailureCode.RENDER_FAILED,
            "The five-format render failed.",
        )
    return {
        format_name: output_dir / filename
        for format_name, filename in DELIVERABLE_FILE_NAMES.items()
    }


def _lease_deadline(settings: WorkerSettings) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=settings.render_lease_duration_secs)


def _assert_owned(
    session: Session,
    settings: WorkerSettings,
    principal: PrincipalContext,
    job_id: uuid.UUID,
    worker_id: str,
    fence_token: int,
) -> None:
    raise_if_shutdown_requested()
    if renew_render_lease(
        session,
        principal,
        job_id,
        worker_id=worker_id,
        fence_token=fence_token,
        lease_expires_at=_lease_deadline(settings),
    ):
        return
    if cancel_render_if_job_cancelled(
        session,
        principal,
        job_id,
        worker_id=worker_id,
        fence_token=fence_token,
    ):
        raise RenderCancelled
    raise RenderOwnershipLost


def _hash_file(path: Path) -> tuple[int, str]:
    size = 0
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _validated_outputs(outputs: dict[str, Path], output_dir: Path) -> dict[str, Path]:
    if set(outputs) != DELIVERABLE_FORMATS:
        raise RenderWorkerFailure(
            RenderFailureCode.RENDER_FAILED,
            "The render engine did not produce all five deliverables.",
        )
    expected_root = output_dir.resolve()
    validated: dict[str, Path] = {}
    for format_name, filename in DELIVERABLE_FILE_NAMES.items():
        path = outputs.get(format_name)
        expected = (output_dir / filename).resolve()
        if (
            not isinstance(path, Path)
            or path.is_symlink()
            or path.resolve() != expected
            or expected.parent != expected_root
            or not path.is_file()
        ):
            raise RenderWorkerFailure(
                RenderFailureCode.RENDER_FAILED,
                "The render engine returned an invalid output path.",
            )
        validated[format_name] = path
    return validated


def _published_identity_exists(
    session: Session,
    principal: PrincipalContext,
    artifact: StagedDeliverableSpec,
) -> bool:
    # Any non-staged row is a public or historical publication tombstone
    # (including the future ``purged`` state) and must never be deleted here.
    return bool(
        session.scalar(
            sa.select(
                sa.exists().where(
                    Deliverable.organization_id == principal.organization_id,
                    Deliverable.object_key == artifact.object_key,
                    Deliverable.version_id == artifact.version_id,
                    Deliverable.state != "staged",
                )
            )
        )
    )


def _discard_cleanup_identity(
    session: Session,
    principal: PrincipalContext,
    artifact: StagedDeliverableSpec,
) -> None:
    session.execute(
        sa.delete(RenderAttemptArtifact).where(
            RenderAttemptArtifact.organization_id == principal.organization_id,
            RenderAttemptArtifact.object_key == artifact.object_key,
            RenderAttemptArtifact.version_id == artifact.version_id,
        )
    )


def _delete_unpublished_version(
    session: Session,
    settings: WorkerSettings,
    principal: PrincipalContext,
    s3,
    artifact: StagedDeliverableSpec,
) -> str:
    """Delete one exact S3 version only after a fresh publication check."""

    if _published_identity_exists(session, principal, artifact):
        _discard_cleanup_identity(session, principal, artifact)
        session.commit()
        return "published"

    s3.delete_object(
        Bucket=settings.media_bucket,
        Key=artifact.object_key,
        VersionId=artifact.version_id,
    )
    session.execute(
        sa.delete(Deliverable).where(
            Deliverable.organization_id == principal.organization_id,
            Deliverable.object_key == artifact.object_key,
            Deliverable.version_id == artifact.version_id,
            Deliverable.state == "staged",
        )
    )
    _discard_cleanup_identity(session, principal, artifact)
    session.commit()
    return "deleted"


def _cleanup_uploaded_versions(
    session: Session,
    settings: WorkerSettings,
    principal: PrincipalContext,
    s3,
    uploaded: list[StagedDeliverableSpec],
    *,
    reason: str,
) -> None:
    """Best-effort immediate cleanup; failures remain durable for janitor retry."""

    if not uploaded:
        return
    deleted = 0
    protected = 0
    deferred = 0
    for artifact in uploaded:
        try:
            session.rollback()
            result = _delete_unpublished_version(session, settings, principal, s3, artifact)
            if result == "published":
                protected += 1
            else:
                deleted += 1
        except Exception as exc:
            session.rollback()
            deferred += 1
            log(
                "render_artifact_cleanup_deferred",
                level="warning",
                reason=reason,
                error_class=type(exc).__name__,
            )
    log(
        "render_artifact_cleanup_complete",
        reason=reason,
        deleted=deleted,
        protected=protected,
        deferred=deferred,
    )


def cleanup_orphaned_render_attempts(
    session: Session,
    settings: WorkerSettings,
    s3,
    *,
    limit: int = _RENDER_CLEANUP_BATCH,
) -> int:
    """Retry a bounded set of terminal/stale exact-version cleanup journals.

    Each row is claimed with ``FOR UPDATE SKIP LOCKED``.  S3 never receives a
    key-only delete and any failed exact-version deletion leaves the journal
    row intact for a later worker cycle.
    """

    if not 1 <= limit <= 100:
        raise ValueError("render cleanup limit must be between 1 and 100")
    cleaned = 0
    for _ in range(limit):
        row = session.execute(
            sa.select(RenderAttemptArtifact)
            .join(
                Render,
                sa.and_(
                    Render.organization_id == RenderAttemptArtifact.organization_id,
                    Render.id == RenderAttemptArtifact.render_id,
                ),
            )
            .where(orphaned_render_attempt_condition())
            .order_by(RenderAttemptArtifact.created_at, RenderAttemptArtifact.id)
            .limit(1)
            .with_for_update(of=RenderAttemptArtifact, skip_locked=True)
        ).scalar_one_or_none()
        if row is None:
            session.rollback()
            break
        principal = _principal(row.organization_id)
        artifact = StagedDeliverableSpec(
            format=row.format,
            object_key=row.object_key,
            version_id=row.version_id,
            content_type=DELIVERABLE_CONTENT_TYPES[row.format],
            size_bytes=0,
            checksum_sha256="0" * 64,
        )
        try:
            result = _delete_unpublished_version(session, settings, principal, s3, artifact)
        except Exception as exc:
            session.rollback()
            log(
                "render_artifact_janitor_deferred",
                level="warning",
                category="render-cleanup",
                error_class=type(exc).__name__,
            )
            break
        if result == "deleted":
            cleaned += 1
    if cleaned:
        log("render_artifact_janitor_complete", deleted=cleaned, limit=limit)
    return cleaned


def _upload_outputs(
    session: Session,
    settings: WorkerSettings,
    principal: PrincipalContext,
    job_id: uuid.UUID,
    worker_id: str,
    fence_token: int,
    s3,
    outputs: dict[str, Path],
    uploaded: list[StagedDeliverableSpec],
) -> tuple[StagedDeliverableSpec, ...]:
    prefix = f"deliverables/orgs/{principal.organization_id}/jobs/{job_id}/attempts/{fence_token}"
    for format_name in ("mp4", "mp3", "srt", "csv", "docx"):
        _assert_owned(session, settings, principal, job_id, worker_id, fence_token)
        path = outputs[format_name]
        size, checksum = _hash_file(path)
        object_key = f"{prefix}/{DELIVERABLE_FILE_NAMES[format_name]}"
        try:
            with path.open("rb") as body:
                response = s3.put_object(
                    Bucket=settings.media_bucket,
                    Key=object_key,
                    Body=body,
                    ContentType=DELIVERABLE_CONTENT_TYPES[format_name],
                    ServerSideEncryption="AES256",
                )
        except Exception:
            raise RenderWorkerFailure(
                RenderFailureCode.UPLOAD_FAILED,
                "A deliverable upload failed.",
            ) from None
        version_id = response.get("VersionId") if isinstance(response, dict) else None
        if not isinstance(version_id, str) or not version_id.strip():
            raise RenderWorkerFailure(
                RenderFailureCode.UPLOAD_FAILED,
                "A deliverable upload did not return a pinned object version.",
            )
        artifact = StagedDeliverableSpec(
            format=format_name,
            object_key=object_key,
            version_id=version_id,
            content_type=DELIVERABLE_CONTENT_TYPES[format_name],
            size_bytes=size,
            checksum_sha256=checksum,
        )
        uploaded.append(artifact)
        record_render_attempt_artifact(
            session,
            principal,
            job_id,
            worker_id=worker_id,
            fence_token=fence_token,
            artifact=artifact,
        )
        post_upload_size, post_upload_checksum = _hash_file(path)
        if (post_upload_size, post_upload_checksum) != (size, checksum):
            raise RenderWorkerFailure(
                RenderFailureCode.RENDER_FAILED,
                "A local deliverable changed during upload.",
            )
    _assert_owned(session, settings, principal, job_id, worker_id, fence_token)
    return tuple(uploaded)


def _map_job_failure(failure: JobFailure) -> RenderWorkerFailure:
    if failure.code in {FailureCode.SOURCE_DOWNLOAD_FAILED}:
        return RenderWorkerFailure(
            RenderFailureCode.SOURCE_UNAVAILABLE,
            "A version-pinned render input could not be downloaded.",
        )
    return RenderWorkerFailure(
        RenderFailureCode.INPUT_INVALID,
        "A version-pinned render input failed integrity validation.",
    )


def _execute_claimed(
    settings: WorkerSettings,
    session: Session,
    claimed: Render,
    *,
    session_factory: SessionFactory,
    s3,
    renderer: Renderer | None,
) -> str:
    principal = _principal(claimed.organization_id)
    worker_id = claimed.worker_id or ""
    fence_token = claimed.fence_token
    uploaded: list[StagedDeliverableSpec] = []
    failure: RenderWorkerFailure | None = None
    try:
        with tempfile.TemporaryDirectory(
            prefix=f"instadescribe-render-{str(claimed.job_id)[:8]}-",
            dir=settings.workspace_root,
        ) as workspace_name:
            workspace = Path(workspace_name)
            source_path = workspace / "source-video"
            scenes_path = workspace / "scenes.json"
            entities_path = workspace / "entities.json"
            output_dir = workspace / "deliverables"

            _assert_owned(
                session,
                settings,
                principal,
                claimed.job_id,
                worker_id,
                fence_token,
            )
            artifacts = _artifact_map(session, principal, claimed.job_id)
            source_artifact = artifacts["source_video"]
            job = session.execute(
                sa.select(Job).where(
                    Job.organization_id == principal.organization_id,
                    Job.id == claimed.job_id,
                    Job.status == JobState.EXPORTING.value,
                )
            ).scalar_one_or_none()
            if job is None:
                raise RenderOwnershipLost
            try:
                source_sha256 = download_source(
                    s3,
                    settings.media_bucket,
                    job,
                    source_path,
                )
                if source_sha256 != source_artifact.checksum_sha256:
                    raise JobFailure(
                        FailureCode.SOURCE_IDENTITY_MISMATCH,
                        "source bytes disagree with the immutable artifact row",
                    )
                _assert_owned(
                    session,
                    settings,
                    principal,
                    claimed.job_id,
                    worker_id,
                    fence_token,
                )
                download_versioned_artifact(
                    s3,
                    settings.media_bucket,
                    artifacts["scenes_json"],
                    scenes_path,
                    max_bytes=_JSON_ARTIFACT_LIMIT,
                )
                _assert_owned(
                    session,
                    settings,
                    principal,
                    claimed.job_id,
                    worker_id,
                    fence_token,
                )
                download_versioned_artifact(
                    s3,
                    settings.media_bucket,
                    artifacts["entities_json"],
                    entities_path,
                    max_bytes=_JSON_ARTIFACT_LIMIT,
                )
            except JobFailure as exc:
                raise _map_job_failure(exc) from None

            snapshot = _reviewed_snapshot(
                session,
                principal,
                claimed,
                scenes_payload=_strict_json(scenes_path, "scenes"),
                entities_payload=_strict_json(entities_path, "entities"),
            )

            def on_progress(_stage: str, _percent: int) -> None:
                _assert_owned(
                    session,
                    settings,
                    principal,
                    claimed.job_id,
                    worker_id,
                    fence_token,
                )

            heartbeat = RenderLeaseHeartbeat(
                session_factory,
                settings,
                principal,
                claimed.job_id,
                worker_id=worker_id,
                fence_token=fence_token,
            )
            renderer_error: Exception | None = None
            outputs: dict[str, Path] | None = None
            heartbeat.start()
            try:
                if renderer is None:
                    outputs = _run_default_renderer(
                        settings,
                        session,
                        heartbeat,
                        source_video=source_path,
                        scenes=snapshot.scenes,
                        entities_by_id=snapshot.entities_by_id,
                        output_dir=output_dir,
                        project_name=snapshot.project_name,
                        default_voice=snapshot.default_voice,
                    )
                else:
                    # Explicitly injected renderers are a deterministic test
                    # seam only; production always takes the isolated branch.
                    outputs = renderer(
                        source_video=source_path,
                        scenes=snapshot.scenes,
                        entities_by_id=snapshot.entities_by_id,
                        output_dir=output_dir,
                        project_name=snapshot.project_name,
                        default_voice=snapshot.default_voice,
                        on_progress=on_progress,
                    )
            except Exception as exc:
                renderer_error = exc
            finally:
                heartbeat.stop()
            heartbeat.assert_healthy(session)
            if isinstance(renderer_error, RenderOwnershipLost | WorkerShutdownRequested):
                raise renderer_error
            if renderer_error is not None:
                raise RenderWorkerFailure(
                    RenderFailureCode.RENDER_FAILED,
                    "The five-format render failed.",
                ) from None
            assert outputs is not None
            _assert_owned(
                session,
                settings,
                principal,
                claimed.job_id,
                worker_id,
                fence_token,
            )
            validated = _validated_outputs(outputs, output_dir)
            specs = _upload_outputs(
                session,
                settings,
                principal,
                claimed.job_id,
                worker_id,
                fence_token,
                s3,
                validated,
                uploaded,
            )
            _assert_owned(
                session,
                settings,
                principal,
                claimed.job_id,
                worker_id,
                fence_token,
            )
            stage_render_deliverables(
                session,
                principal,
                claimed.job_id,
                worker_id=worker_id,
                fence_token=fence_token,
                deliverables=specs,
            )
            _assert_owned(
                session,
                settings,
                principal,
                claimed.job_id,
                worker_id,
                fence_token,
            )
            publish_staged_deliverables(
                session,
                principal,
                claimed.job_id,
                worker_id=worker_id,
                fence_token=fence_token,
            )
            return "success"
    except WorkerShutdownRequested:
        _cleanup_uploaded_versions(
            session,
            settings,
            principal,
            s3,
            uploaded,
            reason="worker-shutdown",
        )
        return "shutdown"
    except RenderCancelled:
        _cleanup_uploaded_versions(
            session,
            settings,
            principal,
            s3,
            uploaded,
            reason="cancelled",
        )
        return "cancelled"
    except RenderOwnershipLost:
        _cleanup_uploaded_versions(
            session,
            settings,
            principal,
            s3,
            uploaded,
            reason="stale-fence",
        )
        return "stale_render"
    except RenderHeartbeatUnavailable:
        session.rollback()
        log(
            "render_heartbeat_unavailable",
            level="error",
            job_id=claimed.job_id,
            category="render-heartbeat",
        )
        _cleanup_uploaded_versions(
            session,
            settings,
            principal,
            s3,
            uploaded,
            reason="heartbeat-unavailable",
        )
        return "infra_error"
    except LifecycleConflict:
        outcome = "stale_render"
        try:
            if cancel_render_if_job_cancelled(
                session,
                principal,
                claimed.job_id,
                worker_id=worker_id,
                fence_token=fence_token,
            ):
                outcome = "cancelled"
        except Exception:
            session.rollback()
        _cleanup_uploaded_versions(
            session,
            settings,
            principal,
            s3,
            uploaded,
            reason="cancelled" if outcome == "cancelled" else "stale-fence",
        )
        return outcome
    except RenderWorkerFailure as exc:
        failure = exc
    except LifecycleServiceError:
        failure = RenderWorkerFailure(
            RenderFailureCode.INPUT_INVALID,
            "The locked render lifecycle is inconsistent.",
        )
    except (SQLAlchemyError, BotoCoreError, ClientError) as exc:
        session.rollback()
        log(
            "render_infrastructure_unavailable",
            level="error",
            job_id=claimed.job_id,
            category="render-infrastructure",
            error_class=type(exc).__name__,
        )
        _cleanup_uploaded_versions(
            session,
            settings,
            principal,
            s3,
            uploaded,
            reason="infrastructure-error",
        )
        return "infra_error"
    except Exception as exc:
        log(
            "render_internal_error",
            level="error",
            job_id=claimed.job_id,
            category="render",
            error_class=type(exc).__name__,
        )
        failure = RenderWorkerFailure(
            RenderFailureCode.INTERNAL_ERROR,
            "An unexpected render worker failure occurred.",
        )

    assert failure is not None
    try:
        event = fail_render(
            session,
            principal,
            claimed.job_id,
            worker_id=worker_id,
            fence_token=fence_token,
            error_code=failure.code.value,
            error_message=failure.public_message,
        )
    except Exception as exc:
        session.rollback()
        log(
            "render_failure_persistence_unavailable",
            level="error",
            job_id=claimed.job_id,
            category="db-render-failure",
            error_class=type(exc).__name__,
        )
        _cleanup_uploaded_versions(
            session,
            settings,
            principal,
            s3,
            uploaded,
            reason="failure-persistence-error",
        )
        return "infra_error"
    if event is None:
        outcome = "stale_render"
        try:
            if cancel_render_if_job_cancelled(
                session,
                principal,
                claimed.job_id,
                worker_id=worker_id,
                fence_token=fence_token,
            ):
                outcome = "cancelled"
        except Exception:
            session.rollback()
        _cleanup_uploaded_versions(
            session,
            settings,
            principal,
            s3,
            uploaded,
            reason="cancelled" if outcome == "cancelled" else "stale-fence",
        )
        return outcome
    _cleanup_uploaded_versions(
        session,
        settings,
        principal,
        s3,
        uploaded,
        reason="failed",
    )
    log(
        "render_failed",
        level="warning",
        job_id=claimed.job_id,
        error_code=failure.code.value,
    )
    return "failed"


def run_render_once(
    settings: WorkerSettings | None = None,
    *,
    session_factory: SessionFactory | None = None,
    s3=None,
    renderer: Renderer | None = None,
) -> str:
    """Poll and execute at most one render; return a stable cycle outcome."""

    if shutdown_requested():
        return "shutdown"
    settings = settings or get_worker_settings()
    session_factory = session_factory or get_sessionmaker()
    session = None
    try:
        session = session_factory()
        cleanup_orphaned_render_attempts(
            session,
            settings,
            s3 if s3 is not None else _s3(),
        )
        candidate = poll_render_candidate(session)
        if candidate is None:
            session.rollback()
            return "empty"
        worker_id = f"{settings.worker_id}:{uuid.uuid4()}"
        try:
            claimed = claim_render(
                session,
                _principal(candidate.organization_id),
                candidate.job_id,
                worker_id=worker_id,
                lease_expires_at=_lease_deadline(settings),
            )
        except RenderAttemptsExhausted:
            log(
                "render_attempt_limit_exhausted",
                level="warning",
                job_id=candidate.job_id,
            )
            return "failed"
        except LifecycleConflict:
            return "claim_lost"
        log(
            "render_claimed",
            job_id=claimed.job_id,
            worker_label=settings.worker_id,
            fence_token=claimed.fence_token,
            attempt=claimed.attempt_count,
        )
        return _execute_claimed(
            settings,
            session,
            claimed,
            session_factory=session_factory,
            s3=s3 if s3 is not None else _s3(),
            renderer=renderer,
        )
    except (SQLAlchemyError, BotoCoreError, ClientError) as exc:
        if session is not None:
            session.rollback()
        log(
            "render_poll_unavailable",
            level="error",
            category="render-poll",
            error_class=type(exc).__name__,
        )
        return "infra_error"
    except Exception as exc:
        if session is not None:
            session.rollback()
        log(
            "render_cycle_internal_error",
            level="error",
            category="render-cycle",
            error_class=type(exc).__name__,
        )
        return "infra_error"
    finally:
        if session is not None:
            session.close()
