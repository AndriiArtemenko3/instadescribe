"""Artifact validation, attempt-scoped upload and success ordering (B8,
hardened by G5.1 D1/D2).

Child exit zero is necessary but insufficient: required outputs are strictly
validated first (standards-compliant JSON, canonical unique scene IDs, sane
time bounds, an honest scene count). Generated objects are ATTEMPT-SCOPED —
`jobs/{job_id}/attempts/{attempt}/...` — so a retried or stale attempt can
never overwrite another attempt's bytes; the artifact ROWS select the winning
attempt atomically inside the same transaction as the guarded
PROCESSING → READY_FOR_REVIEW transition (a future manifest resolves rows,
never constructs keys). `source_video` remains the exact VERSIONED upload
object. Only after that commit is the SQS message deleted.
"""

import hashlib
import json
import math
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from app.domain.states import JobState
from app.models import Artifact, Job, JobEvent, Review
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from instadescribe_worker.failures import FailureCode, JobFailure
from instadescribe_worker.workspace import Workspace

REQUIRED_JSON = {
    "scenes_json": "scenes.json",
    "entities_json": "entities.json",
    "audio_events_json": "audio_events.json",
    "ad_placement_gaps_json": "ad_placement_gaps.json",
    "transcript_json": "transcript.json",
}
OPTIONAL_POSTERS = {
    "poster_jpg": ("poster.jpg", "image/jpeg"),
    "poster_avif": ("poster.avif", "image/avif"),
}


@dataclass
class LocalArtifact:
    artifact_type: str
    local_path: Path
    object_key: str
    content_type: str
    meta: dict


_SCENE_ID_RE = re.compile(r"^scene_[1-9][0-9]*$")


def _strict_json(path: Path, filename: str):
    """Standards-compliant JSON only — NaN/Infinity are rejected (D1)."""

    def _no_constants(value: str):
        raise ValueError(f"non-standard JSON constant {value!r}")

    try:
        return json.loads(path.read_text(), parse_constant=_no_constants)
    except JobFailure:
        raise
    except Exception:
        raise JobFailure(
            FailureCode.ARTIFACTS_INVALID, f"required output {filename} missing or unreadable"
        ) from None


def _validate_scenes(payload) -> tuple[str, ...]:
    """Non-empty list of objects with unique canonical scene IDs and finite,
    ordered time bounds; returns the immutable ordered scene identity set."""
    if not isinstance(payload, list) or not payload:
        raise JobFailure(FailureCode.ARTIFACTS_INVALID, "scenes.json is empty or not a list")
    seen_ids: set[str] = set()
    for scene in payload:
        if not isinstance(scene, dict):
            raise JobFailure(FailureCode.ARTIFACTS_INVALID, "scenes.json entry is not an object")
        scene_id = scene.get("scene_id")
        # fullmatch, not match: `$` under .match still accepts a literal
        # trailing newline, which the exact API/DB contract rejects (G8 B1).
        if not isinstance(scene_id, str) or not _SCENE_ID_RE.fullmatch(scene_id):
            raise JobFailure(FailureCode.ARTIFACTS_INVALID, "scene id is not canonical")
        if scene_id in seen_ids:
            raise JobFailure(FailureCode.ARTIFACTS_INVALID, "duplicate scene id")
        seen_ids.add(scene_id)
        start, end = scene.get("start"), scene.get("end")
        for bound in (start, end):
            if isinstance(bound, bool) or not isinstance(bound, int | float):
                raise JobFailure(FailureCode.ARTIFACTS_INVALID, "scene bound is not numeric")
            if not math.isfinite(bound):
                raise JobFailure(FailureCode.ARTIFACTS_INVALID, "scene bound is not finite")
        if not end > start:
            raise JobFailure(FailureCode.ARTIFACTS_INVALID, "scene end must exceed start")
    return tuple(scene["scene_id"] for scene in payload)


