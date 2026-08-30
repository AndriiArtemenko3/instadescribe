"""Atomic claim/reclaim with per-attempt ownership and lease fencing.

`jobs.worker_id` stores a fresh cryptographically strong UUID generated for
EACH successful claim attempt — the fencing value. The configured worker name
is a human-readable log label only: task names can be reused, deployments can
overlap, and sequential attempts run under the same label, so a label can
never fence. Every progress, duration, retry, failure and success predicate
uses the exact claim token; a stale attempt (older token) can therefore never
update the row, delete the message on the row's behalf, or finalize.

Attempt counting happens exactly once per successful normal claim or expired
PROCESSING reclaim — PostgreSQL is the application authority; SQS receive
count is diagnostic only.  A missing lease on a legacy PROCESSING row is
treated as expired so v0.1 crash residue can be recovered after deployment.
"""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from app.domain.states import JobState
from app.models import Job, JobEvent
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from instadescribe_worker.failures import FailureCode

CLAIMABLE = (JobState.QUEUED.value, JobState.UPLOAD_COMPLETE.value)
DEFAULT_LEASE_DURATION_SECS = 300


def _failed_event_values(
    job_id: uuid.UUID,
    organization_id: uuid.UUID,
    job_version: int,
    error_code: str,
) -> dict:
    """Build the bounded public failure event; raw worker errors never cross it."""

    occurred_at = datetime.now(UTC)
    event_id = uuid.uuid4()
    return {
        "id": event_id,
        "organization_id": organization_id,
        "job_id": job_id,
        "event_type": "job.failed",
        "job_version": job_version,
        "payload": {
            "id": str(event_id),
            "type": "job.failed",
            "jobId": str(job_id),
            "state": "failed",
            "occurredAt": occurred_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "error": {"code": error_code[:80]},
        },
        "occurred_at": occurred_at,
        "available_at": occurred_at,
    }


def _execute_failed_transition(
    session: Session,
    statement,
    *,
    job_id: uuid.UUID,
    error_code: str,
) -> bool:
    """Commit one fenced FAILED transition and its public outbox atomically."""

    row = session.execute(
        statement.values(version=Job.version + 1).returning(Job.organization_id, Job.version)
    ).one_or_none()
    if row is not None:
        session.execute(
            pg_insert(JobEvent)
            .values(**_failed_event_values(job_id, row.organization_id, row.version, error_code))
            .on_conflict_do_nothing(index_elements=["organization_id", "job_id", "event_type"])
        )
    session.commit()
    return row is not None


def new_claim_token() -> str:
    return str(uuid.uuid4())


def _lease_deadline(duration_secs: int):
    """Database-clock deadline; the validated integer stays a SQL bind."""
    if (
        isinstance(duration_secs, bool)
        or not isinstance(duration_secs, int)
        or not 30 <= duration_secs <= 1800
    ):
        raise ValueError("lease duration is outside the worker safety bounds")
    # PostgreSQL make_interval(years, months, weeks, days, hours, mins, secs).
    return sa.func.now() + sa.func.make_interval(0, 0, 0, 0, 0, 0, duration_secs)


def _expired_processing_predicate():
    return sa.and_(
        Job.status == JobState.PROCESSING.value,
        sa.or_(Job.lease_expires_at.is_(None), Job.lease_expires_at <= sa.func.now()),
    )


def _configured_attempt_policy(configured_max_attempts: int) -> int:
    if (
        isinstance(configured_max_attempts, bool)
        or not isinstance(configured_max_attempts, int)
        or not 1 <= configured_max_attempts <= 3
    ):
        raise ValueError("configured attempt policy is outside worker safety bounds")
    return configured_max_attempts


def claim_job(
    session: Session,
    job_id: uuid.UUID,
    message_id: uuid.UUID,
    requested_at: datetime,
    *,
    configured_max_attempts: int,
    provider: str | None = None,
    lease_duration_secs: int = DEFAULT_LEASE_DURATION_SECS,
) -> Job | None:
    """Atomically claim queued work or reclaim an expired PROCESSING row.

    The complete durable message identity, provider and attempt budget apply
    to both paths.  On success the returned row carries a new fencing token
    and a database-clock lease; concurrent reclaimers have one winner.
    """
    token = new_claim_token()
    configured_max_attempts = _configured_attempt_policy(configured_max_attempts)
    predicates = [
        Job.id == job_id,
        Job.enqueue_message_id == message_id,
        Job.enqueue_requested_at == requested_at,
        sa.or_(Job.status.in_(CLAIMABLE), _expired_processing_predicate()),
        Job.max_attempts == configured_max_attempts,
        Job.attempt_count < Job.max_attempts,
    ]
    if provider is not None:
        predicates.append(Job.provider == provider)
    stmt = (
        sa.update(Job)
        .where(*predicates)
        .values(
            status=JobState.PROCESSING.value,
            worker_id=token,
            lease_expires_at=_lease_deadline(lease_duration_secs),
            attempt_count=Job.attempt_count + 1,
            started_at=sa.func.coalesce(Job.started_at, sa.func.now()),
            stage="initializing",
            progress=0,
            updated_at=sa.func.now(),
            error_code=None,
            error_message=None,
        )
        .returning(Job)
    )
    try:
        claimed = session.execute(stmt).scalar_one_or_none()
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint in {"uq_jobs_one_compute_active", "organization_job_capacity_limit"}:
            return None
        raise
    return claimed


