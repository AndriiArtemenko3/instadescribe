"""Authenticated API-first beta surface for InstaDescribe integrations."""

import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import quote

import sqlalchemy as sa
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from instadescribe_contracts.provider import (
    TTS_BETA_MAX_ACTIVE_PREVIEWS_PER_ORGANIZATION,
    TTS_BETA_MAX_APPROVED_SCENES_PER_REVIEW,
    TTS_BETA_MAX_FINAL_SYNTHESIS_CALLS_PER_REVIEW,
    TTS_BETA_MAX_PREVIEW_ATTEMPTS_PER_REQUEST,
    TTS_BETA_MAX_PREVIEW_REQUESTS_PER_JOB,
    TTS_BETA_MAX_PREVIEW_REQUESTS_PER_ORGANIZATION,
    TTS_BETA_MAX_RENDER_ATTEMPTS_PER_REVIEW,
    TTS_BETA_PREVIEW_WINDOW_SECS,
)
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.integrations.auth import (
    authenticate_integration_principal,
    require_scope,
)
from app.api.integrations.pagination import decode_cursor, encode_cursor
from app.api.integrations.problems import (
    INTEGRATION_PROBLEM_RESPONSES,
    IntegrationProblem,
    not_found,
)
from app.api.jobs import complete_upload_for_organization, persist_verified_source
from app.core.config import get_settings
from app.core.rfc3339 import utc_timestamp
from app.core.tenancy import PrincipalContext
from app.db.session import get_db
from app.domain.public_states import PublicJobState, to_public_state
from app.domain.states import JobState
from app.models import Asset, IdempotencyRecord, Job, JobEvent, Organization, Project
from app.repositories.jobs import transition_job
from app.schemas.integrations import (
    MAX_TRANSCRIPT_BYTES,
    IntegrationCapabilitiesResponse,
    IntegrationJobCreate,
    IntegrationJobCreateResponse,
    IntegrationJobListResponse,
    IntegrationJobResponse,
    IntegrationNestedJobCreate,
    IntegrationOrganizationResponse,
    IntegrationProjectCreate,
    IntegrationProjectListResponse,
    IntegrationProjectPatch,
    IntegrationProjectResponse,
)
from app.services import idempotency
from app.services.audit import append_succeeded
from app.services.idempotency import IdempotencyClaim, IdempotencyError
from app.services.quota import QuotaExceededError, QuotaStateError, reserve_job_media
from app.services.s3 import generate_upload_post, head_source

logger = logging.getLogger("app.integrations")

router = APIRouter(
    prefix="/api/integrations/v1",
    tags=["integrations"],
    dependencies=[Depends(authenticate_integration_principal)],
    responses=INTEGRATION_PROBLEM_RESPONSES,
)

IntegrationPrincipal = Annotated[PrincipalContext, Depends(authenticate_integration_principal)]
Database = Annotated[Session, Depends(get_db)]
IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=255,
        description="Unique key for safe retry of this organization-scoped write",
    ),
]


def _iso(value: datetime) -> str:
    return utc_timestamp(value)


