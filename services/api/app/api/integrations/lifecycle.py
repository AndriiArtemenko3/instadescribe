"""Read-only Integration API routes for review, render and deliverables.

The router is intentionally separate from ``v1.py`` so it can be included at
one explicit composition seam after the locked base contract lands.  Review
mutation is absent: ``finish_review`` is reserved for the authenticated Web
App surface.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, Path
from fastapi.responses import RedirectResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.integrations.auth import authenticate_integration_principal, require_scope
from app.api.integrations.problems import (
    INTEGRATION_PROBLEM_RESPONSES,
    IntegrationProblem,
    not_found,
)
from app.core.config import get_settings
from app.core.tenancy import PrincipalContext
from app.db.session import get_db
from app.schemas.lifecycle import (
    IntegrationDeliverablesResponse,
    IntegrationRenderResponse,
    IntegrationReviewResponse,
)
from app.services import lifecycle as lifecycle_service
from app.services.lifecycle import (
    DELIVERABLE_FILE_NAMES,
    LifecycleConflict,
    LifecycleInvariantError,
    LifecycleNotFound,
)
from app.services.s3 import generate_download_url

router = APIRouter(
    prefix="/api/integrations/v1",
    tags=["integrations"],
    dependencies=[Depends(authenticate_integration_principal)],
    responses=INTEGRATION_PROBLEM_RESPONSES,
)

IntegrationPrincipal = Annotated[PrincipalContext, Depends(authenticate_integration_principal)]
Database = Annotated[Session, Depends(get_db)]
ResourceId = Annotated[str, Path(json_schema_extra={"format": "uuid"})]


def _parse_id(value: str, resource: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise not_found(resource) from None


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def translate_lifecycle_error(error: Exception) -> IntegrationProblem:
    if isinstance(error, LifecycleNotFound):
        return IntegrationProblem(404, error.code, "Not found", error.detail)
    if isinstance(error, LifecycleConflict):
        return IntegrationProblem(409, error.code, "Lifecycle conflict", error.detail)
    if isinstance(error, LifecycleInvariantError):
        return IntegrationProblem(
            503,
            error.code,
            "Lifecycle unavailable",
            error.detail,
            retryable=error.code == "deliverables_unavailable",
        )
    raise error


def review_body(snapshot: lifecycle_service.ReviewSnapshot) -> dict[str, Any]:
    review = snapshot.review
    return {
        "id": str(review.id),
        "object": "review",
        "jobId": str(review.job_id),
        "state": review.state,
        "version": review.version,
        "locked": review.locked_at is not None,
        "sceneCount": snapshot.scene_count,
        "decidedSceneCount": snapshot.decided_scene_count,
        "approvedSceneCount": snapshot.approved_scene_count,
        "rejectedSceneCount": snapshot.rejected_scene_count,
        "zeroAdConfirmed": review.zero_ad_confirmed_at is not None,
        "lockedAt": _iso(review.locked_at),
        "completedAt": _iso(review.completed_at),
        "expiresAt": _iso(review.inactivity_expires_at),
        "createdAt": _iso(review.created_at),
        "updatedAt": _iso(review.updated_at),
    }


def render_body(render) -> dict[str, Any]:
    return {
        "id": str(render.id),
        "object": "render",
        "jobId": str(render.job_id),
        "reviewId": str(render.review_id),
        "state": render.state,
        "attemptCount": render.attempt_count,
        "error": {"code": render.error_code} if render.error_code is not None else None,
        "createdAt": _iso(render.created_at),
        "updatedAt": _iso(render.updated_at),
        "startedAt": _iso(render.started_at),
        "completedAt": _iso(render.completed_at),
    }


def deliverables_body(rows) -> dict[str, Any]:
    return {
        "items": [
            {
                "id": str(row.id),
                "jobId": str(row.job_id),
                "kind": row.format,
                "fileName": DELIVERABLE_FILE_NAMES[row.format],
                "contentType": row.content_type,
                "byteSize": row.size_bytes,
                "sha256": row.checksum_sha256,
                "createdAt": _iso(row.created_at),
            }
            for row in rows
        ],
        "completedSet": True,
    }


@router.get(
    "/jobs/{jobId}/review",
    response_model=IntegrationReviewResponse,
    operation_id="getReview",
)
def get_review(
    jobId: ResourceId,
    principal: IntegrationPrincipal,
    db: Database,
) -> dict[str, Any]:
    require_scope(principal, "jobs:read")
    try:
        snapshot = lifecycle_service.get_review_snapshot(
            db,
            principal,
            _parse_id(jobId, "Review"),
        )
    except (LifecycleNotFound, LifecycleConflict, LifecycleInvariantError) as exc:
        raise translate_lifecycle_error(exc) from None
    except SQLAlchemyError:
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "Review could not be loaded.",
            retryable=True,
        ) from None
    return review_body(snapshot)


@router.get(
    "/jobs/{jobId}/render",
    response_model=IntegrationRenderResponse,
    operation_id="getRender",
)
def get_render(
    jobId: ResourceId,
    principal: IntegrationPrincipal,
    db: Database,
) -> dict[str, Any]:
    require_scope(principal, "jobs:read")
    try:
        render = lifecycle_service.get_render(db, principal, _parse_id(jobId, "Render"))
    except (LifecycleNotFound, LifecycleConflict, LifecycleInvariantError) as exc:
        raise translate_lifecycle_error(exc) from None
    except SQLAlchemyError:
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "Render could not be loaded.",
            retryable=True,
        ) from None
    return render_body(render)


@router.get(
    "/jobs/{jobId}/deliverables",
    response_model=IntegrationDeliverablesResponse,
    operation_id="listDeliverables",
    openapi_extra={"x-sdk-public": True},
)
def list_deliverables(
    jobId: ResourceId,
    principal: IntegrationPrincipal,
    db: Database,
) -> dict[str, Any]:
    require_scope(principal, "deliverables:read")
    try:
        rows = lifecycle_service.list_published_deliverables(
            db,
            principal,
            _parse_id(jobId, "Job"),
        )
    except (LifecycleNotFound, LifecycleConflict, LifecycleInvariantError) as exc:
        raise translate_lifecycle_error(exc) from None
    except SQLAlchemyError:
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "Deliverables could not be loaded.",
            retryable=True,
        ) from None
    return deliverables_body(rows)


@router.get(
    "/deliverables/{deliverableId}/content",
    status_code=303,
    response_class=RedirectResponse,
    operation_id="getDeliverableContent",
    openapi_extra={"x-sdk-public": True},
    responses={
        303: {
            "description": "Version-pinned short-lived signed location",
            "headers": {
                "Location": {
                    "required": True,
                    "schema": {"type": "string", "format": "uri"},
                }
            },
        }
    },
)
def get_deliverable_content(
    deliverableId: ResourceId,
    principal: IntegrationPrincipal,
    db: Database,
) -> RedirectResponse:
    require_scope(principal, "deliverables:read")
    try:
        deliverable = lifecycle_service.get_download_target(
            db,
            principal,
            _parse_id(deliverableId, "Deliverable"),
        )
    except (LifecycleNotFound, LifecycleConflict, LifecycleInvariantError) as exc:
        raise translate_lifecycle_error(exc) from None
    except SQLAlchemyError:
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "Deliverable metadata could not be loaded.",
            retryable=True,
        ) from None
    try:
        location = generate_download_url(
            deliverable.object_key,
            version_id=deliverable.version_id,
            expires_in=get_settings().download_presign_expiry_secs,
        )
    except (BotoCoreError, ClientError):
        raise IntegrationProblem(
            503,
            "download_unavailable",
            "Download unavailable",
            "A signed download location could not be created.",
            retryable=True,
        ) from None
    return RedirectResponse(
        location,
        status_code=303,
        headers={"Cache-Control": "private, no-store", "Pragma": "no-cache"},
    )
