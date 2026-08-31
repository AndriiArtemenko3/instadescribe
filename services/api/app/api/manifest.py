"""GET /jobs/{job_id}/manifest — the exact version-pinned artifact manifest
(G6 Gate 2, ADR-0003).

The path identifier is a PROCESSING JOB ID, never a project ID. Bounded G6
serves manifests only for `READY_FOR_REVIEW`. Rows are resolved and
validated (winning attempt, pinned source identity) before any signing; one
request signs every reference against ONE common expiry instant via the
browser-visible endpoint. Failures are sanitized: identity problems are a
generic 404, non-ready states a safe 409, and database/signing/consistency
problems a 503 with a stable category logged — never exception text, keys,
bucket names, endpoints, credentials or the token.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.tenancy import PORTFOLIO_ORGANIZATION_ID
from app.db.session import get_db
from app.domain.states import JobState
from app.repositories.artifacts import (
    ManifestInconsistent,
    ResolvedArtifact,
    load_job_with_project,
    resolve_manifest,
)
from app.schemas.manifest import ManifestResponse
from app.services.s3 import generate_download_url

logger = logging.getLogger("app.manifest")
router = APIRouter()


def _http_error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _ref(resolved: ResolvedArtifact, expires_in: int) -> dict[str, Any]:
    return {
        "url": generate_download_url(
            resolved.object_key, version_id=resolved.version_id, expires_in=expires_in
        ),
        "contentType": resolved.content_type,
        "sizeBytes": resolved.size_bytes,
        "checksumSha256": resolved.checksum_sha256,
    }


@router.get("/jobs/{job_id}/manifest")
def get_manifest(job_id: str, db: Session = Depends(get_db)) -> JSONResponse:
    return get_manifest_for_organization(job_id, PORTFOLIO_ORGANIZATION_ID, db)


def get_manifest_for_organization(
    job_id: str,
    organization_id: uuid.UUID,
    db: Session,
) -> JSONResponse:
    """Build a manifest only after the caller resolved its tenant boundary."""
    try:
        parsed = uuid.UUID(job_id)
    except ValueError:
        raise _http_error(404, "not_found", "not found") from None
    try:
        row = load_job_with_project(db, parsed, organization_id)
    except Exception:
        logger.warning("manifest_failed category=database")
        raise _http_error(503, "manifest_unavailable", "manifest unavailable") from None
    if row is None:  # absent job OR a project ID supplied as a job ID
        raise _http_error(404, "not_found", "not found")
    job, project = row.Job, row.Project
    if job.workflow_kind != "audio_description":
        raise _http_error(404, "not_found", "not found")
    if job.status != JobState.READY_FOR_REVIEW.value:
        raise _http_error(409, "artifacts_not_ready", "artifacts are not ready for review")

    settings = get_settings()
    expires_in = settings.download_presign_expiry_secs
    try:
        resolved = resolve_manifest(db, job)
    except ManifestInconsistent as exc:
        logger.warning("manifest_failed category=%s", exc.category)
        raise _http_error(503, "manifest_unavailable", "manifest unavailable") from None
    except Exception:
        logger.warning("manifest_failed category=database")
        raise _http_error(503, "manifest_unavailable", "manifest unavailable") from None

    # ONE common expiry instant for every reference in this response.
    expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
    try:
        artifacts = {
            wire: (_ref(reference, expires_in) if reference is not None else None)
            for wire, reference in resolved.items()
        }
    except Exception:
        logger.warning("manifest_failed category=signer")
        raise _http_error(503, "manifest_unavailable", "manifest unavailable") from None

    # G6.1/G6.2: final contract validation, serialization AND response
    # rendering all sit inside the same sanitized boundary — Starlette
    # encodes the JSON in the JSONResponse constructor, so a render failure
    # outside the try could still escape as a 500. A clean 503 only; signed
    # URLs and row contents never reach logs.
    try:
        body = {
            "projectId": str(project.id),
            "jobId": str(job.id),
            "pipelineRevision": job.pipeline_revision,
            "expiresAt": expires_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "artifacts": artifacts,
        }
        # The typed contract is enforced before anything leaves the process.
        content = ManifestResponse.model_validate(body).model_dump(by_alias=True)
        # Signed URLs must never be cached or stored by intermediaries.
        return JSONResponse(content=content, headers={"Cache-Control": "private, no-store"})
    except Exception:
        logger.warning("manifest_failed category=contract")
        raise _http_error(503, "manifest_unavailable", "manifest unavailable") from None