def _parse_id(value: str, resource: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise not_found(resource) from None


def _project_etag(project_id: uuid.UUID | str, version: int) -> str:
    return f'"project-{project_id}-v{version}"'


def _tenant_upload_key(
    organization_id: uuid.UUID,
    job_id: uuid.UUID,
    asset: str,
    file_name: str,
) -> str:
    return f"uploads/orgs/{organization_id}/jobs/{job_id}/{asset}/{file_name}"


def _upload_body(upload: dict[str, Any]) -> dict[str, Any]:
    return {
        "method": "POST",
        "url": upload["url"],
        "fields": upload["fields"],
        "expiresAt": utc_timestamp(upload["expires_at"]),
    }


def _canonical_json(value: Any) -> Any:
    """Stabilize response bytes across PostgreSQL JSONB replay ordering."""
    if isinstance(value, dict):
        return {key: _canonical_json(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonical_json(item) for item in value]
    return value


def _json_response(
    *,
    content: dict[str, Any],
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=_canonical_json(content),
        headers=headers,
    )


def _organization_body(organization: Organization) -> dict[str, Any]:
    return {
        "id": str(organization.id),
        "object": "organization",
        "slug": organization.slug,
        "name": organization.name,
        "active": organization.is_active,
        "createdAt": _iso(organization.created_at),
        "updatedAt": _iso(organization.updated_at),
    }


def _project_body(project: Project) -> dict[str, Any]:
    return {
        "id": str(project.id),
        "object": "project",
        "name": project.name,
        "externalId": project.external_id,
        "starred": project.starred,
        "version": project.version,
        "createdAt": _iso(project.created_at),
        "updatedAt": _iso(project.updated_at),
    }


def _project_is_on_audio_description_surface() -> sa.ColumnElement[bool]:
    """Keep investigation-only projects out of the stable Integration API.

    Empty projects remain visible because the Integration API can create a
    project before its first audio-description job.  A project with jobs is
    visible only when at least one of them belongs to the legacy/public
    audio-description workflow.
    """

    any_job = sa.exists(
        sa.select(1).where(
            Job.organization_id == Project.organization_id,
            Job.project_id == Project.id,
        )
    )
    audio_description_job = sa.exists(
        sa.select(1).where(
            Job.organization_id == Project.organization_id,
            Job.project_id == Project.id,
            Job.workflow_kind == "audio_description",
        )
    )
    return sa.or_(~any_job, audio_description_job)


def _job_lookup_conditions(
    job_id: uuid.UUID,
    organization_id: uuid.UUID,
    *,
    allow_video_investigation: bool,
) -> list[sa.ColumnElement[bool]]:
    conditions: list[sa.ColumnElement[bool]] = [
        Job.id == job_id,
        Project.organization_id == organization_id,
    ]
    if not allow_video_investigation:
        conditions.append(Job.workflow_kind == "audio_description")
    return conditions


def _organization_slug(db: Session, organization_id: uuid.UUID) -> str:
    slug = db.execute(
        sa.select(Organization.slug).where(Organization.id == organization_id)
    ).scalar_one_or_none()
    if slug is None:
        raise not_found("Organization")
    return slug


def _job_body(job: Job, organization_slug: str) -> dict[str, Any]:
    settings = get_settings()
    state = to_public_state(JobState(job.status))
    review_url = None
    if state in {
        PublicJobState.NEEDS_REVIEW,
        PublicJobState.RENDERING,
        PublicJobState.COMPLETED,
    }:
        review_url = (
            f"{settings.integration_review_base_url}/orgs/{quote(organization_slug, safe='')}"
            f"/projects/{job.project_id}/jobs/{job.id}/review"
        )
    return {
        "id": str(job.id),
        "object": "job",
        "projectId": str(job.project_id),
        "clientReference": job.client_reference,
        "state": state.value,
        "progress": job.progress,
        "stage": job.stage,
        "pipelineRevision": job.pipeline_revision,
        "source": {
            "status": (
                "uploaded"
                if job.source_etag and job.source_version_id and job.upload_verified_at
                else "awaiting_upload"
            ),
            "contentType": job.input_content_type,
            "sizeBytes": job.input_size_bytes,
            "durationSeconds": (
                float(job.duration_secs) if job.duration_secs is not None else None
            ),
        },
        "reviewUrl": review_url,
        "error": (
            {"code": job.error_code, "message": job.error_message}
            if job.error_code or job.error_message
            else None
        ),
        "createdAt": _iso(job.created_at),
        "updatedAt": _iso(job.updated_at),
    }


def _claim(
    db: Session,
    principal: PrincipalContext,
    request: Request,
    key: str,
    body: dict[str, Any],
) -> IdempotencyClaim | JSONResponse:
    try:
        result = idempotency.claim(
            db,
            principal.organization_id,
            key=key,
            method=request.method,
            path=request.url.path,
            body=body,
        )
    except IdempotencyError as exc:
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
        status, title, detail = details[exc.code]
        raise IntegrationProblem(
            status,
            exc.code,
            title,
            detail,
            retryable=exc.code == "idempotency_in_progress",
        ) from None
    except SQLAlchemyError:
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "The request could not be persisted.",
            retryable=True,
        ) from None
    if result.is_replay:
        headers = {"Idempotent-Replayed": "true"}
        replay_body = result.replay_body or {}
        if replay_body.get("object") == "project" and replay_body.get("version"):
            headers["ETag"] = _project_etag(replay_body["id"], replay_body["version"])
        return _json_response(
            status_code=result.replay_status,
            content=replay_body,
            headers=headers,
        )
    return result


def _collection(rows: list[Any], limit: int, serializer) -> dict[str, Any]:
    page = rows[:limit]
    return {
        "object": "list",
        "data": [serializer(row) for row in page],
        "nextCursor": (
            encode_cursor(page[-1].created_at, page[-1].id) if len(rows) > limit and page else None
        ),
    }


@router.get(
    "/capabilities",
    response_model=IntegrationCapabilitiesResponse,
    operation_id="capabilities",
    openapi_extra={"x-sdk-public": True},
)
def capabilities(principal: IntegrationPrincipal) -> dict[str, Any]:
    settings = get_settings()
    return {
        "brand": "InstaDescribe",
        "apiVersion": "v1-beta",
        "organizationId": str(principal.organization_id),
        "resources": ["organization", "projects", "jobs"],
        "jobStates": [state.value for state in PublicJobState],
        "review": {"mode": "web"},
        "uploads": {
            "maxBytes": settings.max_upload_bytes,
            "maxDurationSeconds": settings.max_duration_secs,
            "contentTypes": list(settings.allowed_content_types),
        },
        "idempotency": {
            "requiredForWrites": True,
            "retentionSeconds": int(idempotency.IDEMPOTENCY_RETENTION.total_seconds()),
        },
        "tts": {
            "maxApprovedScenesPerReview": TTS_BETA_MAX_APPROVED_SCENES_PER_REVIEW,
            "maxRenderAttemptsPerReview": TTS_BETA_MAX_RENDER_ATTEMPTS_PER_REVIEW,
            "maxFinalSynthesisCallsPerReview": (TTS_BETA_MAX_FINAL_SYNTHESIS_CALLS_PER_REVIEW),
            "previews": {
                "rollingWindowSeconds": TTS_BETA_PREVIEW_WINDOW_SECS,
                "maxRequestsPerJob": TTS_BETA_MAX_PREVIEW_REQUESTS_PER_JOB,
                "maxRequestsPerOrganization": TTS_BETA_MAX_PREVIEW_REQUESTS_PER_ORGANIZATION,
                "maxActivePerOrganization": TTS_BETA_MAX_ACTIVE_PREVIEWS_PER_ORGANIZATION,
                "maxAttemptsPerRequest": TTS_BETA_MAX_PREVIEW_ATTEMPTS_PER_REQUEST,
            },
        },
    }


@router.get(
    "/organization",
    response_model=IntegrationOrganizationResponse,
    operation_id="getOrganization",
)
def get_organization(principal: IntegrationPrincipal, db: Database) -> dict[str, Any]:
    require_scope(principal, "organization:read")
    try:
        organization = db.execute(
            sa.select(Organization).where(Organization.id == principal.organization_id)
        ).scalar_one_or_none()
    except SQLAlchemyError:
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "The organization could not be loaded.",
            retryable=True,
        ) from None
    if organization is None:
        raise not_found("Organization")
    return _organization_body(organization)


