"""Manifest row resolution (G6 Gate 2, ADR-0003/ADR-0008).

The manifest RESOLVES persisted artifact rows — it never reconstructs keys.
Every row is validated against the job's winning attempt and pinned source
identity before anything is signed; ANY missing or inconsistent required row
refuses the whole manifest (no partial manifests). Unknown future artifact
types are ignored. Validation failures raise ManifestInconsistent with a
stable internal category only — callers log the category and return a
sanitized 503; row keys/metadata never reach responses or logs.
"""

import re
import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.core.tenancy import PORTFOLIO_ORGANIZATION_ID
from app.models import Artifact, Job

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")

# artifact_type -> (wire name, expected key suffix, expected content type)
GENERATED_REQUIRED = {
    "scenes_json": ("scenes", "analysis/scenes.json", "application/json"),
    "entities_json": ("entities", "analysis/entities.json", "application/json"),
    "audio_events_json": ("audioEvents", "analysis/audio_events.json", "application/json"),
    "ad_placement_gaps_json": (
        "placementGaps",
        "analysis/ad_placement_gaps.json",
        "application/json",
    ),
    "transcript_json": ("transcript", "analysis/transcript.json", "application/json"),
}
GENERATED_OPTIONAL = {
    "poster_jpg": ("posterJpg", "posters/poster.jpg", "image/jpeg"),
    "poster_avif": ("posterAvif", "posters/poster.avif", "image/avif"),
}


class ManifestInconsistent(Exception):
    """Internal category only — never exception text from row content."""

    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(category)


@dataclass
class ResolvedArtifact:
    object_key: str  # signed as stored — NEVER constructed
    content_type: str
    size_bytes: int
    checksum_sha256: str
    version_id: str | None = None  # only the pinned source carries one


def _validated_common(row: Artifact, category: str) -> tuple[int, str]:
    size = row.size_bytes
    if not isinstance(size, int) or size <= 0:
        raise ManifestInconsistent(f"{category}-size")
    checksum = row.checksum_sha256 or ""
    # G6.2: fullmatch — `.match()` with `$` accepts a trailing newline, so a
    # 65-character "64 hex + \n" value would have passed the exact contract.
    if not _SHA256_HEX_RE.fullmatch(checksum):
        raise ManifestInconsistent(f"{category}-checksum")
    return size, checksum


