"""Minimal human browser surface called only by the Next server-side BFF."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Annotated, Any

import sqlalchemy as sa
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.browser.auth import (
    BrowserPrincipal,
    authenticate_browser_principal,
    require_browser_access_principal,
    require_browser_review_principal,
    require_browser_scene_principal,
    require_browser_upload_principal,
)
from app.api.integrations.lifecycle import deliverables_body, render_body, review_body
from app.api.integrations.problems import INTEGRATION_PROBLEM_RESPONSES, IntegrationProblem
from app.api.integrations.v1 import (
    cancel_job_for_principal,
    complete_upload_for_principal,
    create_job_for_principal,
    get_job_for_principal,
)
from app.api.jobs import mirror_investigation_upload_acceptance
from app.api.manifest import get_manifest_for_organization
from app.api.scenes import get_overrides_for_organization, patch_scene_for_organization
from app.core.config import get_settings
from app.core.tenancy import PrincipalContext
from app.db.session import get_db
from app.domain.states import JobState
from app.models.identity import Organization
from app.models.job import Job
from app.models.project import Project
from app.repositories.projects import (
    ProjectNotFoundError,
    StaleProjectVersionError,
    update_project,
)
from app.schemas.browser import (
    BrowserFinishReviewRequest,
    BrowserFinishReviewResponse,
    BrowserOverridesResponse,
    BrowserProjectListResponse,
    BrowserProjectSummary,
    BrowserScenePatchResponse,
    BrowserSessionResponse,
    BrowserTtsPreviewRequest,
    BrowserTtsPreviewResponse,
)
from app.schemas.integrations import (
    IntegrationJobCreate,
    IntegrationJobCreateResponse,
    IntegrationJobResponse,
)
from app.schemas.lifecycle import (
    IntegrationDeliverablesResponse,
    IntegrationRenderResponse,
    IntegrationReviewResponse,
)
from app.schemas.manifest import ManifestResponse
from app.schemas.projects import ProjectPatch, ProjectResponse
from app.schemas.scenes import SceneOverridePatch
from app.services import idempotency
from app.services import lifecycle as lifecycle_service
from app.services.audit import append_succeeded
from app.services.idempotency import IdempotencyClaim, IdempotencyError
from app.services.lifecycle import LifecycleConflict, LifecycleInvariantError, LifecycleNotFound
from app.services.s3 import generate_download_url
from app.services.tts_previews import (
    PreviewConflict,
    PreviewNotFound,
    create_preview,
    get_preview,
    get_preview_content,
)

router = APIRouter(prefix="/api/app/v1", tags=["browser-app"])
_MAX_BROWSER_PROJECTS = 10_000

BrowserReviewPrincipal = Annotated[
    BrowserPrincipal,
    Depends(require_browser_review_principal),
]
BrowserHumanPrincipal = Annotated[
    BrowserPrincipal,
    Depends(require_browser_access_principal),
]
BrowserUploadPrincipal = Annotated[
    BrowserPrincipal,
    Depends(require_browser_upload_principal),
]
BrowserScenePrincipal = Annotated[
    BrowserPrincipal,
    Depends(require_browser_scene_principal),
]
BrowserIdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=255,
        description="Organization-scoped safe-retry key for this browser write",
    ),
]

_PROJECT_STATUS: dict[JobState, str] = {
    JobState.AWAITING_UPLOAD: "confirmation_pending",
    JobState.UPLOAD_COMPLETE: "processing",
    JobState.QUEUED: "processing",
    JobState.PROCESSING: "processing",
    JobState.READY_FOR_REVIEW: "ready",
    JobState.EXPORT_QUEUED: "processing",
    JobState.EXPORTING: "processing",
    JobState.COMPLETED: "ready",
    JobState.FAILED: "failed",
    JobState.CANCELLED: "failed",
}


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _service_principal(principal: BrowserPrincipal) -> PrincipalContext:
    return PrincipalContext(
        organization_id=principal.organization_id,
        principal_id=principal.principal_id,
        principal_type="human",
        scopes=frozenset(),
    )


def _canonical_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _canonical_json(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonical_json(item) for item in value]
    return value


def _private_json(
    body: dict[str, Any],
    *,
    status_code: int = 200,
    replayed: bool = False,
    extra_headers: dict[str, str] | None = None,
) -> JSONResponse:
    headers = {"Cache-Control": "private, no-store"}
    if replayed:
        headers["Idempotent-Replayed"] = "true"
    if extra_headers:
        headers.update(extra_headers)
    return JSONResponse(
        status_code=status_code,
        content=_canonical_json(body),
        headers=headers,
    )


def _preview_body(preview) -> dict[str, Any]:
    return BrowserTtsPreviewResponse(
        previewId=str(preview.id),
        jobId=str(preview.job_id),
        sceneId=preview.scene_id,
        state=preview.state,
        contentReady=preview.state == "completed",
        errorCode=preview.error_code if preview.state == "failed" else None,
        createdAt=_iso_utc(preview.created_at),
        updatedAt=_iso_utc(preview.updated_at),
        expiresAt=_iso_utc(preview.expires_at),
    ).model_dump(mode="json", by_alias=True)


def _preview_problem(exc: Exception) -> IntegrationProblem:
    if isinstance(exc, PreviewNotFound):
        return IntegrationProblem(404, "not_found", "Not found", "Resource was not found.")
    if isinstance(exc, PreviewConflict):
        if exc.code in {
            "tts_preview_job_limit_exceeded",
            "tts_preview_organization_limit_exceeded",
        }:
            return IntegrationProblem(429, exc.code, "TTS preview limit exceeded", exc.detail)
        return IntegrationProblem(409, exc.code, "Preview conflict", exc.detail)
    raise exc


def _project_etag(project_id: str | uuid.UUID, version: int) -> str:
    return f'"project-{project_id}-v{version}"'


def _legacy_problem(exc: HTTPException, *, resource: str = "Job") -> IntegrationProblem:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    code = detail.get("code") if isinstance(detail.get("code"), str) else "request_failed"
    message = detail.get("message") if isinstance(detail.get("message"), str) else "Request failed."
    if exc.status_code == 404:
        code = "not_found"
        message = f"{resource} was not found."
    return IntegrationProblem(
        exc.status_code,
        code,
        HTTPStatus(exc.status_code).phrase,
        message,
        retryable=exc.status_code >= 500,
    )


def _lifecycle_problem(exc: Exception) -> IntegrationProblem:
    if isinstance(exc, LifecycleNotFound):
        return IntegrationProblem(404, "not_found", "Not found", "Job was not found.")
    if isinstance(exc, LifecycleConflict):
        return IntegrationProblem(409, exc.code, "Lifecycle conflict", exc.detail)
    if isinstance(exc, LifecycleInvariantError):
        return IntegrationProblem(
            503,
            exc.code,
            "Lifecycle unavailable",
            exc.detail,
            retryable=True,
        )
    raise exc


def _claim_browser_write(
    db: Session,
    principal: BrowserPrincipal,
    request: Request,
    idempotency_key: str,
    body: dict[str, Any],
) -> IdempotencyClaim:
    try:
        return idempotency.claim(
            db,
            principal.organization_id,
            key=idempotency_key,
            method=request.method,
            path=request.url.path,
            body=body,
        )
    except IdempotencyError as exc:
        raise _idempotency_problem(exc) from None
    except SQLAlchemyError:
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "The request could not be persisted.",
            retryable=True,
        ) from None


@router.get(
    "/session",
    response_model=BrowserSessionResponse,
    response_model_by_alias=True,
    operation_id="getBrowserSession",
)
def get_browser_session(
    response: Response,
    principal: Annotated[BrowserPrincipal, Depends(authenticate_browser_principal)],
) -> BrowserSessionResponse:
    response.headers["Cache-Control"] = "private, no-store"
    return BrowserSessionResponse(
        subject=principal.subject,
        email=principal.email,
        displayName=principal.display_name,
        organizationId=str(principal.organization_id),
        role=principal.role,
        mfaVerified=principal.mfa_verified,
    )


@router.get(
    "/projects",
    response_model=BrowserProjectListResponse,
    response_model_by_alias=True,
    operation_id="listBrowserProjects",
)
def list_browser_projects(
    response: Response,
    principal: BrowserHumanPrincipal,
    db: Session = Depends(get_db),
) -> BrowserProjectListResponse:
    latest_jobs = (
        sa.select(
            Job.organization_id.label("organization_id"),
            Job.project_id.label("project_id"),
            Job.id.label("job_id"),
            Job.status.label("job_status"),
            Job.updated_at.label("job_updated_at"),
            sa.func.row_number()
            .over(
                partition_by=(Job.organization_id, Job.project_id),
                order_by=(Job.created_at.desc(), Job.id.desc()),
            )
            .label("row_number"),
        )
        .where(
            Job.organization_id == principal.organization_id,
            Job.workflow_kind == "audio_description",
        )
        .subquery()
    )
    all_jobs = (
        sa.select(
            Job.organization_id.label("organization_id"),
            Job.project_id.label("project_id"),
            sa.func.count(Job.id).label("job_count"),
        )
        .where(Job.organization_id == principal.organization_id)
        .group_by(Job.organization_id, Job.project_id)
        .subquery()
    )
    effective_updated_at = sa.func.coalesce(
        sa.func.greatest(Project.updated_at, latest_jobs.c.job_updated_at),
        Project.updated_at,
    )
    statement = (
        sa.select(
            Project.id,
            Project.name,
            Project.updated_at,
            Organization.slug,
            latest_jobs.c.job_id,
            latest_jobs.c.job_status,
            latest_jobs.c.job_updated_at,
        )
        .join(
            Organization,
            sa.and_(
                Organization.id == Project.organization_id,
                Organization.is_active.is_(True),
            ),
        )
        .outerjoin(
            latest_jobs,
            sa.and_(
                latest_jobs.c.organization_id == Project.organization_id,
                latest_jobs.c.project_id == Project.id,
                latest_jobs.c.row_number == 1,
            ),
        )
        .outerjoin(
            all_jobs,
            sa.and_(
                all_jobs.c.organization_id == Project.organization_id,
                all_jobs.c.project_id == Project.id,
            ),
        )
        .where(
            Project.organization_id == principal.organization_id,
            sa.or_(latest_jobs.c.job_id.is_not(None), all_jobs.c.job_count.is_(None)),
        )
        .order_by(effective_updated_at.desc(), Project.id.desc())
        .limit(_MAX_BROWSER_PROJECTS + 1)
    )
    try:
        rows = db.execute(statement).all()
    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail="Projects are temporarily unavailable.",
        ) from None
    if len(rows) > _MAX_BROWSER_PROJECTS:
        raise HTTPException(
            status_code=503,
            detail="Projects are temporarily unavailable.",
        )

    data: list[BrowserProjectSummary] = []
    for row in rows:
        updated_at = row.updated_at
        if row.job_updated_at is not None and row.job_updated_at > updated_at:
            updated_at = row.job_updated_at
        try:
            status = (
                "draft" if row.job_status is None else _PROJECT_STATUS[JobState(row.job_status)]
            )
        except (KeyError, ValueError):
            raise HTTPException(
                status_code=503,
                detail="Projects are temporarily unavailable.",
            ) from None
        data.append(
            BrowserProjectSummary(
                id=str(row.id),
                orgSlug=row.slug,
                currentJobId=str(row.job_id) if row.job_id is not None else None,
                name=row.name,
                status=status,
                updatedAt=_iso_utc(updated_at),
            )
        )
    response.headers["Cache-Control"] = "private, no-store"
    return BrowserProjectListResponse(data=data)


@router.patch(
    "/projects/{project_id}",
    response_model=ProjectResponse,
    response_model_by_alias=True,
    operation_id="patchBrowserProject",
)
def patch_browser_project(
    project_id: str,
    payload: ProjectPatch,
    request: Request,
    principal: BrowserUploadPrincipal,
    idempotency_key: BrowserIdempotencyKey,
    db: Session = Depends(get_db),
) -> JSONResponse:
    try:
        parsed = uuid.UUID(project_id)
    except ValueError:
        raise IntegrationProblem(
            404,
            "not_found",
            "Not found",
            "Project was not found.",
        ) from None
    request_body = payload.model_dump(mode="json", by_alias=True, exclude_unset=True)
    claim = _claim_browser_write(db, principal, request, idempotency_key, request_body)
    if claim.is_replay:
        body = ProjectResponse.model_validate(claim.replay_body or {}).model_dump(
            mode="json",
            by_alias=True,
        )
        return _private_json(
            body,
            status_code=claim.replay_status or 200,
            replayed=True,
            extra_headers={"ETag": _project_etag(body["projectId"], body["version"])},
        )
    try:
        project = update_project(
            db,
            parsed,
            principal.organization_id,
            expected_version=payload.expected_version,
            column_values=payload.column_values(),
            commit_transaction=False,
        )
        body = ProjectResponse.model_validate(
            {
                "projectId": str(project.id),
                "name": project.name,
                "starred": project.starred,
                "version": project.version,
                "updatedAt": _iso_utc(project.updated_at),
            }
        ).model_dump(mode="json", by_alias=True)
        append_succeeded(
            db,
            _service_principal(principal),
            action="project.updated",
            resource_id=project.id,
            request_id=getattr(request.state, "request_id", None),
        )
        idempotency.complete(db, claim, status=200, body=body)
    except ProjectNotFoundError:
        db.rollback()
        raise IntegrationProblem(
            404,
            "not_found",
            "Not found",
            "Project was not found.",
        ) from None
    except StaleProjectVersionError:
        db.rollback()
        raise IntegrationProblem(
            409,
            "stale_version",
            "Project conflict",
            "The project changed; refresh and retry.",
        ) from None
    except SQLAlchemyError:
        db.rollback()
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "The project update could not be persisted.",
            retryable=True,
        ) from None
    return _private_json(
        body,
        extra_headers={"ETag": _project_etag(body["projectId"], body["version"])},
    )


@router.post(
    "/jobs",
    status_code=201,
    response_model=IntegrationJobCreateResponse,
    operation_id="createBrowserJob",
)
def create_browser_job(
    payload: IntegrationJobCreate,
    request: Request,
    principal: BrowserUploadPrincipal,
    idempotency_key: BrowserIdempotencyKey,
    db: Session = Depends(get_db),
) -> JSONResponse:
    response = create_job_for_principal(
        payload,
        request,
        _service_principal(principal),
        db,
        idempotency_key,
    )
    response.headers["Cache-Control"] = "private, no-store"
    return response


@router.get(
    "/jobs/{job_id}",
    response_model=IntegrationJobResponse,
    operation_id="getBrowserJob",
)
def get_browser_job(
    job_id: str,
    response: Response,
    principal: BrowserHumanPrincipal,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    body = get_job_for_principal(job_id, _service_principal(principal), db)
    response.headers["Cache-Control"] = "private, no-store"
    return body


@router.post(
    "/jobs/{job_id}/uploads/complete",
    status_code=202,
    response_model=IntegrationJobResponse,
    responses={
        200: {"model": IntegrationJobResponse, "description": "Already accepted"},
        422: INTEGRATION_PROBLEM_RESPONSES[422],
    },
    operation_id="completeBrowserUpload",
)
def complete_browser_upload(
    job_id: str,
    request: Request,
    principal: BrowserUploadPrincipal,
    idempotency_key: BrowserIdempotencyKey,
    db: Session = Depends(get_db),
) -> JSONResponse:
    try:
        parsed_job_id = uuid.UUID(job_id)
    except ValueError:
        parsed_job_id = None
    if parsed_job_id is not None and parsed_job_id.int != 0:
        try:
            # Repairs the only possible split-brain left by a pre-pivot API
            # process before an idempotency replay can return early.
            if mirror_investigation_upload_acceptance(
                db,
                parsed_job_id,
                principal.organization_id,
            ):
                db.commit()
        except HTTPException as exc:
            raise IntegrationProblem(
                exc.status_code,
                "investigation_state_conflict",
                "Investigation conflict",
                "The investigation upload state could not be reconciled.",
            ) from None
        except SQLAlchemyError:
            db.rollback()
            raise IntegrationProblem(
                503,
                "persistence_unavailable",
                "Persistence unavailable",
                "The investigation upload state could not be reconciled.",
                retryable=True,
            ) from None
    response = complete_upload_for_principal(
        job_id,
        request,
        _service_principal(principal),
        db,
        idempotency_key,
        allow_video_investigation=True,
    )
    response.headers["Cache-Control"] = "private, no-store"
    return response


@router.post(
    "/jobs/{job_id}/cancel",
    response_model=IntegrationJobResponse,
    operation_id="cancelBrowserJob",
)
def cancel_browser_job(
    job_id: str,
    request: Request,
    principal: BrowserUploadPrincipal,
    idempotency_key: BrowserIdempotencyKey,
    db: Session = Depends(get_db),
) -> JSONResponse:
    response = cancel_job_for_principal(
        job_id,
        request,
        _service_principal(principal),
        db,
        idempotency_key,
    )
    response.headers["Cache-Control"] = "private, no-store"
    return response


@router.get(
    "/jobs/{job_id}/manifest",
    response_model=ManifestResponse,
    response_model_by_alias=True,
    operation_id="getBrowserManifest",
)
def get_browser_manifest(
    job_id: str,
    principal: BrowserHumanPrincipal,
    db: Session = Depends(get_db),
) -> JSONResponse:
    try:
        return get_manifest_for_organization(job_id, principal.organization_id, db)
    except HTTPException as exc:
        raise _legacy_problem(exc) from None


@router.get(
    "/jobs/{job_id}/overrides",
    response_model=BrowserOverridesResponse,
    response_model_by_alias=True,
    response_model_exclude_none=True,
    operation_id="getBrowserOverrides",
)
def get_browser_overrides(
    job_id: str,
    principal: BrowserHumanPrincipal,
    db: Session = Depends(get_db),
) -> JSONResponse:
    try:
        body = get_overrides_for_organization(job_id, principal.organization_id, db)
    except HTTPException as exc:
        raise _legacy_problem(exc) from None
    return _private_json(body)


@router.patch(
    "/jobs/{job_id}/scenes/{scene_id}",
    response_model=BrowserScenePatchResponse,
    response_model_by_alias=True,
    response_model_exclude_none=True,
    operation_id="patchBrowserScene",
)
def patch_browser_scene(
    job_id: str,
    scene_id: str,
    payload: SceneOverridePatch,
    request: Request,
    principal: BrowserScenePrincipal,
    idempotency_key: BrowserIdempotencyKey,
    db: Session = Depends(get_db),
) -> JSONResponse:
    request_body = payload.model_dump(mode="json", by_alias=True, exclude_unset=True)
    claim = _claim_browser_write(db, principal, request, idempotency_key, request_body)
    if claim.is_replay:
        body = BrowserScenePatchResponse.model_validate(claim.replay_body or {}).model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        )
        return _private_json(
            body,
            status_code=claim.replay_status or 200,
            replayed=True,
        )

    service_principal = _service_principal(principal)
    try:
        result = patch_scene_for_organization(
            job_id,
            scene_id,
            payload,
            principal.organization_id,
            db,
            commit_transaction=False,
        )
        body = BrowserScenePatchResponse.model_validate(result.body).model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        )
        append_succeeded(
            db,
            service_principal,
            action="scene.updated",
            resource_id=result.override_id,
            request_id=getattr(request.state, "request_id", None),
        )
        idempotency.complete(db, claim, status=200, body=body)
    except HTTPException as exc:
        db.rollback()
        raise _legacy_problem(exc) from None
    except SQLAlchemyError:
        db.rollback()
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "The scene update could not be persisted.",
            retryable=True,
        ) from None
    return _private_json(body)


@router.post(
    "/jobs/{job_id}/scenes/{scene_id}/tts-previews",
    status_code=202,
    response_model=BrowserTtsPreviewResponse,
    response_model_by_alias=True,
    operation_id="createBrowserTtsPreview",
)
def create_browser_tts_preview(
    job_id: str,
    scene_id: str,
    payload: BrowserTtsPreviewRequest,
    request: Request,
    principal: BrowserScenePrincipal,
    idempotency_key: BrowserIdempotencyKey,
    db: Session = Depends(get_db),
) -> JSONResponse:
    request_body = payload.model_dump(mode="json")
    claim = _claim_browser_write(db, principal, request, idempotency_key, request_body)
    if claim.is_replay:
        body = BrowserTtsPreviewResponse.model_validate(claim.replay_body or {}).model_dump(
            mode="json", by_alias=True
        )
        return _private_json(
            body,
            status_code=claim.replay_status or 202,
            replayed=True,
        )
    service_principal = _service_principal(principal)
    try:
        preview = create_preview(
            db,
            service_principal,
            _parse_job_id(job_id),
            scene_id=scene_id,
            text=payload.text,
            voice=payload.voice,
            speed=payload.speed,
        )
        body = _preview_body(preview)
        append_succeeded(
            db,
            service_principal,
            action="tts_preview.created",
            resource_id=preview.id,
            request_id=getattr(request.state, "request_id", None),
        )
        idempotency.complete(db, claim, status=202, body=body)
    except (PreviewNotFound, PreviewConflict) as exc:
        db.rollback()
        raise _preview_problem(exc) from None
    except IntegrityError:
        # The partial unique index closes a concurrent same-scene race after
        # the organization capacity check. Never leak which competing row won.
        db.rollback()
        raise IntegrationProblem(
            409,
            "preview_in_progress",
            "Preview conflict",
            "A TTS preview for this scene is already in progress.",
        ) from None
    except SQLAlchemyError:
        db.rollback()
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "The TTS preview request could not be persisted.",
            retryable=True,
        ) from None
    return _private_json(body, status_code=202)


@router.get(
    "/tts-previews/{preview_id}",
    response_model=BrowserTtsPreviewResponse,
    response_model_by_alias=True,
    operation_id="getBrowserTtsPreview",
)
def get_browser_tts_preview(
    preview_id: str,
    principal: BrowserScenePrincipal,
    db: Session = Depends(get_db),
) -> JSONResponse:
    try:
        preview = get_preview(db, _service_principal(principal), _parse_preview_id(preview_id))
    except (PreviewNotFound, PreviewConflict) as exc:
        raise _preview_problem(exc) from None
    except SQLAlchemyError:
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "The TTS preview could not be loaded.",
            retryable=True,
        ) from None
    return _private_json(_preview_body(preview))


@router.get(
    "/tts-previews/{preview_id}/content",
    status_code=303,
    response_class=RedirectResponse,
    operation_id="getBrowserTtsPreviewContent",
)
def get_browser_tts_preview_content(
    preview_id: str,
    principal: BrowserScenePrincipal,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    try:
        preview = get_preview_content(
            db,
            _service_principal(principal),
            _parse_preview_id(preview_id),
        )
    except (PreviewNotFound, PreviewConflict) as exc:
        raise _preview_problem(exc) from None
    except SQLAlchemyError:
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "The TTS preview metadata could not be loaded.",
            retryable=True,
        ) from None
    try:
        location = generate_download_url(
            preview.object_key or "",
            version_id=preview.version_id,
            expires_in=get_settings().download_presign_expiry_secs,
        )
    except (BotoCoreError, ClientError):
        raise IntegrationProblem(
            503,
            "preview_unavailable",
            "Preview unavailable",
            "A signed preview location could not be created.",
            retryable=True,
        ) from None
    return RedirectResponse(
        location,
        status_code=303,
        headers={"Cache-Control": "private, no-store", "Pragma": "no-cache"},
    )


@router.get(
    "/jobs/{job_id}/review",
    response_model=IntegrationReviewResponse,
    operation_id="getBrowserReview",
)
def get_browser_review(
    job_id: str,
    response: Response,
    principal: BrowserHumanPrincipal,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        snapshot = lifecycle_service.get_review_snapshot(
            db,
            _service_principal(principal),
            _parse_job_id(job_id),
        )
    except (LifecycleNotFound, LifecycleConflict, LifecycleInvariantError) as exc:
        raise _lifecycle_problem(exc) from None
    except SQLAlchemyError:
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "Review could not be loaded.",
            retryable=True,
        ) from None
    response.headers["Cache-Control"] = "private, no-store"
    return review_body(snapshot)


@router.get(
    "/jobs/{job_id}/render",
    response_model=IntegrationRenderResponse,
    operation_id="getBrowserRender",
)
def get_browser_render(
    job_id: str,
    response: Response,
    principal: BrowserHumanPrincipal,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        render = lifecycle_service.get_render(
            db,
            _service_principal(principal),
            _parse_job_id(job_id),
        )
    except (LifecycleNotFound, LifecycleConflict, LifecycleInvariantError) as exc:
        raise _lifecycle_problem(exc) from None
    except SQLAlchemyError:
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "Render could not be loaded.",
            retryable=True,
        ) from None
    response.headers["Cache-Control"] = "private, no-store"
    return render_body(render)


@router.get(
    "/jobs/{job_id}/deliverables",
    response_model=IntegrationDeliverablesResponse,
    operation_id="listBrowserDeliverables",
)
def list_browser_deliverables(
    job_id: str,
    response: Response,
    principal: BrowserHumanPrincipal,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        rows = lifecycle_service.list_published_deliverables(
            db,
            _service_principal(principal),
            _parse_job_id(job_id),
        )
    except (LifecycleNotFound, LifecycleConflict, LifecycleInvariantError) as exc:
        raise _lifecycle_problem(exc) from None
    except SQLAlchemyError:
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "Deliverables could not be loaded.",
            retryable=True,
        ) from None
    response.headers["Cache-Control"] = "private, no-store"
    return deliverables_body(rows)


@router.get(
    "/deliverables/{deliverable_id}/content",
    status_code=303,
    response_class=RedirectResponse,
    operation_id="getBrowserDeliverableContent",
)
def get_browser_deliverable_content(
    deliverable_id: str,
    principal: BrowserHumanPrincipal,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    try:
        parsed = uuid.UUID(deliverable_id)
    except ValueError:
        raise IntegrationProblem(
            404,
            "not_found",
            "Not found",
            "Deliverable was not found.",
        ) from None
    try:
        deliverable = lifecycle_service.get_download_target(
            db,
            _service_principal(principal),
            parsed,
        )
    except LifecycleNotFound:
        raise IntegrationProblem(
            404,
            "not_found",
            "Not found",
            "Deliverable was not found.",
        ) from None
    except (LifecycleConflict, LifecycleInvariantError) as exc:
        raise _lifecycle_problem(exc) from None
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


def _parse_job_id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise IntegrationProblem(
            404,
            "not_found",
            "Not found",
            "Job was not found.",
        ) from None


def _parse_preview_id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise IntegrationProblem(
            404,
            "not_found",
            "Not found",
            "Resource was not found.",
        ) from None


def _idempotency_problem(error: IdempotencyError) -> IntegrationProblem:
    details = {
        "invalid_idempotency_key": (
            400,
            "Invalid idempotency key",
            "The idempotency key must contain 1-255 visible ASCII characters.",
        ),
        "idempotency_key_reused": (
            409,
            "Idempotency key reused",
            "The idempotency key was already used for a different request.",
        ),
        "idempotency_in_progress": (
            409,
            "Request in progress",
            "A request with this idempotency key is still in progress.",
        ),
    }
    status, title, detail = details[error.code]
    return IntegrationProblem(
        status,
        error.code,
        title,
        detail,
        retryable=error.code == "idempotency_in_progress",
    )


def _finish_body(result: lifecycle_service.FinishReviewResult) -> dict[str, object]:
    return BrowserFinishReviewResponse(
        jobId=str(result.review.job_id),
        reviewId=str(result.review.id),
        renderId=str(result.render.id),
        reviewState="completed",
        renderState="queued",
        idempotent=result.idempotent,
    ).model_dump(mode="json")


def _stable_finish_body(body: dict[str, object]) -> dict[str, object]:
    return BrowserFinishReviewResponse.model_validate(body).model_dump(mode="json")


@router.post(
    "/jobs/{job_id}/review/finish",
    response_model=BrowserFinishReviewResponse,
    operation_id="finishBrowserReview",
)
def finish_browser_review(
    job_id: str,
    payload: BrowserFinishReviewRequest,
    request: Request,
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=255,
            description="Organization-scoped safe-retry key for Finish Review",
        ),
    ],
    principal: BrowserReviewPrincipal,
    db: Session = Depends(get_db),
) -> JSONResponse:
    parsed_job_id = _parse_job_id(job_id)
    request_body = payload.model_dump(mode="json", by_alias=True)
    try:
        claim = idempotency.claim(
            db,
            principal.organization_id,
            key=idempotency_key,
            method=request.method,
            path=request.url.path,
            body=request_body,
        )
    except IdempotencyError as exc:
        raise _idempotency_problem(exc) from None
    except SQLAlchemyError:
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "The review request could not be persisted.",
            retryable=True,
        ) from None
    if claim.is_replay:
        return JSONResponse(
            status_code=claim.replay_status or 200,
            content=_stable_finish_body(claim.replay_body or {}),
            headers={
                "Cache-Control": "private, no-store",
                "Idempotent-Replayed": "true",
            },
        )

    service_principal = PrincipalContext(
        organization_id=principal.organization_id,
        principal_id=principal.principal_id,
        principal_type="human",
        scopes=frozenset(),
    )
    try:
        result = lifecycle_service.finish_review(
            db,
            service_principal,
            parsed_job_id,
            zero_ad_confirmed=payload.zero_ad_confirmed,
            commit_transaction=False,
        )
        body = _finish_body(result)
        if not result.idempotent:
            append_succeeded(
                db,
                service_principal,
                action="review.finished",
                resource_id=result.review.id,
                request_id=getattr(request.state, "request_id", None),
            )
        idempotency.complete(db, claim, status=200, body=body)
    except LifecycleNotFound as exc:
        raise IntegrationProblem(404, "not_found", "Not found", exc.detail) from None
    except LifecycleConflict as exc:
        raise IntegrationProblem(409, exc.code, "Lifecycle conflict", exc.detail) from None
    except LifecycleInvariantError as exc:
        raise IntegrationProblem(
            503,
            exc.code,
            "Lifecycle unavailable",
            exc.detail,
            retryable=True,
        ) from None
    except SQLAlchemyError:
        db.rollback()
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "The review could not be completed.",
            retryable=True,
        ) from None
    return JSONResponse(
        status_code=200,
        content=body,
        headers={"Cache-Control": "private, no-store"},
    )