@router.get(
    "/projects",
    response_model=IntegrationProjectListResponse,
    operation_id="listProjects",
)
def list_projects(
    principal: IntegrationPrincipal,
    db: Database,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> dict[str, Any]:
    require_scope(principal, "projects:read")
    decoded = decode_cursor(cursor)
    statement = sa.select(Project).where(
        Project.organization_id == principal.organization_id,
        _project_is_on_audio_description_surface(),
    )
    if decoded is not None:
        statement = statement.where(
            sa.tuple_(Project.created_at, Project.id)
            < sa.tuple_(decoded.created_at, decoded.resource_id)
        )
    statement = statement.order_by(Project.created_at.desc(), Project.id.desc()).limit(limit + 1)
    try:
        rows = list(db.execute(statement).scalars())
    except SQLAlchemyError:
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "Projects could not be loaded.",
            retryable=True,
        ) from None
    return _collection(rows, limit, _project_body)


@router.post(
    "/projects",
    status_code=201,
    response_model=IntegrationProjectResponse,
    operation_id="createProject",
)
def create_project(
    payload: IntegrationProjectCreate,
    request: Request,
    principal: IntegrationPrincipal,
    db: Database,
    idempotency_key: IdempotencyKey,
) -> JSONResponse:
    require_scope(principal, "projects:write")
    claim = _claim(
        db,
        principal,
        request,
        idempotency_key,
        payload.model_dump(mode="json"),
    )
    if isinstance(claim, JSONResponse):
        return claim
    try:
        project = Project(organization_id=principal.organization_id, name=payload.name)
        db.add(project)
        db.flush()
        body = _project_body(project)
        append_succeeded(
            db,
            principal,
            action="project.created",
            resource_id=project.id,
            request_id=getattr(request.state, "request_id", None),
        )
        idempotency.complete(db, claim, status=201, body=body)
    except SQLAlchemyError:
        db.rollback()
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "The project could not be created.",
            retryable=True,
        ) from None
    return _json_response(
        status_code=201,
        content=body,
        headers={"ETag": _project_etag(project.id, project.version)},
    )


@router.get(
    "/projects/{project_id}",
    response_model=IntegrationProjectResponse,
    operation_id="getProject",
)
def get_project(
    project_id: str,
    principal: IntegrationPrincipal,
    db: Database,
) -> dict[str, Any]:
    require_scope(principal, "projects:read")
    parsed = _parse_id(project_id, "Project")
    try:
        project = db.execute(
            sa.select(Project).where(
                Project.id == parsed,
                Project.organization_id == principal.organization_id,
                _project_is_on_audio_description_surface(),
            )
        ).scalar_one_or_none()
    except SQLAlchemyError:
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "The project could not be loaded.",
            retryable=True,
        ) from None
    if project is None:
        raise not_found("Project")
    return _json_response(
        content=_project_body(project),
        headers={"ETag": _project_etag(project.id, project.version)},
    )


@router.patch(
    "/projects/{project_id}",
    response_model=IntegrationProjectResponse,
    operation_id="updateProject",
)
def patch_project(
    project_id: str,
    payload: IntegrationProjectPatch,
    request: Request,
    principal: IntegrationPrincipal,
    db: Database,
    idempotency_key: IdempotencyKey,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> JSONResponse:
    require_scope(principal, "projects:write")
    parsed = _parse_id(project_id, "Project")
    if if_match is None:
        raise IntegrationProblem(
            428,
            "precondition_required",
            "Precondition required",
            "If-Match is required for project updates.",
        )
    claim = _claim(
        db,
        principal,
        request,
        idempotency_key,
        {
            "payload": payload.model_dump(mode="json", exclude_unset=True),
            "ifMatch": if_match,
        },
    )
    if isinstance(claim, JSONResponse):
        return claim
    try:
        project = db.execute(
            sa.select(Project).where(
                Project.id == parsed,
                Project.organization_id == principal.organization_id,
                _project_is_on_audio_description_surface(),
            )
        ).scalar_one_or_none()
    except SQLAlchemyError:
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "The project could not be loaded.",
            retryable=True,
        ) from None
    # Tenant resolution precedes the precondition check so a foreign UUID is
    # indistinguishable from an absent resource.
    if project is None:
        db.rollback()
        raise not_found("Project")
    if if_match != _project_etag(project.id, project.version):
        db.rollback()
        raise IntegrationProblem(
            412,
            "precondition_failed",
            "Precondition failed",
            "The project changed; fetch it again before retrying.",
        )

    try:
        updated = db.execute(
            sa.update(Project)
            .where(
                Project.id == parsed,
                Project.organization_id == principal.organization_id,
                Project.version == project.version,
            )
            .values(
                **payload.column_values(),
                version=Project.version + 1,
                updated_at=sa.func.now(),
            )
            .returning(Project)
        ).scalar_one_or_none()
        if updated is None:
            db.rollback()
            raise IntegrationProblem(
                412,
                "precondition_failed",
                "Precondition failed",
                "The project changed; fetch it again before retrying.",
            )
        body = _project_body(updated)
        append_succeeded(
            db,
            principal,
            action="project.updated",
            resource_id=updated.id,
            request_id=getattr(request.state, "request_id", None),
        )
        idempotency.complete(db, claim, status=200, body=body)
    except IntegrationProblem:
        raise
    except IntegrityError as exc:
        db.rollback()
        if _constraint_name(exc) == "uq_projects_organization_id_external_id":
            raise IntegrationProblem(
                409,
                "external_id_conflict",
                "External ID conflict",
                "externalId is already in use in this organization.",
            ) from None
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "The project could not be updated.",
            retryable=True,
        ) from None
    except SQLAlchemyError:
        db.rollback()
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "The project could not be updated.",
            retryable=True,
        ) from None
    return _json_response(
        content=body,
        headers={"ETag": _project_etag(updated.id, updated.version)},
    )


def _constraint_name(exc: IntegrityError) -> str | None:
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None)


@router.post(
    "/jobs",
    status_code=201,
    response_model=IntegrationJobCreateResponse,
    operation_id="createJob",
    openapi_extra={"x-sdk-public": True},
)
def create_integration_job(
    payload: IntegrationJobCreate,
    request: Request,
    principal: IntegrationPrincipal,
    db: Database,
    idempotency_key: IdempotencyKey,
) -> JSONResponse:
    """Atomically reserve a tenant Project, Job and all declared uploads."""
    require_scope(principal, "jobs:write")
    return create_job_for_principal(payload, request, principal, db, idempotency_key)