def _nonnegative_int(value, category: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise JobFailure(FailureCode.ARTIFACTS_INVALID, f"system info {category} is invalid")
    return value


def _canonical_system_info(
    path: Path,
    job_id: str,
    provider: str,
    model: str,
    *,
    expected_chunk_size: int,
) -> dict:
    """Validate and reduce the child sidecar to a secret-free provenance shape."""
    raw = _strict_json(path, "system_info.json")
    if (
        not isinstance(raw, dict)
        or raw.get("video_id") != job_id
        or raw.get("status") != "completed"
    ):
        raise JobFailure(FailureCode.ARTIFACTS_INVALID, "system info identity is invalid")
    processing = raw.get("processing")
    if not isinstance(processing, dict) or processing.get("model") != model:
        raise JobFailure(FailureCode.ARTIFACTS_INVALID, "system info model is invalid")
    if processing.get("image_detail") != "low" or processing.get("chunk_sizes") != [
        expected_chunk_size
    ]:
        raise JobFailure(FailureCode.ARTIFACTS_INVALID, "system info processing bounds are invalid")
    tokens = raw.get("tokens")
    if not isinstance(tokens, dict):
        raise JobFailure(FailureCode.ARTIFACTS_INVALID, "system info token usage is invalid")
    canonical_tokens = {
        name: _nonnegative_int(tokens.get(name), f"token {name}")
        for name in ("input_tokens", "output_tokens", "total_tokens")
    }
    if canonical_tokens["total_tokens"] != (
        canonical_tokens["input_tokens"] + canonical_tokens["output_tokens"]
    ):
        raise JobFailure(FailureCode.ARTIFACTS_INVALID, "system info token totals disagree")
    canonical = {
        "schema_version": 1,
        "video_id": job_id,
        "processing": {
            "provider": provider,
            "model": model,
            "image_detail": "low",
            "chunk_sizes": processing["chunk_sizes"],
        },
        "tokens": canonical_tokens,
        "status": "completed",
    }
    # Replacing rather than augmenting the child document ensures unknown
    # fields can never smuggle credentials into an uploaded client artifact.
    path.write_text(json.dumps(canonical, indent=2, sort_keys=True) + "\n")
    return canonical


def attempt_prefix(job_id: str, attempt: int) -> str:
    """Attempt-scoped generated-object prefix (D2): a retried or stale
    attempt writes under its own prefix and can never change a winner's
    bytes; rows — not constructed keys — select the winner."""
    return f"jobs/{job_id}/attempts/{attempt}"


def validate_outputs(
    workspace: Workspace,
    job_id: str,
    attempt: int,
    *,
    provider: str = "fake",
    model: str = "gpt-4.1",
    expected_chunk_size: int = 60,
) -> list[LocalArtifact]:
    """Strictly validate the required artifact set; returns the upload list.
    Optional posters degrade rather than fail (ADR-0008)."""
    if expected_chunk_size not in {60, 120}:
        raise JobFailure(FailureCode.ARTIFACTS_INVALID, "expected chunk bound is invalid")
    out_dir = workspace.data_dir
    prefix = attempt_prefix(job_id, attempt)
    artifacts: list[LocalArtifact] = []
    scene_count_actual = 0
    for artifact_type, filename in REQUIRED_JSON.items():
        path = out_dir / filename
        payload = _strict_json(path, filename)
        artifact_meta = {}
        if artifact_type == "scenes_json":
            scene_ids = _validate_scenes(payload)
            scene_count_actual = len(scene_ids)
            # Finish Review validates every decision against this immutable,
            # transactionally selected artifact row. It never trusts a client
            # supplied count and does not need a later S3 read to establish
            # scene identity.
            artifact_meta = {
                "scene_ids": list(scene_ids),
                "scene_count": scene_count_actual,
            }
        elif not isinstance(payload, list):
            raise JobFailure(FailureCode.ARTIFACTS_INVALID, f"{filename} is not a list")
        artifacts.append(
            LocalArtifact(
                artifact_type=artifact_type,
                local_path=path,
                object_key=f"{prefix}/analysis/{filename}",
                content_type="application/json",
                meta=artifact_meta,
            )
        )
    system_path = out_dir / "system_info.json"
    # Legacy/fake workers predate this row, so fake compatibility remains
    # optional. Every OpenAI G12 success must carry validated provenance.
    if system_path.exists() or provider == "openai":
        system_info = _canonical_system_info(
            system_path,
            job_id,
            provider,
            model,
            expected_chunk_size=expected_chunk_size,
        )
        artifacts.append(
            LocalArtifact(
                artifact_type="system_info_json",
                local_path=system_path,
                object_key=f"{prefix}/analysis/system_info.json",
                content_type="application/json",
                meta={
                    "provider": provider,
                    "model": model,
                    "tokens": system_info["tokens"],
                },
            )
        )
    result_path = workspace.job_dir / "result.json"
    result = _strict_json(result_path, "result.json")
    if not isinstance(result, dict):
        raise JobFailure(FailureCode.ARTIFACTS_INVALID, "result.json is not an object")
    scene_count = result.get("scene_count")
    # bool is an int subclass: `True` must not pass as a count of one (D1).
    if isinstance(scene_count, bool) or not isinstance(scene_count, int) or scene_count <= 0:
        raise JobFailure(FailureCode.ARTIFACTS_INVALID, "result.json reports no scenes")
    if scene_count != scene_count_actual:
        raise JobFailure(
            FailureCode.ARTIFACTS_INVALID, "result.json scene count disagrees with scenes.json"
        )
    if result.get("data_path") != f"/data/{job_id}":
        raise JobFailure(FailureCode.ARTIFACTS_INVALID, "result.json is for a different job")
    for artifact_type, (filename, content_type) in OPTIONAL_POSTERS.items():
        path = out_dir / filename
        if path.exists() and path.stat().st_size > 0:
            artifacts.append(
                LocalArtifact(
                    artifact_type=artifact_type,
                    local_path=path,
                    object_key=f"{prefix}/posters/{filename}",
                    content_type=content_type,
                    meta={},
                )
            )
    return artifacts


def upload_and_finalize(
    session: Session,
    s3,
    bucket: str,
    job: Job,
    worker_id: str,
    artifacts: list[LocalArtifact],
    source_local_sha256: str,
    *,
    before_upload: Callable[[], None] | None = None,
) -> bool:
    """Upload deterministic objects, then upsert rows + transition in one
    transaction. Returns False when a stale worker lost the finalize race —
    the caller must NOT delete the message in that case."""
    rows = []
    for artifact in artifacts:
        # Heartbeat before each potentially blocking network write.  This is
        # still before the database transaction: a heartbeat failure may
        # leave attempt-scoped S3 bytes, but can never commit artifact rows or
        # a terminal state for a stale worker.
        if before_upload is not None:
            before_upload()
        body = artifact.local_path.read_bytes()
        checksum = hashlib.sha256(body).hexdigest()
        response = s3.put_object(
            Bucket=bucket,
            Key=artifact.object_key,
            Body=body,
            ContentType=artifact.content_type,
            ServerSideEncryption="AES256",
        )
        version_id = response.get("VersionId") if isinstance(response, dict) else None
        if not isinstance(version_id, str) or not version_id.strip():
            raise JobFailure(
                FailureCode.ARTIFACTS_INVALID,
                "generated artifact upload did not return a pinned object version",
            )
        persisted_meta = {**artifact.meta, "version_id": version_id}
        response_etag = response.get("ETag")
        if isinstance(response_etag, str) and response_etag.strip('"'):
            persisted_meta["etag"] = response_etag.strip('"')
        rows.append(
            {
                "id": uuid.uuid4(),
                "organization_id": job.organization_id,
                "job_id": job.id,
                "artifact_type": artifact.artifact_type,
                "object_key": artifact.object_key,
                "version_id": version_id,
                "content_type": artifact.content_type,
                "size_bytes": len(body),
                "checksum_sha256": checksum,
                "meta": persisted_meta,
            }
        )
    if before_upload is not None:
        before_upload()
    # C1: the source row must retain the EXACT processed version and stay
    # consistent with the job's pinned identity — never "latest".
    if not job.source_version_id:
        raise JobFailure(
            FailureCode.SOURCE_IDENTITY_MISMATCH,
            "job has no pinned source version; refusing to record source provenance",
        )
    rows.append(
        {
            "id": uuid.uuid4(),
            "organization_id": job.organization_id,
            "job_id": job.id,
            "artifact_type": "source_video",
            "object_key": job.input_object_key,
            "version_id": job.source_version_id,
            "content_type": job.input_content_type or "video/mp4",
            "size_bytes": job.input_size_bytes,
            "checksum_sha256": source_local_sha256,
            "meta": {"etag": job.source_etag or "", "version_id": job.source_version_id},
        }
    )

    # One transaction: rows + conditional success transition, then commit.
    for row in rows:
        stmt = pg_insert(Artifact).values(**row)
        stmt = stmt.on_conflict_do_update(
            index_elements=["job_id", "artifact_type"],
            set_={
                "object_key": stmt.excluded.object_key,
                "organization_id": stmt.excluded.organization_id,
                "version_id": stmt.excluded.version_id,
                "content_type": stmt.excluded.content_type,
                "size_bytes": stmt.excluded.size_bytes,
                "checksum_sha256": stmt.excluded.checksum_sha256,
                # The column is NAMED "metadata" (attribute: meta) — the
                # excluded collection keys by column name.
                "metadata": stmt.excluded["metadata"],
                "retention_state": "active",
                "purged_at": None,
                "purge_after": sa.func.now() + sa.text("interval '30 days'"),
                # ADR-0008 §3: Core upserts set freshness explicitly — ORM
                # onupdate does not fire here (artifacts carries created_at).
                "created_at": sa.func.now(),
            },
        )
        session.execute(stmt)
    finalized_version = session.execute(
        sa.update(Job)
        .where(
            Job.id == job.id,
            Job.status == JobState.PROCESSING.value,
            Job.worker_id == worker_id,
            Job.lease_expires_at.is_not(None),
            Job.lease_expires_at > sa.func.now(),
        )
        .values(
            status=JobState.READY_FOR_REVIEW.value,
            progress=100,
            stage="complete",
            completed_at=datetime.now(UTC),
            lease_expires_at=None,
            version=Job.version + 1,
            updated_at=sa.func.now(),
        )
        .returning(Job.version)
    ).scalar_one_or_none()
    if finalized_version is None:
        session.rollback()  # stale worker: report no success, keep the message
        return False

    # Review availability and its public notification become visible in the
    # same commit as READY_FOR_REVIEW and the winning artifact rows. Conflict
    # handling supports a deliberately re-driven historical test/job without
    # duplicating an immutable logical event.
    session.execute(
        pg_insert(Review)
        .values(
            id=uuid.uuid4(),
            organization_id=job.organization_id,
            job_id=job.id,
            state="open",
        )
        .on_conflict_do_nothing(index_elements=["organization_id", "job_id"])
    )
    occurred_at = datetime.now(UTC)
    event_id = uuid.uuid4()
    session.execute(
        pg_insert(JobEvent)
        .values(
            id=event_id,
            organization_id=job.organization_id,
            job_id=job.id,
            event_type="job.needs_review",
            job_version=finalized_version,
            payload={
                "id": str(event_id),
                "type": "job.needs_review",
                "jobId": str(job.id),
                "state": "needs_review",
                "occurredAt": occurred_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
            },
            occurred_at=occurred_at,
            available_at=occurred_at,
        )
        .on_conflict_do_nothing(index_elements=["organization_id", "job_id", "event_type"])
    )
    session.commit()
    return True
