"""Job routes: protected creation, list, get (G3) and verified upload
completion with durable SQS enqueue (G4).

`POST /api/v1/jobs` creates one durable project plus its initial processing
job atomically; the presigned POST is generated inside the transaction
boundary, and both rows roll back if signing fails.
`POST /api/v1/jobs/{job_id}/upload-complete` verifies the exact private
object, durably pins that source identity, and then acquires the compute slot
before publishing.

Strangler adapter (documented, temporary): the v0.1 job routes keep the
legacy dashboard shape — compatibility `id` is the processing **jobId** while
`projectId` is explicit and distinct. No code may treat the two as
interchangeable. `project_name`/`starred` come from `projects`; status is the
legacy lower-case mapping. Every read and write is constrained to the
deterministic portfolio organization associated with the legacy token.
Creation does NOT
reserve the compute slot — `AWAITING_UPLOAD` sits outside the partial index
by design (ADR-0008 §2); the guarantee begins at G4's conditional transition.
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from instadescribe_contracts.queue import QueueMessage
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.rfc3339 import utc_timestamp
from app.core.tenancy import PORTFOLIO_ORGANIZATION_ID
from app.db.session import get_db
from app.domain.states import JobState, to_legacy_status
from app.models import Job, Project
from app.repositories.jobs import transition_job
from app.schemas.jobs import CreateJobRequest, JobSummary
from app.services.quota import QuotaExceededError, QuotaStateError, reserve_job_media
from app.services.s3 import canonical_source_key, generate_upload_post, head_source
from app.services.sqs import send_task_message

logger = logging.getLogger("app.jobs")

router = APIRouter()

# States where a repeated upload-complete call is an idempotent success.
_ALREADY_ACCEPTED = frozenset(
    {
        JobState.QUEUED,
        JobState.PROCESSING,
        JobState.READY_FOR_REVIEW,
        JobState.EXPORT_QUEUED,
        JobState.EXPORTING,
        JobState.COMPLETED,
    }
)


def _is_slot_violation(exc: IntegrityError) -> bool:
    """Recognize only database-enforced active-capacity violations."""
    diag = getattr(getattr(exc, "orig", None), "diag", None)
    return getattr(diag, "constraint_name", None) in {
        "uq_jobs_one_compute_active",
        "organization_job_capacity_limit",
    }


def _norm_content_type(value: str | None) -> str:
    return (value or "").split(";")[0].strip().lower()


def _norm_etag(value: str | None) -> str:
    return (value or "").strip().strip('"')


def _http_error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _legacy_summary(job: Job, project: Project) -> JobSummary:
    state = JobState(job.status)
    return JobSummary.model_validate(
        {
            "id": str(job.id),  # compatibility id == processing jobId (documented adapter)
            "projectId": str(project.id),
            "project_name": project.name,
            "starred": project.starred,
            "projectVersion": project.version,
            "status": to_legacy_status(state),
            "canonicalState": state,
            # This is deliberately independent from lifecycle state. A verified
            # source can remain AWAITING_UPLOAD while another job owns the single
            # compute slot; an unuploaded reservation has no verification tuple.
            "sourceUploaded": _has_verified_source(job),
            "progress": job.progress,
            "stage": job.stage,
            "duration_secs": float(job.duration_secs) if job.duration_secs is not None else None,
            "model": job.model,
            "chunk_size": (job.settings or {}).get("chunk_size"),
            "pipeline_revision": job.pipeline_revision,
            "created_at": utc_timestamp(job.created_at) if job.created_at else None,
            "updated_at": utc_timestamp(job.updated_at) if job.updated_at else None,
            "error": job.error_message,
            "error_code": job.error_code,
        }
    )


def _has_verified_source(job: Job) -> bool:
    """Whether the server durably pinned a complete source identity.

    An ETag alone is not sufficient identity evidence. The bucket is
    versioned, so all three persisted fields are required before the summary
    may tell clients that the upload is recoverable without another S3 POST.
    """
    return bool(job.source_etag and job.source_version_id and job.upload_verified_at)


def _source_identity_matches(
    job: Job,
    etag: str,
    version_id: str,
    checksum: str | None,
) -> bool:
    return (
        _has_verified_source(job)
        and job.source_etag == etag
        and job.source_version_id == version_id
        and (job.source_checksum_sha256 is None) == (checksum is None)
        and (job.source_checksum_sha256 is None or job.source_checksum_sha256 == checksum)
    )


def persist_verified_source(
    db: Session,
    job_id: uuid.UUID,
    job: Job,
    etag: str,
    version_id: str,
    checksum: str | None,
    verified_at: datetime,
) -> Job:
    """Pin a verified source while keeping AWAITING_UPLOAD slot-free.

    The commit intentionally precedes the compute-active transition. If that
    later transition conflicts with the partial unique index, the durable
    source identity remains available to list/recovery clients. A conditional
    first-writer update also makes concurrent completion calls converge on one
    immutable identity rather than overwriting each other.
    """
    if _has_verified_source(job):
        if not _source_identity_matches(job, etag, version_id, checksum):
            raise _http_error(
                409,
                "source_identity_changed",
                "source object changed after verification; create a new job",
            )
        return job

    # A partial tuple is invalid/corrupt evidence and must never be completed
    # by mixing fields from a later object version.
    if any(
        (
            job.source_etag,
            job.source_version_id,
            job.source_checksum_sha256,
            job.upload_verified_at,
        )
    ):
        raise _http_error(
            409,
            "source_identity_changed",
            "source object changed after verification; create a new job",
        )

    try:
        db.execute(
            sa.update(Job)
            .where(
                Job.id == job_id,
                Job.status == JobState.AWAITING_UPLOAD.value,
                Job.source_etag.is_(None),
                Job.source_version_id.is_(None),
                Job.source_checksum_sha256.is_(None),
                Job.upload_verified_at.is_(None),
            )
            .values(
                source_etag=etag,
                source_version_id=version_id,
                source_checksum_sha256=checksum,
                upload_verified_at=verified_at,
                updated_at=sa.func.now(),
            )
        )
        db.commit()
        db.expire_all()
        persisted = db.get(Job, job_id)
    except SQLAlchemyError:
        try:
            db.rollback()
        except Exception:
            pass
        logger.warning("upload_complete_failed category=verification-persistence")
        raise _http_error(503, "persistence_unavailable", "persistence unavailable") from None
    if persisted is None:
        raise _http_error(404, "not_found", "not found")
    if not _source_identity_matches(persisted, etag, version_id, checksum):
        raise _http_error(
            409,
            "source_identity_changed",
            "source object changed after verification; create a new job",
        )
    return persisted


@router.post("/jobs", status_code=201)
def create_job(payload: CreateJobRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    settings = get_settings()
    if settings.provider not in settings.provider_allowlist or not settings.pipeline_revision:
        raise HTTPException(status_code=503, detail="service unavailable")

    job_id = uuid.uuid4()
    object_key = canonical_source_key(str(job_id), payload.file_name)

    project = Project(organization_id=PORTFOLIO_ORGANIZATION_ID, name=payload.name)
    db.add(project)
    db.flush()
    job = Job(
        id=job_id,
        project_id=project.id,
        pipeline_revision=settings.pipeline_revision,
        status=JobState.AWAITING_UPLOAD.value,
        provider=settings.provider,
        model=payload.settings.model,
        # G12 paid-provider smoke is deliberately single-attempt; fake keeps
        # the v0.1 redrive-aligned default of three.
        max_attempts=settings.max_attempts,
        settings=payload.to_worker_settings(),
        input_object_key=object_key,
        input_content_type=payload.content_type,
        input_size_bytes=payload.file_size_bytes,
        duration_secs=payload.duration_secs,
    )
    try:
        db.add(job)
        db.flush()
        reserve_job_media(db, job, estimated_seconds=payload.duration_secs)
        upload = generate_upload_post(object_key, payload.content_type)
    except QuotaExceededError:
        db.rollback()
        raise _http_error(429, "media_quota_exceeded", "monthly media quota exceeded") from None
    except QuotaStateError:
        db.rollback()
        raise _http_error(503, "quota_unavailable", "media quota unavailable") from None
    except IntegrityError as exc:
        db.rollback()
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint == "organization_job_capacity_limit":
            raise _http_error(
                429, "job_capacity_exceeded", "active job capacity exceeded"
            ) from None
        raise _http_error(503, "persistence_unavailable", "persistence unavailable") from None
    except Exception:
        db.rollback()
        # Category only — no endpoint, credential, or exception text.
        logger.warning("presign_failed category=upload-service")
        raise HTTPException(status_code=503, detail="upload service unavailable") from None

    db.commit()
    return {
        "projectId": str(project.id),
        "projectVersion": project.version,
        "jobId": str(job_id),
        "upload": {
            "url": upload["url"],
            "fields": upload["fields"],
            "expiresAt": utc_timestamp(upload["expires_at"]),
        },
    }


@router.post("/jobs/{job_id}/upload-complete", status_code=202)
def upload_complete(job_id: str, db: Session = Depends(get_db)):
    """Verified upload completion + durable enqueue (G4, ADR-0008 §2).

    Recoverable ordering (no atomic PostgreSQL/SQS transaction exists):
    verify the exact private object → durably pin its identity while still
    AWAITING_UPLOAD → durable AWAITING_UPLOAD→UPLOAD_COMPLETE with a stable
    message identity (the compute slot is acquired HERE by the partial unique
    index) → SQS send → UPLOAD_COMPLETE→QUEUED. A slot conflict preserves the
    verified identity without claiming a slot. Send failures keep durable
    UPLOAD_COMPLETE with safe metadata; a late final update never rolls back
    or overwrites PROCESSING/terminal — G5 claims both UPLOAD_COMPLETE and
    QUEUED, so duplicates with the same stable messageId are harmless
    (standard SQS may deliver duplicates; no exactly-once claim).
    """
    try:
        parsed = uuid.UUID(job_id)
    except ValueError:
        raise _http_error(404, "not_found", "not found") from None
    # UUID generation never emits the nil value; reject it without opening a
    # database connection (also keeps unaffiliated liveness/auth probes cheap).
    if parsed.int == 0:
        raise _http_error(404, "not_found", "not found")
    return complete_upload_for_organization(parsed, db, PORTFOLIO_ORGANIZATION_ID)


def complete_upload_for_organization(
    parsed: uuid.UUID,
    db: Session,
    organization_id: uuid.UUID,
):
    """Verify and enqueue one organization-scoped job.

    This is the proven legacy upload-completion state machine with its tenant
    selector made explicit so the Integration API can reuse it without ever
    widening the portfolio token's fixed organization boundary.
    """
    job = db.execute(
        sa.select(Job)
        .join(Project, Job.project_id == Project.id)
        .where(
            Job.id == parsed,
            Project.organization_id == organization_id,
        )
    ).scalar_one_or_none()
    if job is None:
        raise _http_error(404, "not_found", "not found")

    state = JobState(job.status)
    if state in _ALREADY_ACCEPTED:
        return JSONResponse(
            status_code=200,
            content={
                "projectId": str(job.project_id),
                "jobId": str(parsed),
                "status": to_legacy_status(state),
            },
        )
    if state in (JobState.FAILED, JobState.CANCELLED):
        raise _http_error(409, "terminal_conflict", "job is in a terminal state")

    # Verify the EXACT persisted object — nothing client-supplied is trusted.
    try:
        head = head_source(job.input_object_key)
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code in ("404", "NoSuchKey", "NotFound"):
            # Real-AWS nuance reserved for G11: without s3:ListBucket a
            # missing object surfaces as 403, not 404. We deliberately do NOT
            # grant broad ListBucket now; G11 revisits the mapping.
            raise _http_error(
                409, "source_not_visible", "source object not found or not yet visible"
            ) from None
        logger.warning("upload_complete_failed category=storage")
        raise _http_error(503, "storage_unavailable", "storage unavailable") from None
    except BotoCoreError:
        # Transport/timeout/credential-path failures — sanitized identically.
        logger.warning("upload_complete_failed category=storage-transport")
        raise _http_error(503, "storage_unavailable", "storage unavailable") from None

    etag = _norm_etag(head.get("ETag"))
    mismatches = []
    if head.get("ContentLength") != job.input_size_bytes:
        mismatches.append("size")
    if _norm_content_type(head.get("ContentType")) != _norm_content_type(job.input_content_type):
        mismatches.append("content_type")
    if head.get("ServerSideEncryption") != "AES256":
        mismatches.append("encryption")
    if not etag:
        mismatches.append("etag")
    if mismatches:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "source_mismatch",
                "message": "uploaded object does not match the declared source",
                "checks": mismatches,
            },
        )
    version_id = head.get("VersionId")
    if not version_id:
        # G5.1 C1: source identity is pinned by VersionId; a bucket without
        # versioning is an infrastructure/configuration failure — sanitized,
        # retryable, and it must NOT acquire the compute slot or enqueue.
        logger.warning("upload_complete_failed category=versioning")
        raise _http_error(503, "storage_unavailable", "storage unavailable")
    checksum = head.get("ChecksumSHA256")
    now = datetime.now(UTC)

    if state == JobState.AWAITING_UPLOAD:
        # Persist verification separately from slot acquisition. A capacity
        # conflict rolls back only the transition below, never this marker.
        job = persist_verified_source(db, parsed, job, etag, version_id, checksum, now)
        message_id = uuid.uuid4()
        requested_at = now
        try:
            moved = transition_job(
                db,
                parsed,
                JobState.AWAITING_UPLOAD,
                JobState.UPLOAD_COMPLETE,
                values={
                    "enqueue_message_id": message_id,
                    "enqueue_requested_at": requested_at,
                    "enqueue_attempt_count": Job.enqueue_attempt_count + 1,
                },
            )
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            if _is_slot_violation(exc):
                raise _http_error(
                    409,
                    "capacity_conflict",
                    "organization active job capacity is full; retry later",
                ) from None
            raise
        if moved is None:
            # A concurrent call for the SAME job won the transition: reuse the
            # winner's persisted identity — never a loser-generated one.
            db.expire_all()
            job = db.get(Job, parsed)
            state = JobState(job.status)
            if state in _ALREADY_ACCEPTED:
                return JSONResponse(
                    status_code=200,
                    content={
                        "projectId": str(job.project_id),
                        "jobId": str(parsed),
                        "status": to_legacy_status(state),
                    },
                )
            if state != JobState.UPLOAD_COMPLETE:
                raise _http_error(409, "terminal_conflict", "job is in a terminal state")
            message_id, requested_at = _retry_identity(db, parsed, job, etag, version_id, checksum)
    else:  # UPLOAD_COMPLETE — publication retry
        message_id, requested_at = _retry_identity(db, parsed, job, etag, version_id, checksum)

    # Immutable response identity captured BEFORE the send: after a
    # successful send, no database outage may trigger a lazy ORM refresh and
    # turn the promised accepted 202 into a 500.
    response_project_id = str(job.project_id)
    response_job_id = str(parsed)

    message = QueueMessage(
        schema_version=1,
        message_id=message_id,
        task_type="ANALYZE",
        job_id=parsed,
        requested_at=requested_at,
    )
    try:
        send_task_message(message)
    except Exception:
        _record_enqueue_failure(db, parsed, now)
        # Category only — no queue URL, credential, or AWS error text.
        logger.warning("upload_complete_failed category=queue")
        raise _http_error(503, "enqueue_unavailable", "queue unavailable; retry") from None

    current = JobState.QUEUED
    try:
        final = transition_job(
            db,
            parsed,
            JobState.UPLOAD_COMPLETE,
            JobState.QUEUED,
            values={"enqueued_at": now, "enqueue_failed_at": None, "enqueue_error": None},
        )
        db.commit()
        if final is None:
            # Race with a worker/finalizer: never roll back, never overwrite.
            db.expire_all()
            refreshed = db.get(Job, parsed)
            current = JobState(refreshed.status)
    except Exception:
        # Send succeeded; the durable UPLOAD_COMPLETE row + stable identity
        # keep this recoverable (G5 claims both states). Accepted regardless —
        # using the pre-captured identity, no ORM refresh required.
        db.rollback()
        logger.warning("upload_complete_finalize_failed category=database")
        current = JobState.UPLOAD_COMPLETE
    return {
        "projectId": response_project_id,
        "jobId": response_job_id,
        "status": to_legacy_status(current),
    }


def _record_enqueue_failure(db: Session, job_id: uuid.UUID, now: datetime) -> None:
    """Persist safe enqueue-failure metadata; if even that write fails, roll
    back best-effort and keep the committed UPLOAD_COMPLETE — the caller still
    returns the sanitized retryable response either way."""
    try:
        db.execute(
            sa.update(Job)
            .where(Job.id == job_id, Job.status == JobState.UPLOAD_COMPLETE.value)
            .values(enqueue_failed_at=now, enqueue_error="sqs_send_failed")
        )
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        logger.warning("upload_complete_failed category=failure-metadata")


def _retry_identity(
    db: Session,
    job_id: uuid.UUID,
    job: Job,
    etag: str,
    version_id: str | None,
    checksum: str | None,
) -> tuple[uuid.UUID, datetime]:
    """Reuse the persisted stable enqueue identity for a publication retry,
    refusing silently-changed sources (presigned-POST reuse/overwrite TOCTOU).
    The route has already re-Headed the object. G5.1 C1: the VersionId is
    compared EXACTLY (both sides required), and the optional checksum is
    compared symmetrically in presence AND value — identity evidence that
    disappears is a refusal, never a silent acceptance. A SHA-256 is never
    derived from an ETag."""
    if (
        job.source_etag != etag
        or not job.source_version_id
        or version_id != job.source_version_id
        or (job.source_checksum_sha256 is None) != (checksum is None)
        or (job.source_checksum_sha256 is not None and checksum != job.source_checksum_sha256)
    ):
        raise _http_error(
            409,
            "source_identity_changed",
            "source object changed after verification; create a new job",
        )
    if job.enqueue_message_id is None:
        # Pre-0003 legacy row: assign an identity once, race-safely.
        db.execute(
            sa.update(Job)
            .where(Job.id == job_id, Job.enqueue_message_id.is_(None))
            .values(enqueue_message_id=uuid.uuid4(), enqueue_requested_at=datetime.now(UTC))
        )
        db.commit()
        db.expire_all()
        job = db.get(Job, job_id)
    db.execute(
        sa.update(Job)
        .where(Job.id == job_id, Job.status == JobState.UPLOAD_COMPLETE.value)
        .values(enqueue_attempt_count=Job.enqueue_attempt_count + 1)
    )
    db.commit()
    return job.enqueue_message_id, job.enqueue_requested_at


@router.get("/jobs", response_model=dict[str, JobSummary], response_model_by_alias=True)
def list_jobs(limit: int = 50, db: Session = Depends(get_db)) -> dict[str, JobSummary]:
    limit = max(1, min(limit, 100))
    rows = db.execute(
        sa.select(Job, Project)
        .join(Project, Job.project_id == Project.id)
        .where(Project.organization_id == PORTFOLIO_ORGANIZATION_ID)
        # Migration 0002 intentionally reused each populated pre-G3 job UUID
        # for its synthetic project. Those rows cannot satisfy the cloud
        # contract's distinct project/job identities and have no G3+ cloud
        # provenance, so omit them rather than breaking portfolio admission.
        .where(Job.id != Job.project_id)
        .order_by(Job.created_at.desc())
        .limit(limit)
    ).all()
    return {str(row.Job.id): _legacy_summary(row.Job, row.Project) for row in rows}


@router.get("/jobs/{job_id}", response_model=JobSummary, response_model_by_alias=True)
def get_job(job_id: str, db: Session = Depends(get_db)) -> JobSummary:
    try:
        parsed = uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="not found") from None
    row = db.execute(
        sa.select(Job, Project)
        .join(Project, Job.project_id == Project.id)
        .where(
            Job.id == parsed,
            Project.organization_id == PORTFOLIO_ORGANIZATION_ID,
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    return _legacy_summary(row.Job, row.Project)