def create_job_for_principal(
    payload: IntegrationJobCreate,
    request: Request,
    principal: PrincipalContext,
    db: Session,
    idempotency_key: str,
) -> JSONResponse:
    """Atomically reserve a tenant Project, Job and all declared uploads.

    Authentication and role/scope authorization stay at each wire boundary;
    this shared operation accepts only an already-resolved tenant principal.
    """
    claim = _claim(
        db,
        principal,
        request,
        idempotency_key,
        payload.model_dump(mode="json", by_alias=True),
    )
    if isinstance(claim, JSONResponse):
        return claim

    settings = get_settings()
    if settings.provider not in settings.provider_allowlist or not settings.pipeline_revision:
        db.rollback()
        raise IntegrationProblem(
            503,
            "service_unavailable",
            "Service unavailable",
            "Job creation is temporarily unavailable.",
            retryable=True,
        )

    try:
        if payload.project.id is not None:
            project = db.execute(
                sa.select(Project).where(
                    Project.id == payload.project.id,
                    Project.organization_id == principal.organization_id,
                    _project_is_on_audio_description_surface(),
                )
            ).scalar_one_or_none()
            if project is None:
                db.rollback()
                raise not_found("Project")
            if (
                payload.project.external_id is not None
                and project.external_id != payload.project.external_id
            ):
                db.rollback()
                raise IntegrationProblem(
                    409,
                    "project_external_id_mismatch",
                    "Project conflict",
                    "project.externalId does not match the selected project.",
                )
        else:
            project = Project(
                organization_id=principal.organization_id,
                name=payload.project.name,
                external_id=payload.project.external_id,
            )
            db.add(project)
            db.flush()

        job_id = uuid.uuid4()
        video_key = _tenant_upload_key(
            principal.organization_id,
            job_id,
            "source",
            payload.video.file_name,
        )
        stored_settings = payload.settings.to_worker_settings(
            project_name=project.name,
            duration_seconds=payload.video.duration_seconds,
            provided_transcript=payload.transcript is not None,
        )
        job = Job(
            id=job_id,
            organization_id=principal.organization_id,
            project_id=project.id,
            client_reference=payload.client_reference,
            pipeline_revision=settings.pipeline_revision,
            status=JobState.AWAITING_UPLOAD.value,
            provider=settings.provider,
            model=str(stored_settings["model"]),
            max_attempts=settings.max_attempts,
            settings=stored_settings,
            input_object_key=video_key,
            input_content_type=payload.video.content_type,
            input_size_bytes=payload.video.size_bytes,
            duration_secs=payload.video.duration_seconds,
        )
        db.add(job)
        db.flush()
        reserve_job_media(
            db,
            job,
            estimated_seconds=payload.video.duration_seconds,
        )
        db.add(
            Asset(
                organization_id=principal.organization_id,
                job_id=job.id,
                asset_type="source_video",
                object_key=video_key,
                content_type=payload.video.content_type,
                size_bytes=payload.video.size_bytes,
                duration_seconds=payload.video.duration_seconds,
            )
        )

        uploads: dict[str, Any] = {
            "video": _upload_body(
                generate_upload_post(
                    video_key,
                    payload.video.content_type,
                    max_bytes=payload.video.size_bytes,
                )
            )
        }
        if payload.transcript is not None:
            transcript_key = _tenant_upload_key(
                principal.organization_id,
                job_id,
                "transcript",
                payload.transcript.file_name,
            )
            db.add(
                Asset(
                    organization_id=principal.organization_id,
                    job_id=job.id,
                    asset_type="source_transcript",
                    object_key=transcript_key,
                    content_type=payload.transcript.content_type,
                    size_bytes=payload.transcript.size_bytes,
                    transcript_format=payload.transcript.format,
                )
            )
            uploads["transcript"] = _upload_body(
                generate_upload_post(
                    transcript_key,
                    payload.transcript.content_type,
                    max_bytes=min(payload.transcript.size_bytes, MAX_TRANSCRIPT_BYTES),
                )
            )
        db.flush()
        body = {
            "job": _job_body(job, _organization_slug(db, principal.organization_id)),
            "uploads": uploads,
        }
        if payload.project.id is None:
            append_succeeded(
                db,
                principal,
                action="project.created",
                resource_id=project.id,
                request_id=getattr(request.state, "request_id", None),
            )
        append_succeeded(
            db,
            principal,
            action="job.created",
            resource_id=job.id,
            request_id=getattr(request.state, "request_id", None),
        )
        idempotency.complete(db, claim, status=201, body=body)
    except IntegrationProblem:
        raise
    except QuotaExceededError:
        db.rollback()
        raise IntegrationProblem(
            429,
            "media_quota_exceeded",
            "Media quota exceeded",
            "The organization does not have enough monthly media quota for this job.",
            headers={"Retry-After": "3600"},
        ) from None
    except QuotaStateError:
        db.rollback()
        raise IntegrationProblem(
            503,
            "quota_unavailable",
            "Quota unavailable",
            "Media quota could not be reserved.",
            retryable=True,
        ) from None
    except IntegrityError as exc:
        db.rollback()
        constraint = _constraint_name(exc)
        if constraint == "organization_job_capacity_limit":
            raise IntegrationProblem(
                429,
                "job_capacity_exceeded",
                "Job capacity exceeded",
                "The organization has reached its active job limit.",
                retryable=True,
                headers={"Retry-After": "30"},
            ) from None
        if constraint == "uq_projects_organization_id_external_id":
            raise IntegrationProblem(
                409,
                "external_id_conflict",
                "External ID conflict",
                "project.externalId is already in use in this organization.",
            ) from None
        if constraint == "uq_jobs_organization_id_client_reference":
            raise IntegrationProblem(
                409,
                "client_reference_conflict",
                "Client reference conflict",
                "clientReference is already in use in this organization.",
            ) from None
        logger.warning("integration_job_create_failed category=database-constraint")
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "The job could not be created.",
            retryable=True,
        ) from None
    except SQLAlchemyError:
        db.rollback()
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "The job could not be created.",
            retryable=True,
        ) from None
    except Exception:
        db.rollback()
        logger.warning("integration_job_create_failed category=upload-service")
        raise IntegrationProblem(
            503,
            "upload_service_unavailable",
            "Upload service unavailable",
            "An upload reservation could not be created.",
            retryable=True,
        ) from None
    return _json_response(status_code=201, content=body)


