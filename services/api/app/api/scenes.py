"""Conflict-safe scene override and explicit human review routes.

PATCH /jobs/{job_id}/scenes/{scene_id}  — strict versioned partial write
GET   /jobs/{job_id}/overrides          — deterministic compatibility map

Both exist only for READY_FOR_REVIEW in bounded G6 and share the manifest's
generic-404 identity policy (job ID only — a project ID yields 404). Other
states are a safe 409 job_not_editable; database failures roll back and
return a sanitized 503 persistence_unavailable. The original generated
scenes_json artifact is immutable — user edits live ONLY in scene_overrides.

Retained limitation: scene_id is validated for canonical SHAPE; a
canonical-but-nonexistent scene creates an orphan override rather than
triggering a per-PATCH S3 read of scenes.json. Every update after the first
insert requires the exact server version; lost races return a sanitized 409.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.rfc3339 import utc_timestamp
from app.core.tenancy import PORTFOLIO_ORGANIZATION_ID
from app.db.session import get_db
from app.domain.retention import REVIEW_INACTIVITY_TTL
from app.domain.states import JobState
from app.models import Review, SceneOverride
from app.repositories.artifacts import load_job_with_project, load_job_with_project_for_update
from app.repositories.overrides import StaleVersionError, list_overrides, write_override
from app.schemas.scenes import SCENE_ID_RE, SceneOverridePatch

logger = logging.getLogger("app.scenes")
router = APIRouter()


def _http_error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


@dataclass(frozen=True, slots=True)
class SceneMutationResult:
    body: dict[str, Any]
    override_id: uuid.UUID


def _load_editable(
    db: Session,
    job_id: str,
    *,
    organization_id: uuid.UUID = PORTFOLIO_ORGANIZATION_ID,
    lock_job: bool = False,
):
    try:
        parsed = uuid.UUID(job_id)
    except ValueError:
        raise _http_error(404, "not_found", "not found") from None
    try:
        loader = load_job_with_project_for_update if lock_job else load_job_with_project
        # Preserve the original two-argument portfolio seam (including test
        # instrumentation around its row-lock boundary). Browser tenants still
        # pass their organization explicitly and cannot fall through to the
        # legacy portfolio default.
        row = (
            loader(db, parsed)
            if organization_id == PORTFOLIO_ORGANIZATION_ID
            else loader(db, parsed, organization_id)
        )
    except Exception:
        logger.warning("override_failed category=database")
        raise _http_error(503, "persistence_unavailable", "persistence unavailable") from None
    if row is None:  # absent job OR a project ID supplied as a job ID
        raise _http_error(404, "not_found", "not found")
    if row.Job.status != JobState.READY_FOR_REVIEW.value:
        raise _http_error(409, "job_not_editable", "job is not editable in its current state")
    return row


def _override_body(row: SceneOverride) -> dict[str, Any]:
    body: dict[str, Any] = {
        "active": row.active,
        "locked": row.locked,
        "version": row.version,
        "reviewStatus": row.review_status,
        "reviewedAt": (utc_timestamp(row.reviewed_at) if row.reviewed_at is not None else None),
        "updatedAt": utc_timestamp(row.updated_at),
    }
    if row.text is not None:
        body["ad"] = row.text
    if row.voice is not None:
        body["voice"] = row.voice
    if row.speed is not None:
        body["speed"] = float(row.speed)
    return body


@router.patch("/jobs/{job_id}/scenes/{scene_id}")
def patch_scene(
    job_id: str, scene_id: str, payload: SceneOverridePatch, db: Session = Depends(get_db)
) -> JSONResponse:
    result = patch_scene_for_organization(
        job_id,
        scene_id,
        payload,
        PORTFOLIO_ORGANIZATION_ID,
        db,
    )
    return JSONResponse(
        content=result.body,
        headers={"Cache-Control": "private, no-store"},
    )


def patch_scene_for_organization(
    job_id: str,
    scene_id: str,
    payload: SceneOverridePatch,
    organization_id: uuid.UUID,
    db: Session,
    *,
    commit_transaction: bool = True,
) -> SceneMutationResult:
    # The Job row lock spans state validation and the override commit. A
    # concurrent transition either commits first (this sees non-editable) or
    # waits until this edit commits; no edit can cross the state boundary.
    row = _load_editable(
        db,
        job_id,
        organization_id=organization_id,
        lock_job=True,
    )
    # G6.1: fullmatch — `.match()` with `$` accepts a trailing newline.
    if len(scene_id) > 120 or not SCENE_ID_RE.fullmatch(scene_id):
        raise _http_error(422, "invalid_scene_id", "scene id must be canonical (scene_N)")
    try:
        stored = write_override(
            db,
            row.Job.id,
            scene_id,
            payload.column_values(),
            expected_version=payload.expected_version,
            review_status=payload.resolved_review_status().value,
            # The override and review activity deadline are one mutation. The
            # caller owns the single commit so neither half can survive alone.
            commit_transaction=False,
        )
        now = datetime.now(UTC)
        review = db.execute(
            sa.select(Review)
            .where(
                Review.organization_id == organization_id,
                Review.job_id == row.Job.id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if review is None:
            # Historical portfolio READY_FOR_REVIEW rows predate the Review
            # table. Materialize their open review on the first edit so the
            # compatibility route receives the same inactivity policy.
            review = Review(
                organization_id=organization_id,
                job_id=row.Job.id,
                state="open",
                inactivity_expires_at=now + REVIEW_INACTIVITY_TTL,
            )
            db.add(review)
        elif review.state != "open":
            raise _http_error(
                409,
                "review_not_editable",
                "review is not editable in its current state",
            )
        else:
            review.inactivity_expires_at = now + REVIEW_INACTIVITY_TTL
            review.updated_at = now
            review.version += 1
        if commit_transaction:
            db.commit()
        else:
            db.flush()
    except StaleVersionError:
        try:
            db.rollback()
        except Exception:
            pass
        raise _http_error(409, "stale_version", "resource changed; refresh and retry") from None
    except HTTPException:
        try:
            db.rollback()
        except Exception:
            pass
        raise
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        logger.warning("override_failed category=database")
        raise _http_error(503, "persistence_unavailable", "persistence unavailable") from None
    body = {
        "projectId": str(row.Project.id),
        "jobId": str(row.Job.id),
        "sceneId": stored.scene_id,
        "version": stored.version,
        "reviewStatus": stored.review_status,
        "reviewedAt": (
            utc_timestamp(stored.reviewed_at) if stored.reviewed_at is not None else None
        ),
        "updatedAt": utc_timestamp(stored.updated_at),
        "override": _override_body(stored),
    }
    return SceneMutationResult(body=body, override_id=stored.id)


@router.get("/jobs/{job_id}/overrides")
def get_overrides(job_id: str, db: Session = Depends(get_db)) -> JSONResponse:
    return JSONResponse(
        content=get_overrides_for_organization(job_id, PORTFOLIO_ORGANIZATION_ID, db),
        headers={"Cache-Control": "private, no-store"},
    )


def get_overrides_for_organization(
    job_id: str,
    organization_id: uuid.UUID,
    db: Session,
) -> dict[str, Any]:
    row = _load_editable(db, job_id, organization_id=organization_id)
    try:
        rows = list_overrides(db, row.Job.id)
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        logger.warning("override_failed category=database")
        raise _http_error(503, "persistence_unavailable", "persistence unavailable") from None
    # The map shape remains compatible while each value now carries the exact
    # version/review metadata required for conflict-safe writes.
    return {stored.scene_id: _override_body(stored) for stored in rows}
