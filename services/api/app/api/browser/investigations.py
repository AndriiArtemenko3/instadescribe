"""Human Browser API for tenant-scoped observable video investigations."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import sqlalchemy as sa
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from instadescribe_contracts.provider import PROVIDER_MAX_ATTEMPTS
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.browser.auth import (
    BrowserPrincipal,
    require_browser_access_principal,
    require_browser_review_principal,
    require_browser_upload_principal,
)
from app.api.integrations.problems import INTEGRATION_PROBLEM_SCHEMA, IntegrationProblem
from app.core.config import get_settings
from app.core.rfc3339 import utc_timestamp
from app.core.tenancy import PrincipalContext
from app.db.session import get_db
from app.domain.states import JobState
from app.models import (
    AnalystDecision,
    Asset,
    Investigation,
    Job,
    JobEvent,
    Project,
    SourceRecord,
)
from app.repositories.investigations import (
    get_decision,
    get_investigation,
    get_source_record,
    list_beliefs,
    list_evidence,
    list_investigations,
    list_steps,
)
from app.repositories.jobs import transition_job
from app.schemas.investigations import (
    AnalystDecisionRequest,
    AnalystDecisionResult,
    BeliefCandidate,
    BeliefSnapshotListResponse,
    EvidenceListResponse,
    InvestigationCreateRequest,
    InvestigationCreateResponse,
    InvestigationDetail,
    InvestigationListResponse,
    InvestigationReportResponse,
    InvestigationStepListResponse,
    KeyframeListResponse,
)
from app.services import idempotency
from app.services.audit import append_succeeded
from app.services.idempotency import IdempotencyClaim, IdempotencyError
from app.services.investigations import (
    belief_body,
    decision_body,
    detail_body,
    evidence_body,
    keyframe_body,
    source_body,
    step_body,
    summary_body,
)
from app.services.quota import QuotaExceededError, QuotaStateError, reserve_job_media
from app.services.s3 import generate_upload_post, investigation_retention_tag

logger = logging.getLogger("app.investigations")
router = APIRouter(prefix="/api/app/v1", tags=["browser-investigations"])

BrowserHumanPrincipal = Annotated[BrowserPrincipal, Depends(require_browser_access_principal)]
BrowserUploadPrincipal = Annotated[BrowserPrincipal, Depends(require_browser_upload_principal)]
BrowserReviewPrincipal = Annotated[BrowserPrincipal, Depends(require_browser_review_principal)]
BrowserIdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=255,
        description="Organization-scoped safe-retry key for this browser write",
    ),
]

_KIND_TO_DB = {
    "geolocateProvenance": "geolocate_provenance",
    "damageChange": "damage_change",
}
_POLICY_TO_DB = {
    "local": "local",
    "textOnly": "text_only",
    "approvedCrops": "approved_crops",
    "connected": "connected",
}
_LEGAL_BASIS_TO_DB = {
    "publicDomain": "public_domain",
    "licensed": "licensed",
    "consent": "consent",
    "analystAuthorized": "analyst_authorized",
}

_CREATE_PROBLEM_RESPONSES = {
    422: {
        "description": (
            "Request validation failed or the requested investigation kind and "
            "connectivity policy are unavailable in this build"
        ),
        "content": {
            "application/problem+json": {
                "schema": INTEGRATION_PROBLEM_SCHEMA,
                "examples": {
                    "modeUnavailable": {
                        "summary": "The requested investigation mode is not implemented",
                        "value": {
                            "type": (
                                "https://api.instadescribe.com/problems/"
                                "investigation_mode_unavailable"
                            ),
                            "title": "Investigation mode unavailable",
                            "status": 422,
                            "detail": (
                                "This beta build supports only local geolocation and "
                                "provenance investigations."
                            ),
                            "instance": "/api/app/v1/investigations",
                            "code": "investigation_mode_unavailable",
                            "requestId": "00000000-0000-0000-0000-000000000000",
                            "retryable": False,
                        },
                    }
                },
            }
        },
    }
}
_VALIDATION_PROBLEM_RESPONSES = {
    422: {
        "description": "Request validation failed",
        "content": {
            "application/problem+json": {
                "schema": INTEGRATION_PROBLEM_SCHEMA,
            }
        },
    }
}
_REDISTRIBUTION_TO_DB = {
    "prohibited": "prohibited",
    "metadataOnly": "metadata_only",
    "permitted": "permitted",
}


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
) -> JSONResponse:
    headers = {"Cache-Control": "private, no-store"}
    if replayed:
        headers["Idempotent-Replayed"] = "true"
    return JSONResponse(
        status_code=status_code,
        content=_canonical_json(body),
        headers=headers,
    )


def _parse_id(value: str, resource: str = "Investigation") -> uuid.UUID:
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise _not_found(resource) from None
    if parsed.int == 0:
        raise _not_found(resource)
    return parsed


def _not_found(resource: str = "Investigation") -> IntegrationProblem:
    return IntegrationProblem(
        404,
        "not_found",
        "Not found",
        f"{resource} was not found.",
    )


def _conflict(code: str, detail: str) -> IntegrationProblem:
    return IntegrationProblem(409, code, "Investigation conflict", detail)


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


def _claim(
    db: Session,
    principal: BrowserPrincipal,
    request: Request,
    key: str,
    body: dict[str, Any],
) -> IdempotencyClaim:
    try:
        return idempotency.claim(
            db,
            principal.organization_id,
            key=key,
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


def _tenant_upload_key(
    organization_id: uuid.UUID,
    job_id: uuid.UUID,
    file_name: str,
) -> str:
    return f"uploads/orgs/{organization_id}/jobs/{job_id}/source/{file_name}"


def _upload_body(upload: dict[str, Any]) -> dict[str, Any]:
    return {
        "method": "POST",
        "url": upload["url"],
        "fields": upload["fields"],
        "expiresAt": utc_timestamp(upload["expires_at"]),
    }


def _get_row(db: Session, principal: BrowserPrincipal, investigation_id: str, *, lock=False):
    parsed = _parse_id(investigation_id)
    try:
        row = get_investigation(
            db,
            _service_principal(principal),
            parsed,
            for_update=lock,
        )
    except SQLAlchemyError:
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "The investigation could not be loaded.",
            retryable=True,
        ) from None
    if row is None:
        raise _not_found()
    return row


@router.get(
    "/investigations",
    response_model=InvestigationListResponse,
    operation_id="listBrowserInvestigations",
)
def list_browser_investigations(
    principal: BrowserHumanPrincipal,
    db: Session = Depends(get_db),
) -> JSONResponse:
    try:
        rows = list_investigations(db, _service_principal(principal), limit=100)
        body = {"data": [summary_body(row) for row in rows]}
    except SQLAlchemyError:
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "Investigations could not be loaded.",
            retryable=True,
        ) from None
    return _private_json(body)


@router.post(
    "/investigations",
    status_code=201,
    response_model=InvestigationCreateResponse,
    operation_id="createBrowserInvestigation",
    responses=_CREATE_PROBLEM_RESPONSES,
)
def create_browser_investigation(
    payload: InvestigationCreateRequest,
    request: Request,
    principal: BrowserUploadPrincipal,
    idempotency_key: BrowserIdempotencyKey,
    db: Session = Depends(get_db),
) -> JSONResponse:
    if payload.kind != "geolocateProvenance" or payload.connectivity_policy != "local":
        raise IntegrationProblem(
            422,
            "investigation_mode_unavailable",
            "Investigation mode unavailable",
            "This beta build supports only local geolocation and provenance investigations.",
        )
    request_body = payload.model_dump(mode="json", by_alias=True)
    claim = _claim(db, principal, request, idempotency_key, request_body)
    if claim.is_replay:
        body = claim.replay_body or {}
        InvestigationCreateResponse.model_validate(body)
        return _private_json(
            body,
            status_code=claim.replay_status or 201,
            replayed=True,
        )

    settings = get_settings()
    if not settings.pipeline_revision:
        db.rollback()
        raise IntegrationProblem(
            503,
            "service_unavailable",
            "Service unavailable",
            "Investigation creation is temporarily unavailable.",
            retryable=True,
        )

    investigation_id = uuid.uuid4()
    job_id = uuid.uuid4()
    kind = _KIND_TO_DB[payload.kind]
    connectivity_policy = _POLICY_TO_DB[payload.connectivity_policy]
    object_key = _tenant_upload_key(
        principal.organization_id,
        job_id,
        payload.video.file_name,
    )
    collected_at = datetime.now(UTC)
    service_principal = _service_principal(principal)
    try:
        project = Project(
            organization_id=principal.organization_id,
            name=payload.name,
        )
        db.add(project)
        db.flush()
        job = Job(
            id=job_id,
            organization_id=principal.organization_id,
            workflow_kind="video_investigation",
            project_id=project.id,
            pipeline_revision=settings.pipeline_revision,
            status=JobState.AWAITING_UPLOAD.value,
            provider="local",
            model=None,
            # Investigation compute is always the bounded local provider,
            # independent of the API deployment's AD provider policy.
            max_attempts=PROVIDER_MAX_ATTEMPTS["local"],
            settings={
                "workflow_kind": "video_investigation",
                "investigation_id": str(investigation_id),
                "investigation_kind": kind,
                "connectivity_policy": connectivity_policy,
            },
            input_object_key=object_key,
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
                object_key=object_key,
                content_type=payload.video.content_type,
                size_bytes=payload.video.size_bytes,
                duration_seconds=payload.video.duration_seconds,
                # The analyst-declared investigation retention is also the
                # authoritative deadline for the version-pinned media object;
                # SourceRecord metadata alone must not promise a shorter
                # retention window than the actual Asset janitor enforces.
                purge_after=collected_at + timedelta(days=payload.source.retention_days),
            )
        )
        investigation = Investigation(
            id=investigation_id,
            organization_id=principal.organization_id,
            job_id=job.id,
            kind=kind,
            connectivity_policy=connectivity_policy,
            status="awaiting_upload",
            model_provenance={"executedLocally": False},
            runtime_provenance={},
        )
        db.add(investigation)
        db.flush()
        db.add(
            SourceRecord(
                organization_id=principal.organization_id,
                job_id=job.id,
                investigation_id=investigation.id,
                publisher_url=payload.source.publisher_url,
                published_at=payload.source.published_at,
                collected_at=collected_at,
                legal_basis=_LEGAL_BASIS_TO_DB[payload.source.legal_basis],
                license_name=payload.source.license_name,
                redistribution_policy=_REDISTRIBUTION_TO_DB[payload.source.redistribution_policy],
                retention_days=payload.source.retention_days,
                purge_after=collected_at + timedelta(days=payload.source.retention_days),
            )
        )
        upload = generate_upload_post(
            object_key,
            payload.video.content_type,
            max_bytes=payload.video.size_bytes,
            retention_tag=investigation_retention_tag(payload.source.retention_days),
        )
        db.flush()
        row = get_investigation(db, service_principal, investigation.id)
        if row is None:
            raise RuntimeError("new investigation was not visible in its transaction")
        body = {
            "investigation": detail_body(row),
            "upload": _upload_body(upload),
        }
        append_succeeded(
            db,
            service_principal,
            action="project.created",
            resource_id=project.id,
            request_id=getattr(request.state, "request_id", None),
        )
        append_succeeded(
            db,
            service_principal,
            action="job.created",
            resource_id=job.id,
            request_id=getattr(request.state, "request_id", None),
        )
        append_succeeded(
            db,
            service_principal,
            action="investigation.created",
            resource_id=investigation.id,
            request_id=getattr(request.state, "request_id", None),
        )
        idempotency.complete(db, claim, status=201, body=body)
    except QuotaExceededError:
        db.rollback()
        raise IntegrationProblem(
            429,
            "media_quota_exceeded",
            "Media quota exceeded",
            "The organization does not have enough monthly media quota for this investigation.",
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
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint == "organization_job_capacity_limit":
            raise IntegrationProblem(
                429,
                "job_capacity_exceeded",
                "Job capacity exceeded",
                "The organization has reached its active job limit.",
                retryable=True,
                headers={"Retry-After": "30"},
            ) from None
        logger.warning("investigation_create_failed category=database-constraint")
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "The investigation could not be created.",
            retryable=True,
        ) from None
    except SQLAlchemyError:
        db.rollback()
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "The investigation could not be created.",
            retryable=True,
        ) from None
    except (BotoCoreError, ClientError):
        db.rollback()
        logger.warning("investigation_create_failed category=upload-service")
        raise IntegrationProblem(
            503,
            "upload_service_unavailable",
            "Upload service unavailable",
            "An upload reservation could not be created.",
            retryable=True,
        ) from None
    except Exception:
        db.rollback()
        logger.warning("investigation_create_failed category=internal")
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "The investigation could not be created.",
            retryable=True,
        ) from None
    return _private_json(body, status_code=201)


@router.get(
    "/investigations/{investigation_id}",
    response_model=InvestigationDetail,
    operation_id="getBrowserInvestigation",
)
def get_browser_investigation(
    investigation_id: str,
    principal: BrowserHumanPrincipal,
    db: Session = Depends(get_db),
) -> JSONResponse:
    return _private_json(detail_body(_get_row(db, principal, investigation_id)))


@router.post(
    "/investigations/{investigation_id}/cancel",
    response_model=InvestigationDetail,
    operation_id="cancelBrowserInvestigation",
    responses=_VALIDATION_PROBLEM_RESPONSES,
)
def cancel_browser_investigation(
    investigation_id: str,
    request: Request,
    principal: BrowserUploadPrincipal,
    idempotency_key: BrowserIdempotencyKey,
    db: Session = Depends(get_db),
) -> JSONResponse:
    claim = _claim(db, principal, request, idempotency_key, {})
    if claim.is_replay:
        body = claim.replay_body or {}
        InvestigationDetail.model_validate(body)
        return _private_json(body, replayed=True)
    service_principal = _service_principal(principal)
    try:
        row = get_investigation(
            db,
            service_principal,
            _parse_id(investigation_id),
            for_update=True,
        )
        if row is None:
            db.rollback()
            raise _not_found()
        if row.investigation.status in {"completed", "failed"}:
            db.rollback()
            raise _conflict(
                "terminal_conflict",
                "A completed or failed investigation cannot be cancelled.",
            )
        cancelled_now = row.investigation.status != "cancelled"
        job_state = JobState(row.job.status)
        if job_state not in {JobState.CANCELLED, JobState.COMPLETED, JobState.FAILED}:
            moved = transition_job(
                db,
                row.job.id,
                job_state,
                JobState.CANCELLED,
                values={
                    "completed_at": datetime.now(UTC),
                    "lease_expires_at": None,
                    "worker_id": None,
                    "error_code": None,
                    "error_message": None,
                },
            )
            if moved is None:
                db.rollback()
                raise _conflict(
                    "state_conflict",
                    "The investigation changed concurrently; fetch it before retrying.",
                )
            event_id = uuid.uuid4()
            occurred_at = datetime.now(UTC)
            db.add(
                JobEvent(
                    id=event_id,
                    organization_id=principal.organization_id,
                    job_id=row.job.id,
                    event_type="job.cancelled",
                    job_version=moved.version,
                    payload={
                        "id": str(event_id),
                        "type": "job.cancelled",
                        "jobId": str(row.job.id),
                        "state": "cancelled",
                        "occurredAt": utc_timestamp(occurred_at),
                    },
                    occurred_at=occurred_at,
                    available_at=occurred_at,
                )
            )
        elif job_state in {JobState.COMPLETED, JobState.FAILED}:
            db.rollback()
            raise _conflict(
                "terminal_conflict",
                "A completed or failed investigation cannot be cancelled.",
            )
        row.investigation.status = "cancelled"
        row.investigation.updated_at = datetime.now(UTC)
        db.execute(
            sa.delete(Asset).where(
                Asset.organization_id == principal.organization_id,
                Asset.job_id == row.job.id,
                Asset.status == "awaiting_upload",
                Asset.version_id.is_(None),
            )
        )
        if cancelled_now:
            append_succeeded(
                db,
                service_principal,
                action="investigation.cancelled",
                resource_id=row.investigation.id,
                request_id=getattr(request.state, "request_id", None),
            )
        db.flush()
        body = detail_body(row)
        idempotency.complete(db, claim, status=200, body=body)
    except IntegrationProblem:
        raise
    except SQLAlchemyError:
        db.rollback()
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "The investigation could not be cancelled.",
            retryable=True,
        ) from None
    return _private_json(body)


def _load_collection(
    investigation_id: str,
    principal: BrowserPrincipal,
    db: Session,
    loader,
) -> list:
    row = _get_row(db, principal, investigation_id)
    try:
        return loader(db, _service_principal(principal), row.investigation.id)
    except SQLAlchemyError:
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "Investigation evidence could not be loaded.",
            retryable=True,
        ) from None


@router.get(
    "/investigations/{investigation_id}/steps",
    response_model=InvestigationStepListResponse,
    operation_id="listBrowserInvestigationSteps",
)
def list_browser_investigation_steps(
    investigation_id: str,
    principal: BrowserHumanPrincipal,
    db: Session = Depends(get_db),
) -> JSONResponse:
    rows = _load_collection(investigation_id, principal, db, list_steps)
    return _private_json({"data": [step_body(row) for row in rows]})


@router.get(
    "/investigations/{investigation_id}/evidence",
    response_model=EvidenceListResponse,
    operation_id="listBrowserInvestigationEvidence",
)
def list_browser_investigation_evidence(
    investigation_id: str,
    principal: BrowserHumanPrincipal,
    db: Session = Depends(get_db),
) -> JSONResponse:
    rows = _load_collection(investigation_id, principal, db, list_evidence)
    return _private_json({"data": [evidence_body(row) for row in rows]})


@router.get(
    "/investigations/{investigation_id}/keyframes",
    response_model=KeyframeListResponse,
    operation_id="listBrowserInvestigationKeyframes",
)
def list_browser_investigation_keyframes(
    investigation_id: str,
    principal: BrowserHumanPrincipal,
    db: Session = Depends(get_db),
) -> JSONResponse:
    rows = _load_collection(investigation_id, principal, db, list_evidence)
    keyframes = sorted(
        (row for row in rows if row.kind == "keyframe" and row.frame_time_ms is not None),
        key=lambda row: (row.frame_time_ms, str(row.id)),
    )
    return _private_json(
        {
            # Timeline order is part of the Browser contract. Evidence UUIDs
            # are investigation-derived and therefore cannot provide a stable
            # chronological order across otherwise identical runs.
            "data": [keyframe_body(row) for row in keyframes]
        }
    )


@router.get(
    "/investigations/{investigation_id}/beliefs",
    response_model=BeliefSnapshotListResponse,
    operation_id="listBrowserInvestigationBeliefs",
)
def list_browser_investigation_beliefs(
    investigation_id: str,
    principal: BrowserHumanPrincipal,
    db: Session = Depends(get_db),
) -> JSONResponse:
    rows = _load_collection(investigation_id, principal, db, list_beliefs)
    return _private_json({"data": [belief_body(row) for row in rows]})


@router.post(
    "/investigations/{investigation_id}/decision",
    response_model=AnalystDecisionResult,
    operation_id="finalizeBrowserInvestigation",
    responses=_VALIDATION_PROBLEM_RESPONSES,
)
def finalize_browser_investigation(
    investigation_id: str,
    payload: AnalystDecisionRequest,
    request: Request,
    principal: BrowserReviewPrincipal,
    idempotency_key: BrowserIdempotencyKey,
    db: Session = Depends(get_db),
) -> JSONResponse:
    request_body = payload.model_dump(mode="json", by_alias=True)
    claim = _claim(db, principal, request, idempotency_key, request_body)
    if claim.is_replay:
        body = claim.replay_body or {}
        AnalystDecisionResult.model_validate(body)
        return _private_json(body, replayed=True)
    service_principal = _service_principal(principal)
    try:
        row = get_investigation(
            db,
            service_principal,
            _parse_id(investigation_id),
            for_update=True,
        )
        if row is None:
            db.rollback()
            raise _not_found()
        if row.investigation.status != "needs_review":
            db.rollback()
            raise _conflict(
                "review_state_conflict",
                "The investigation is not ready for an analyst decision.",
            )
        if get_decision(db, service_principal, row.investigation.id) is not None:
            db.rollback()
            raise _conflict(
                "decision_exists",
                "The investigation already has a final analyst decision.",
            )
        evidence = list_evidence(db, service_principal, row.investigation.id)
        expected_ids = {item.id for item in evidence}
        supplied_ids = {item.evidence_id for item in payload.evidence_decisions}
        if expected_ids != supplied_ids:
            db.rollback()
            raise _conflict(
                "evidence_decisions_incomplete",
                "Every current evidence item requires exactly one analyst decision.",
            )
        if not evidence and not payload.abstain:
            db.rollback()
            raise _conflict(
                "insufficient_evidence",
                "An investigation without evidence must be finalized as an abstention.",
            )
        decisions = {item.evidence_id: item.decision for item in payload.evidence_decisions}
        if not payload.abstain and "accepted" not in decisions.values():
            db.rollback()
            raise _conflict(
                "insufficient_accepted_evidence",
                "A non-abstaining report requires at least one accepted evidence item.",
            )
        # Analyst disposition and provenance verification are intentionally
        # separate. Accepting an observation for a report does not prove that
        # a geometric/source verifier checked it; the durable decision below
        # records accepted/rejected without rewriting verification_state.
        final_hypothesis = (
            payload.final_hypothesis.model_dump(mode="json", by_alias=True, exclude_none=True)
            if payload.final_hypothesis is not None
            else None
        )
        beliefs = list_beliefs(db, service_principal, row.investigation.id)
        latest_belief = beliefs[-1] if beliefs else None
        if not payload.abstain and (latest_belief is None or latest_belief.abstained):
            db.rollback()
            raise _conflict(
                "belief_requires_abstention",
                "The current belief snapshot requires an abstaining analyst decision.",
            )
        if final_hypothesis is not None:
            latest_candidates = latest_belief.candidates if latest_belief is not None else []
            immutable_candidate = next(
                (
                    BeliefCandidate.model_validate(candidate).model_dump(
                        mode="json",
                        by_alias=True,
                        exclude_none=True,
                        exclude={"probability"},
                    )
                    for candidate in latest_candidates
                    if candidate.get("id") == final_hypothesis["id"]
                ),
                None,
            )
            if immutable_candidate != final_hypothesis:
                db.rollback()
                raise _conflict(
                    "final_hypothesis_mismatch",
                    "The final hypothesis must exactly match a current belief candidate.",
                )
            accepted_supports_candidate = False
            for item in evidence:
                if decisions[item.id] != "accepted" or item.polarity != "supports":
                    continue
                details = item.observation.get("details")
                contributions = details.get("contributions") if isinstance(details, dict) else None
                if not isinstance(contributions, list):
                    continue
                if any(
                    isinstance(contribution, dict)
                    and contribution.get("candidate_id") == final_hypothesis["id"]
                    and isinstance(contribution.get("score"), int | float)
                    and not isinstance(contribution.get("score"), bool)
                    and contribution["score"] > 0
                    for contribution in contributions
                ):
                    accepted_supports_candidate = True
                    break
            if not accepted_supports_candidate:
                db.rollback()
                raise _conflict(
                    "accepted_evidence_does_not_support_hypothesis",
                    "At least one accepted evidence item must support the final hypothesis.",
                )
        decision = AnalystDecision(
            organization_id=principal.organization_id,
            job_id=row.job.id,
            investigation_id=row.investigation.id,
            decided_by_principal_id=principal.principal_id,
            status="final",
            evidence_decisions=[
                item.model_dump(mode="json", by_alias=True) for item in payload.evidence_decisions
            ],
            final_hypothesis=final_hypothesis,
            abstained=payload.abstain,
            abstention_reason=payload.abstention_reason,
            notes=payload.notes,
        )
        db.add(decision)
        now = datetime.now(UTC)
        row.investigation.status = "completed"
        row.investigation.final_hypothesis = final_hypothesis
        row.investigation.abstained = payload.abstain
        row.investigation.abstention_reason = payload.abstention_reason
        row.investigation.completed_at = now
        row.investigation.updated_at = now
        # The current softmax is a transparent baseline posterior, not a
        # validation-fitted calibration. Keep the calibrated field empty until
        # a measured temperature/calibration artifact is deployed.
        row.investigation.calibrated_confidence = None

        state = JobState(row.job.status)
        if state == JobState.READY_FOR_REVIEW:
            export_queued = transition_job(
                db,
                row.job.id,
                JobState.READY_FOR_REVIEW,
                JobState.EXPORT_QUEUED,
                values={"stage": "analyst_finalized"},
            )
            exporting = transition_job(
                db,
                row.job.id,
                JobState.EXPORT_QUEUED,
                JobState.EXPORTING,
                values={"stage": "report_finalization", "started_at": row.job.started_at or now},
            )
            completed = transition_job(
                db,
                row.job.id,
                JobState.EXPORTING,
                JobState.COMPLETED,
                values={"stage": "completed", "progress": 100, "completed_at": now},
            )
            if any(item is None for item in (export_queued, exporting, completed)):
                db.rollback()
                raise _conflict(
                    "state_conflict",
                    "The investigation changed concurrently; fetch it before retrying.",
                )
        elif state != JobState.COMPLETED:
            db.rollback()
            raise _conflict(
                "review_state_conflict",
                "The investigation job is not ready for an analyst decision.",
            )
        db.flush()
        append_succeeded(
            db,
            service_principal,
            action="investigation.finalized",
            resource_id=row.investigation.id,
            request_id=getattr(request.state, "request_id", None),
        )
        body = {
            "investigation": detail_body(row),
            "decision": decision_body(decision),
        }
        idempotency.complete(db, claim, status=200, body=body)
    except IntegrationProblem:
        raise
    except IntegrityError:
        db.rollback()
        raise _conflict(
            "decision_exists",
            "The investigation already has a final analyst decision.",
        ) from None
    except SQLAlchemyError:
        db.rollback()
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "The analyst decision could not be persisted.",
            retryable=True,
        ) from None
    return _private_json(body)


@router.get(
    "/investigations/{investigation_id}/report",
    response_model=InvestigationReportResponse,
    operation_id="getBrowserInvestigationReport",
)
def get_browser_investigation_report(
    investigation_id: str,
    principal: BrowserHumanPrincipal,
    db: Session = Depends(get_db),
) -> JSONResponse:
    row = _get_row(db, principal, investigation_id)
    service_principal = _service_principal(principal)
    try:
        evidence = list_evidence(db, service_principal, row.investigation.id)
        beliefs = list_beliefs(db, service_principal, row.investigation.id)
        decision = get_decision(db, service_principal, row.investigation.id)
        source = get_source_record(db, service_principal, row.investigation.id)
        if source is None:
            raise IntegrationProblem(
                503,
                "source_lineage_unavailable",
                "Source lineage unavailable",
                "The investigation source record could not be loaded.",
                retryable=True,
            )
        body = {
            "investigation": detail_body(row),
            "source": source_body(source),
            "decision": decision_body(decision) if decision is not None else None,
            "evidence": [evidence_body(item) for item in evidence],
            "latestBelief": belief_body(beliefs[-1]) if beliefs else None,
        }
    except SQLAlchemyError:
        raise IntegrationProblem(
            503,
            "persistence_unavailable",
            "Persistence unavailable",
            "The investigation report could not be loaded.",
            retryable=True,
        ) from None
    return _private_json(body)