def _list_jobs(
    principal: PrincipalContext,
    db: Session,
    *,
    limit: int,
    cursor: str | None,
    project_id: uuid.UUID | None,
) -> dict[str, Any]:
    decoded = decode_cursor(cursor)
    statement = (
        sa.select(Job)
        .join(Project, Job.project_id == Project.id)
        .where(
            Project.organization_id == principal.organization_id,
            Job.workflow_kind == "audio_description",
        )
    )
    if project_id is not None:
        statement = statement.where(Job.project_id == project_id)
    if decoded is not None:
        statement = statement.where(
            sa.tuple_(Job.created_at, Job.id) < sa.tuple_(decoded.created_at, decoded.resource_id)
        )
    statement = statement.order_by(Job.created_at.desc(), Job.id.desc()).limit(limit + 1)
    try:
        rows = list(db.execute(statement).scalars())
    except SQLAlchemyError:
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "Jobs could not be loaded.",
            retryable=True,
        ) from None
    organization_slug = _organization_slug(db, principal.organization_id)
    return _collection(rows, limit, lambda row: _job_body(row, organization_slug))


@router.get(
    "/jobs",
    response_model=IntegrationJobListResponse,
    operation_id="listJobs",
    openapi_extra={"x-sdk-public": True},
)
def list_jobs(
    principal: IntegrationPrincipal,
    db: Database,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    project_id: Annotated[str | None, Query(alias="projectId", max_length=36)] = None,
) -> dict[str, Any]:
    require_scope(principal, "jobs:read")
    parsed_project = _parse_id(project_id, "Project") if project_id is not None else None
    return _list_jobs(
        principal,
        db,
        limit=limit,
        cursor=cursor,
        project_id=parsed_project,
    )


@router.get("/projects/{project_id}/jobs", include_in_schema=False)
def list_project_jobs(
    project_id: str,
    principal: IntegrationPrincipal,
    db: Database,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> dict[str, Any]:
    require_scope(principal, "jobs:read")
    parsed = _parse_id(project_id, "Project")
    try:
        project = db.execute(
            sa.select(Project.id).where(
                Project.id == parsed,
                Project.organization_id == principal.organization_id,
                _project_is_on_audio_description_surface(),
            )
        ).scalar_one_or_none()
    except SQLAlchemyError:
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "The project could not be loaded.",
            retryable=True,
        ) from None
    if project is None:
        raise not_found("Project")
    return _list_jobs(principal, db, limit=limit, cursor=cursor, project_id=parsed)


@router.post(
    "/projects/{project_id}/jobs",
    status_code=201,
    include_in_schema=False,
)
def create_job(
    project_id: str,
    payload: IntegrationNestedJobCreate,
    request: Request,
    principal: IntegrationPrincipal,
    db: Database,
    idempotency_key: IdempotencyKey,
) -> JSONResponse:
    require_scope(principal, "jobs:write")
    parsed = _parse_id(project_id, "Project")
    try:
        project = db.execute(
            sa.select(Project).where(
                Project.id == parsed,
                Project.organization_id == principal.organization_id,
                _project_is_on_audio_description_surface(),
            )
        ).scalar_one_or_none()
    except SQLAlchemyError:
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "The project could not be loaded.",
            retryable=True,
        ) from None
    if project is None:
        raise not_found("Project")

    claim = _claim(
        db,
        principal,
        request,
        idempotency_key,
        payload.model_dump(mode="json", by_alias=True),
    )
    if isinstance(claim, JSONResponse):
        return claim

    settings = get_settings()
    if settings.provider not in settings.provider_allowlist or not settings.pipeline_revision:
        db.rollback()
        raise IntegrationProblem(
            503,
            "service_unavailable",
            "Service unavailable",
            "Job creation is temporarily unavailable.",
            retryable=True,
        )
    legacy = payload.to_legacy(project.name)
    job_id = uuid.uuid4()
    object_key = _tenant_upload_key(
        principal.organization_id,
        job_id,
        "source",
        legacy.file_name,
    )
    job = Job(
        id=job_id,
        organization_id=principal.organization_id,
        project_id=project.id,
        pipeline_revision=settings.pipeline_revision,
        status=JobState.AWAITING_UPLOAD.value,
        provider=settings.provider,
        model=legacy.settings.model,
        max_attempts=settings.max_attempts,
        settings=legacy.to_worker_settings(),
        input_object_key=object_key,
        input_content_type=legacy.content_type,
        input_size_bytes=legacy.file_size_bytes,
        duration_secs=legacy.duration_secs,
    )
    try:
        db.add(job)
        db.flush()
        reserve_job_media(db, job, estimated_seconds=legacy.duration_secs)
        db.add(
            Asset(
                organization_id=principal.organization_id,
                job_id=job.id,
                asset_type="source_video",
                object_key=object_key,
                content_type=legacy.content_type,
                size_bytes=legacy.file_size_bytes,
                duration_seconds=legacy.duration_secs,
            )
        )
        upload = generate_upload_post(object_key, legacy.content_type)
        body = {
            **_job_body(job, _organization_slug(db, principal.organization_id)),
            "upload": _upload_body(upload),
        }
        append_succeeded(
            db,
            principal,
            action="job.created",
            resource_id=job.id,
            request_id=getattr(request.state, "request_id", None),
        )
        idempotency.complete(db, claim, status=201, body=body)
    except QuotaExceededError:
        db.rollback()
        raise IntegrationProblem(
            429,
            "media_quota_exceeded",
            "Media quota exceeded",
            "The organization does not have enough monthly media quota for this job.",
            headers={"Retry-After": "3600"},
        ) from None
    except QuotaStateError:
        db.rollback()
        raise IntegrationProblem(
            503,
            "quota_unavailable",
            "Quota unavailable",
            "Media quota could not be reserved.",
            retryable=True,
        ) from None
    except IntegrityError as exc:
        db.rollback()
        if _constraint_name(exc) == "organization_job_capacity_limit":
            raise IntegrationProblem(
                429,
                "job_capacity_exceeded",
                "Job capacity exceeded",
                "The organization has reached its active job limit.",
                retryable=True,
                headers={"Retry-After": "30"},
            ) from None
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "The job could not be created.",
            retryable=True,
        ) from None
    except SQLAlchemyError:
        db.rollback()
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "The job could not be created.",
            retryable=True,
        ) from None
    except Exception:
        db.rollback()
        logger.warning("integration_job_create_failed category=upload-service")
        raise IntegrationProblem(
            503,
            "upload_service_unavailable",
            "Upload service unavailable",
            "An upload reservation could not be created.",
            retryable=True,
        ) from None
    return _json_response(status_code=201, content=body)