def resolve_manifest(session: Session, job: Job) -> dict[str, ResolvedArtifact | None]:
    """Return wire-name -> validated reference (optional posters map to None
    when absent). Raises ManifestInconsistent on any required-row violation."""
    rows = {
        row.artifact_type: row
        for row in session.execute(
            sa.select(Artifact).where(
                Artifact.organization_id == job.organization_id,
                Artifact.job_id == job.id,
                Artifact.retention_state == "active",
            )
        ).scalars()
    }
    winning_prefix = f"jobs/{job.id}/attempts/{job.attempt_count}/"
    resolved: dict[str, ResolvedArtifact | None] = {}

    # --- the pinned processed source -------------------------------------
    source = rows.get("source_video")
    if source is None:
        raise ManifestInconsistent("source-missing")
    if not job.source_version_id:
        raise ManifestInconsistent("source-unpinned")
    if source.object_key != job.input_object_key:
        raise ManifestInconsistent("source-key")
    meta = source.meta or {}
    if meta.get("version_id") != job.source_version_id:
        raise ManifestInconsistent("source-version")
    if meta.get("etag") != job.source_etag:
        raise ManifestInconsistent("source-etag")
    # G6.1: fully fail closed — the row must also agree with the job's
    # persisted content type and byte size, not only key/ETag/VersionId.
    if source.content_type != job.input_content_type:
        raise ManifestInconsistent("source-content-type")
    if source.size_bytes != job.input_size_bytes:
        raise ManifestInconsistent("source-size")
    size, checksum = _validated_common(source, "source")
    resolved["video"] = ResolvedArtifact(
        object_key=source.object_key,
        content_type=source.content_type,
        size_bytes=size,
        checksum_sha256=checksum,
        version_id=job.source_version_id,
    )

    # --- generated artifacts: winning attempt only ------------------------
    for artifact_type, (wire, suffix, expected_type) in GENERATED_REQUIRED.items():
        row = rows.get(artifact_type)
        if row is None:
            raise ManifestInconsistent("artifact-missing")
        resolved[wire] = _validated_generated(row, winning_prefix, suffix, expected_type)
    system_info = rows.get("system_info_json")
    if system_info is None:
        if job.provider == "openai":
            raise ManifestInconsistent("system-info-missing")
        resolved["systemInfo"] = None  # pre-G12 fake-job compatibility
    else:
        meta = system_info.meta or {}
        if meta.get("provider") != job.provider or meta.get("model") != job.model:
            raise ManifestInconsistent("system-info-provenance")
        tokens = meta.get("tokens")
        if not isinstance(tokens, dict) or any(
            isinstance(tokens.get(k), bool)
            or not isinstance(tokens.get(k), int)
            or tokens.get(k) < 0
            for k in ("input_tokens", "output_tokens", "total_tokens")
        ):
            raise ManifestInconsistent("system-info-tokens")
        if tokens["total_tokens"] != tokens["input_tokens"] + tokens["output_tokens"]:
            raise ManifestInconsistent("system-info-tokens")
        resolved["systemInfo"] = _validated_generated(
            system_info,
            winning_prefix,
            "analysis/system_info.json",
            "application/json",
        )
    for artifact_type, (wire, suffix, expected_type) in GENERATED_OPTIONAL.items():
        row = rows.get(artifact_type)
        resolved[wire] = (
            _validated_generated(row, winning_prefix, suffix, expected_type)
            if row is not None
            else None
        )
    # Unknown artifact types in `rows` are deliberately ignored.
    return resolved


def _validated_generated(
    row: Artifact, winning_prefix: str, suffix: str, expected_type: str
) -> ResolvedArtifact:
    # G6.1: the G5 worker emits ONE deterministic key per known artifact —
    # exact equality is the validation (prefix/endswith/substring checks
    # accepted nested, substring and duplicate-basename forms). The check is
    # validation ONLY: signing still uses the persisted row key.
    if row.object_key != winning_prefix + suffix:
        raise ManifestInconsistent("artifact-key")
    if row.content_type != expected_type:
        raise ManifestInconsistent("artifact-content-type")
    size, checksum = _validated_common(row, "artifact")
    return ResolvedArtifact(
        object_key=row.object_key,
        content_type=row.content_type,
        size_bytes=size,
        checksum_sha256=checksum,
    )


def load_job_with_project(
    session: Session,
    job_id: uuid.UUID,
    organization_id: uuid.UUID = PORTFOLIO_ORGANIZATION_ID,
):
    """Job + Project or None — the caller maps None to the generic 404."""
    from app.models import Project

    return session.execute(
        sa.select(Job, Project)
        .join(Project, Job.project_id == Project.id)
        .where(Job.id == job_id, Project.organization_id == organization_id)
    ).one_or_none()


def load_job_with_project_for_update(
    session: Session,
    job_id: uuid.UUID,
    organization_id: uuid.UUID = PORTFOLIO_ORGANIZATION_ID,
):
    """Lock only the Job row for a state-checked mutation transaction.

    PostgreSQL refreshes the selected row after a conflicting transaction
    releases its lock, so the caller validates the current committed state.
    The joined Project row is deliberately not locked.
    """
    from app.models import Project

    return session.execute(
        sa.select(Job, Project)
        .join(Project, Job.project_id == Project.id)
        .where(Job.id == job_id, Project.organization_id == organization_id)
        .with_for_update(of=Job)
    ).one_or_none()