def exhaust_unclaimable(
    session: Session,
    job_id: uuid.UUID,
    message_id: uuid.UUID,
    requested_at: datetime,
    *,
    configured_max_attempts: int,
    provider: str | None = None,
) -> bool:
    """Durable FAILED/retry_exhausted for a delivery that can no longer claim.

    Atomically requires the job ID, the COMPLETE message identity, a still-
    claimable status AND `attempt_count >= max_attempts` — a row whose
    identity, state or attempt eligibility changed between observation and
    this UPDATE is left alone (rowcount honored by the caller)."""
    configured_max_attempts = _configured_attempt_policy(configured_max_attempts)
    predicates = [
        Job.id == job_id,
        Job.enqueue_message_id == message_id,
        Job.enqueue_requested_at == requested_at,
        sa.or_(Job.status.in_(CLAIMABLE), _expired_processing_predicate()),
        Job.max_attempts == configured_max_attempts,
        Job.attempt_count >= Job.max_attempts,
    ]
    if provider is not None:
        predicates.append(Job.provider == provider)
    return _execute_failed_transition(
        session,
        sa.update(Job)
        .where(*predicates)
        .values(
            status=JobState.FAILED.value,
            error_code=FailureCode.RETRY_EXHAUSTED.value,
            error_message="processing attempts exhausted",
            worker_id=None,
            lease_expires_at=None,
            completed_at=sa.func.now(),
            updated_at=sa.func.now(),
        ),
        job_id=job_id,
        error_code=FailureCode.RETRY_EXHAUSTED.value,
    )


def reject_attempt_policy_mismatch(
    session: Session,
    job_id: uuid.UUID,
    message_id: uuid.UUID,
    requested_at: datetime,
    *,
    configured_max_attempts: int,
    provider: str | None = None,
) -> bool:
    """Fail a claimable/expired row whose persisted attempt policy differs.

    This closes the deployment-policy boundary before any provider work.  A
    fresh PROCESSING lease is never stolen by a differently configured
    worker; it remains fenced until its owner renews or the lease expires.
    """
    configured_max_attempts = _configured_attempt_policy(configured_max_attempts)
    predicates = [
        Job.id == job_id,
        Job.enqueue_message_id == message_id,
        Job.enqueue_requested_at == requested_at,
        sa.or_(Job.status.in_(CLAIMABLE), _expired_processing_predicate()),
        Job.max_attempts != configured_max_attempts,
    ]
    if provider is not None:
        predicates.append(Job.provider == provider)
    return _execute_failed_transition(
        session,
        sa.update(Job)
        .where(*predicates)
        .values(
            status=JobState.FAILED.value,
            error_code=FailureCode.INVALID_SETTINGS.value,
            error_message="job attempt policy does not match this worker",
            worker_id=None,
            lease_expires_at=None,
            completed_at=sa.func.now(),
            updated_at=sa.func.now(),
        ),
        job_id=job_id,
        error_code=FailureCode.INVALID_SETTINGS.value,
    )


def guarded_update(session: Session, job_id: uuid.UUID, owner_token: str, **values) -> bool:
    """Update only while this token owns an unexpired PROCESSING lease."""
    result = session.execute(
        sa.update(Job)
        .where(
            Job.id == job_id,
            Job.status == JobState.PROCESSING.value,
            Job.worker_id == owner_token,
            Job.lease_expires_at.is_not(None),
            Job.lease_expires_at > sa.func.now(),
        )
        .values(updated_at=sa.func.now(), **values)
    )
    session.commit()
    return result.rowcount > 0


def guarded_transition(
    session: Session, job_id: uuid.UUID, owner_token: str, to_state: JobState, **values
) -> bool:
    """Move this claim's PROCESSING row to a new state; stale tokens no-op —
    a False return is OWNERSHIP LOSS, never success (G5.1 A3)."""
    statement = (
        sa.update(Job)
        .where(
            Job.id == job_id,
            Job.status == JobState.PROCESSING.value,
            Job.worker_id == owner_token,
            Job.lease_expires_at.is_not(None),
            Job.lease_expires_at > sa.func.now(),
        )
        .values(
            status=to_state.value,
            lease_expires_at=None,
            updated_at=sa.func.now(),
            **values,
        )
    )
    if to_state == JobState.FAILED:
        error_code = str(values.get("error_code") or FailureCode.INTERNAL_ERROR.value)
        return _execute_failed_transition(
            session,
            statement,
            job_id=job_id,
            error_code=error_code,
        )
    result = session.execute(statement)
    session.commit()
    return result.rowcount > 0


def renew_lease(
    session: Session,
    job_id: uuid.UUID,
    owner_token: str,
    lease_duration_secs: int = DEFAULT_LEASE_DURATION_SECS,
) -> bool:
    """Extend only a still-live lease owned by this exact claim token.

    An already expired lease cannot be resurrected, even if no reclaimer has
    arrived yet.  At the expiry boundary either this conditional renewal or
    an expired-row reclaim can win, never both.
    """
    result = session.execute(
        sa.update(Job)
        .where(
            Job.id == job_id,
            Job.status == JobState.PROCESSING.value,
            Job.worker_id == owner_token,
            Job.lease_expires_at.is_not(None),
            Job.lease_expires_at > sa.func.now(),
        )
        .values(
            lease_expires_at=_lease_deadline(lease_duration_secs),
            updated_at=sa.func.now(),
        )
    )
    session.commit()
    return result.rowcount > 0