def _validate_write_key(key: str) -> None:
    if not 1 <= len(key) <= 255 or any(ord(char) < 0x21 or ord(char) > 0x7E for char in key):
        raise IntegrationProblem(
            400,
            "invalid_idempotency_key",
            "Invalid idempotency key",
            "The idempotency key must contain 1-255 visible ASCII characters.",
        )


def _existing_replay(
    db: Session,
    principal: PrincipalContext,
    request: Request,
    key: str,
    body: dict[str, Any],
) -> JSONResponse | None:
    """Return an already-completed response without claiming a new record.

    Upload completion performs several durability commits around storage and
    queue side effects, so a new idempotency claim is stored only afterward.
    This read-first seam still guarantees that an established key always
    replays before inspecting the job's later lifecycle state.
    """
    _validate_write_key(key)
    try:
        exists = db.execute(
            sa.select(IdempotencyRecord.id).where(
                IdempotencyRecord.organization_id == principal.organization_id,
                IdempotencyRecord.method == request.method.upper(),
                IdempotencyRecord.path == request.url.path,
                IdempotencyRecord.key == key,
            )
        ).scalar_one_or_none()
    except SQLAlchemyError:
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "The request could not be reconciled.",
            retryable=True,
        ) from None
    if exists is None:
        db.rollback()
        return None
    replay = _claim(db, principal, request, key, body)
    if not isinstance(replay, JSONResponse):
        db.rollback()
        raise IntegrationProblem(
            503,
            "idempotency_unavailable",
            "Idempotency unavailable",
            "The prior request could not be reconciled.",
            retryable=True,
        )
    return replay


def _norm_content_type(value: str | None) -> str:
    return (value or "").split(";", 1)[0].strip().lower()


def _norm_etag(value: str | None) -> str:
    return (value or "").strip().strip('"')


def _storage_problem(exc: Exception) -> IntegrationProblem:
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        if code in {"404", "NoSuchKey", "NotFound"}:
            return IntegrationProblem(
                409,
                "source_not_visible",
                "Upload incomplete",
                "A declared upload is missing or not yet visible.",
                retryable=True,
            )
    return IntegrationProblem(
        503,
        "storage_unavailable",
        "Storage unavailable",
        "Upload verification is temporarily unavailable.",
        retryable=True,
    )


def _verify_asset(db: Session, asset: Asset) -> tuple[Asset, str | None]:
    """Head and version-pin one declared upload without trusting client data."""
    try:
        head = head_source(asset.object_key)
    except (ClientError, BotoCoreError) as exc:
        raise _storage_problem(exc) from None

    etag = _norm_etag(head.get("ETag"))
    version_id = head.get("VersionId")
    checksum = head.get("ChecksumSHA256")
    mismatches: list[str] = []
    if head.get("ContentLength") != asset.size_bytes:
        mismatches.append("size")
    if _norm_content_type(head.get("ContentType")) != _norm_content_type(asset.content_type):
        mismatches.append("content_type")
    if head.get("ServerSideEncryption") != "AES256":
        mismatches.append("encryption")
    if not etag:
        mismatches.append("etag")
    if mismatches:
        raise IntegrationProblem(
            422,
            "source_mismatch",
            "Upload mismatch",
            "An uploaded object does not match its declaration.",
            extensions={"asset": asset.asset_type, "checks": mismatches},
        )
    if not version_id:
        raise IntegrationProblem(
            503,
            "storage_unavailable",
            "Storage unavailable",
            "Upload verification requires versioned object storage.",
            retryable=True,
        )
    if asset.status in {"rejected", "deleted"}:
        raise IntegrationProblem(
            409,
            "upload_state_conflict",
            "Upload conflict",
            "The declared upload can no longer be completed.",
        )
    if asset.status == "validated":
        if asset.etag != etag or asset.version_id != version_id:
            raise IntegrationProblem(
                409,
                "source_identity_changed",
                "Upload changed",
                "A source object changed after verification; create a new job.",
            )
        return asset, checksum

    now = datetime.now(UTC)
    try:
        persisted = db.execute(
            sa.update(Asset)
            .where(
                Asset.id == asset.id,
                Asset.organization_id == asset.organization_id,
                Asset.status.in_(["awaiting_upload", "uploaded"]),
            )
            .values(
                status="validated",
                version_id=version_id,
                etag=etag,
                # S3's ChecksumSHA256 wire value is base64 while this column's
                # contract is lowercase hex. VersionId + ETag remain the
                # authoritative identity until checksum conversion is shared.
                checksum_sha256=None,
                validated_at=now,
                updated_at=sa.func.now(),
            )
            .returning(Asset)
        ).scalar_one_or_none()
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "Upload verification could not be persisted.",
            retryable=True,
        ) from None
    if persisted is None:
        persisted = db.execute(
            sa.select(Asset).where(
                Asset.id == asset.id,
                Asset.organization_id == asset.organization_id,
            )
        ).scalar_one_or_none()
        if (
            persisted is None
            or persisted.status != "validated"
            or persisted.etag != etag
            or persisted.version_id != version_id
        ):
            raise IntegrationProblem(
                409,
                "source_identity_changed",
                "Upload changed",
                "A source object changed during verification; create a new job.",
            )
    return persisted, checksum


