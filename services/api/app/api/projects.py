"""Protected, conflict-safe durable project metadata routes."""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.rfc3339 import utc_timestamp
from app.core.tenancy import PORTFOLIO_ORGANIZATION_ID
from app.db.session import get_db
from app.repositories.projects import (
    ProjectNotFoundError,
    StaleProjectVersionError,
    update_project,
)
from app.schemas.projects import ProjectPatch, ProjectResponse

logger = logging.getLogger("app.projects")
router = APIRouter()


def _http_error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


@router.patch(
    "/projects/{project_id}", response_model=ProjectResponse, response_model_by_alias=True
)
def patch_project(
    project_id: str, payload: ProjectPatch, db: Session = Depends(get_db)
) -> JSONResponse:
    try:
        parsed = uuid.UUID(project_id)
    except ValueError:
        raise _http_error(404, "not_found", "not found") from None

    try:
        project = update_project(
            db,
            parsed,
            PORTFOLIO_ORGANIZATION_ID,
            expected_version=payload.expected_version,
            column_values=payload.column_values(),
        )
    except ProjectNotFoundError:
        db.rollback()
        raise _http_error(404, "not_found", "not found") from None
    except StaleProjectVersionError:
        db.rollback()
        raise _http_error(409, "stale_version", "resource changed; refresh and retry") from None
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        logger.warning("project_update_failed category=database")
        raise _http_error(503, "persistence_unavailable", "persistence unavailable") from None

    body = ProjectResponse.model_validate(
        {
            "projectId": str(project.id),
            "name": project.name,
            "starred": project.starred,
            "version": project.version,
            "updatedAt": utc_timestamp(project.updated_at),
        }
    ).model_dump(by_alias=True, mode="json")
    return JSONResponse(content=body, headers={"Cache-Control": "private, no-store"})