def _integration_problem_from_legacy(exc: HTTPException) -> IntegrationProblem:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    code = detail.get("code", "upload_completion_failed")
    message = detail.get("message", "The upload could not be completed.")
    extensions = {"checks": detail["checks"]} if "checks" in detail else None
    return IntegrationProblem(
        exc.status_code,
        code,
        "Upload completion failed",
        message,
        retryable=exc.status_code >= 500 or code in {"source_not_visible", "capacity_conflict"},
        extensions=extensions,
    )


@router.post(
    "/jobs/{jobId}/uploads/complete",
    status_code=202,
    response_model=IntegrationJobResponse,
    responses={
        200: {
            "model": IntegrationJobResponse,
            "description": "The upload had already been accepted.",
        }
    },
    operation_id="completeUpload",
    openapi_extra={"x-sdk-public": True},
)
def complete_upload(
    jobId: str,
    request: Request,
    principal: IntegrationPrincipal,
    db: Database,
    idempotency_key: IdempotencyKey,
) -> JSONResponse:
    require_scope(principal, "jobs:write")
    return complete_upload_for_principal(jobId, request, principal, db, idempotency_key)


def complete_upload_for_principal(
    jobId: str,
    request: Request,
    principal: PrincipalContext,
    db: Session,
    idempotency_key: str,
    *,
    allow_video_investigation: bool = False,
) -> JSONResponse:
    parsed = _parse_id(jobId, "Job")
    replay = _existing_replay(db, principal, request, idempotency_key, {})
    if replay is not None:
        return replay
    try:
        job = db.execute(
            sa.select(Job)
            .join(Project, Job.project_id == Project.id)
            .where(
                *_job_lookup_conditions(
                    parsed,
                    principal.organization_id,
                    allow_video_investigation=allow_video_investigation,
                )
            )
        ).scalar_one_or_none()
    except SQLAlchemyError:
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "The job could not be loaded.",
            retryable=True,
        ) from None
    if job is None:
        raise not_found("Job")

    state = JobState(job.status)
    upload_was_already_accepted = state in {
        JobState.QUEUED,
        JobState.PROCESSING,
        JobState.READY_FOR_REVIEW,
        JobState.EXPORT_QUEUED,
        JobState.EXPORTING,
        JobState.COMPLETED,
    }
    if state not in {
        JobState.QUEUED,
        JobState.PROCESSING,
        JobState.READY_FOR_REVIEW,
        JobState.EXPORT_QUEUED,
        JobState.EXPORTING,
        JobState.COMPLETED,
    }:
        if state in {JobState.FAILED, JobState.CANCELLED}:
            raise IntegrationProblem(
                409,
                "terminal_conflict",
                "Job conflict",
                "The job is already in a terminal state.",
            )
        try:
            assets = {
                asset.asset_type: asset
                for asset in db.execute(
                    sa.select(Asset).where(
                        Asset.organization_id == principal.organization_id,
                        Asset.job_id == parsed,
                    )
                ).scalars()
            }
            video_asset = assets.get("source_video")
            transcript_asset = assets.get("source_transcript")
            legacy_reservation = video_asset is None and all(
                (job.input_object_key, job.input_content_type, job.input_size_bytes)
            )
            if video_asset is None and (not legacy_reservation or transcript_asset is not None):
                raise IntegrationProblem(
                    409,
                    "upload_not_reserved",
                    "Upload not reserved",
                    "The job has no video upload reservation.",
                )

            # A declared transcript is verified first. Any missing/mismatched
            # transcript returns before the video can acquire a compute slot.
            if transcript_asset is not None:
                _verify_asset(db, transcript_asset)
            if video_asset is not None:
                verified_video, video_checksum = _verify_asset(db, video_asset)
                job = db.execute(
                    sa.select(Job)
                    .join(Project, Job.project_id == Project.id)
                    .where(
                        *_job_lookup_conditions(
                            parsed,
                            principal.organization_id,
                            allow_video_investigation=allow_video_investigation,
                        )
                    )
                ).scalar_one()
                persist_verified_source(
                    db,
                    parsed,
                    job,
                    verified_video.etag,
                    verified_video.version_id,
                    video_checksum,
                    verified_video.validated_at,
                )
        except IntegrationProblem:
            raise
        except HTTPException as exc:
            raise _integration_problem_from_legacy(exc) from None
        except SQLAlchemyError:
            db.rollback()
            raise IntegrationProblem(
                503,
                "persistence_unavailable",
                "Persistence unavailable",
                "Upload verification could not be completed.",
                retryable=True,
            ) from None

    try:
        legacy_response = complete_upload_for_organization(
            parsed,
            db,
            principal.organization_id,
            allow_video_investigation=allow_video_investigation,
        )
        status = legacy_response.status_code if isinstance(legacy_response, JSONResponse) else 202
        db.expire_all()
        job = db.execute(
            sa.select(Job)
            .join(Project, Job.project_id == Project.id)
            .where(
                *_job_lookup_conditions(
                    parsed,
                    principal.organization_id,
                    allow_video_investigation=allow_video_investigation,
                )
            )
        ).scalar_one()
    except HTTPException as exc:
        raise _integration_problem_from_legacy(exc) from None
    except SQLAlchemyError:
        db.rollback()
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "The upload completion result could not be loaded.",
            retryable=True,
        ) from None
    body = _job_body(job, _organization_slug(db, principal.organization_id))

    # The proven completion flow intentionally uses several durability
    # commits around S3/SQS. Claim after that naturally-idempotent operation,
    # then store the exact public response for byte-identical future replay.
    claim = _claim(db, principal, request, idempotency_key, {})
    if isinstance(claim, JSONResponse):
        return claim
    try:
        if not upload_was_already_accepted:
            append_succeeded(
                db,
                principal,
                action="job.upload_completed",
                resource_id=job.id,
                request_id=getattr(request.state, "request_id", None),
            )
        idempotency.complete(db, claim, status=status, body=body)
    except SQLAlchemyError:
        db.rollback()
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "The completion result could not be persisted; reconcile the job before retrying.",
            retryable=True,
        ) from None
    return _json_response(status_code=status, content=body)


@router.post(
    "/jobs/{jobId}/cancel",
    response_model=IntegrationJobResponse,
    operation_id="cancelJob",
    openapi_extra={"x-sdk-public": True},
)
def cancel_job(
    jobId: str,
    request: Request,
    principal: IntegrationPrincipal,
    db: Database,
    idempotency_key: IdempotencyKey,
) -> JSONResponse:
    require_scope(principal, "jobs:write")
    return cancel_job_for_principal(jobId, request, principal, db, idempotency_key)


def cancel_job_for_principal(
    jobId: str,
    request: Request,
    principal: PrincipalContext,
    db: Session,
    idempotency_key: str,
) -> JSONResponse:
    parsed = _parse_id(jobId, "Job")
    try:
        job = db.execute(
            sa.select(Job)
            .join(Project, Job.project_id == Project.id)
            .where(
                *_job_lookup_conditions(
                    parsed,
                    principal.organization_id,
                    allow_video_investigation=False,
                )
            )
        ).scalar_one_or_none()
    except SQLAlchemyError:
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "The job could not be loaded.",
            retryable=True,
        ) from None
    if job is None:
        raise not_found("Job")

    claim = _claim(db, principal, request, idempotency_key, {})
    if isinstance(claim, JSONResponse):
        return claim
    try:
        cancelled_now = False
        for _attempt in range(3):
            state = JobState(job.status)
            if state == JobState.CANCELLED:
                break
            if state in {JobState.COMPLETED, JobState.FAILED}:
                db.rollback()
                raise IntegrationProblem(
                    409,
                    "terminal_conflict",
                    "Job conflict",
                    "A completed or failed job cannot be cancelled.",
                )
            moved = transition_job(
                db,
                parsed,
                state,
                JobState.CANCELLED,
                values={
                    "completed_at": datetime.now(UTC),
                    "lease_expires_at": None,
                    "worker_id": None,
                    "error_code": None,
                    "error_message": None,
                },
            )
            if moved is not None:
                job = moved
                cancelled_now = True
                event_id = uuid.uuid4()
                occurred_at = datetime.now(UTC)
                db.add(
                    JobEvent(
                        id=event_id,
                        organization_id=principal.organization_id,
                        job_id=job.id,
                        event_type="job.cancelled",
                        job_version=job.version,
                        payload={
                            "id": str(event_id),
                            "type": "job.cancelled",
                            "jobId": str(job.id),
                            "state": "cancelled",
                            "occurredAt": _iso(occurred_at),
                        },
                        occurred_at=occurred_at,
                        available_at=occurred_at,
                    )
                )
                break
            db.expire_all()
            job = db.execute(
                sa.select(Job)
                .join(Project, Job.project_id == Project.id)
                .where(
                    *_job_lookup_conditions(
                        parsed,
                        principal.organization_id,
                        allow_video_investigation=False,
                    )
                )
            ).scalar_one_or_none()
            if job is None:
                db.rollback()
                raise not_found("Job")
        else:
            db.rollback()
            raise IntegrationProblem(
                409,
                "state_conflict",
                "Job conflict",
                "The job changed concurrently; fetch it before retrying.",
                retryable=True,
            )
        # A cancelled, never-completed reservation has no authoritative S3
        # VersionId. Remove only that metadata row so it cannot block the
        # terminal retention reaper; unconfirmed bytes remain governed by the
        # bucket lifecycle and are never deleted by key alone.
        db.execute(
            sa.delete(Asset).where(
                Asset.organization_id == principal.organization_id,
                Asset.job_id == parsed,
                Asset.status == "awaiting_upload",
                Asset.version_id.is_(None),
            )
        )
        body = _job_body(job, _organization_slug(db, principal.organization_id))
        if cancelled_now:
            append_succeeded(
                db,
                principal,
                action="job.cancelled",
                resource_id=job.id,
                request_id=getattr(request.state, "request_id", None),
            )
        idempotency.complete(db, claim, status=200, body=body)
    except IntegrationProblem:
        raise
    except SQLAlchemyError:
        db.rollback()
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "The job could not be cancelled.",
            retryable=True,
        ) from None
    return _json_response(content=body)


@router.get(
    "/jobs/{jobId}",
    response_model=IntegrationJobResponse,
    operation_id="getJob",
    openapi_extra={"x-sdk-public": True},
)
def get_job(
    jobId: str,
    principal: IntegrationPrincipal,
    db: Database,
) -> dict[str, Any]:
    require_scope(principal, "jobs:read")
    return get_job_for_principal(jobId, principal, db)


def get_job_for_principal(
    jobId: str,
    principal: PrincipalContext,
    db: Session,
) -> dict[str, Any]:
    parsed = _parse_id(jobId, "Job")
    try:
        job = db.execute(
            sa.select(Job)
            .join(Project, Job.project_id == Project.id)
            .where(
                *_job_lookup_conditions(
                    parsed,
                    principal.organization_id,
                    allow_video_investigation=False,
                )
            )
        ).scalar_one_or_none()
    except SQLAlchemyError:
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "The job could not be loaded.",
            retryable=True,
        ) from None
    if job is None:
        raise not_found("Job")
    return _job_body(job, _organization_slug(db, principal.organization_id))
