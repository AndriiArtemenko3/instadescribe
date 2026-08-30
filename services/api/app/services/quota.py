"""Atomic monthly media quota reservation and worker reconciliation.

The client-provided duration is only an estimate. Job creation reserves that
estimate (or the full one-hour per-file ceiling when omitted); the worker
reconciles ffprobe's measured duration under its lease fence before the first
paid provider call. No Redis or count-then-insert race is involved.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.domain.states import JobState
from app.models import (
    Job,
    OrganizationQuota,
    OrganizationUsagePeriod,
    QuotaReservation,
)

MAX_JOB_MEDIA_SECONDS = Decimal("3600.000")
UPLOAD_RESERVATION_TTL = timedelta(hours=24)


class QuotaExceededError(Exception):
    """The organization cannot reserve or consume the requested media time."""


class QuotaStateError(Exception):
    """Persisted quota state is missing or internally inconsistent."""


def _seconds(value: float | int | Decimal | None, *, unknown_max: bool) -> Decimal:
    if value is None:
        if unknown_max:
            return MAX_JOB_MEDIA_SECONDS
        raise QuotaStateError("measured duration is required")
    try:
        normalized = Decimal(str(value)).quantize(Decimal("0.001"))
    except (InvalidOperation, ValueError):
        raise QuotaStateError("duration is invalid") from None
    if normalized <= 0 or normalized > MAX_JOB_MEDIA_SECONDS:
        raise QuotaStateError("duration is outside the product limit")
    return normalized


def _period_bounds(now: datetime) -> tuple[date, date]:
    start = date(now.year, now.month, 1)
    end = date(now.year + (now.month == 12), 1 if now.month == 12 else now.month + 1, 1)
    return start, end


def _locked_quota(session: Session, organization_id: uuid.UUID) -> OrganizationQuota:
    quota = session.execute(
        sa.select(OrganizationQuota)
        .where(OrganizationQuota.organization_id == organization_id)
        .with_for_update()
    ).scalar_one_or_none()
    if quota is None:
        raise QuotaStateError("organization quota is not initialized")
    return quota


def _locked_usage(
    session: Session,
    organization_id: uuid.UUID,
    now: datetime,
) -> OrganizationUsagePeriod:
    start, end = _period_bounds(now)
    usage = session.execute(
        sa.select(OrganizationUsagePeriod)
        .where(
            OrganizationUsagePeriod.organization_id == organization_id,
            OrganizationUsagePeriod.period_start == start,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if usage is None:
        usage = OrganizationUsagePeriod(
            organization_id=organization_id,
            period_start=start,
            period_end=end,
        )
        session.add(usage)
        session.flush()
    return usage


def reserve_job_media(
    session: Session,
    job: Job,
    *,
    estimated_seconds: float | int | Decimal | None,
    now: datetime | None = None,
) -> QuotaReservation:
    """Reserve estimated media seconds in the caller's creation transaction."""

    instant = now or datetime.now(UTC)
    estimate = _seconds(estimated_seconds, unknown_max=True)
    quota = _locked_quota(session, job.organization_id)
    usage = _locked_usage(session, job.organization_id, instant)
    existing = session.execute(
        sa.select(QuotaReservation).where(
            QuotaReservation.organization_id == job.organization_id,
            QuotaReservation.job_id == job.id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.state == "reserved" and existing.reserved_seconds == estimate:
            return existing
        raise QuotaStateError("job quota reservation already exists")
    if (
        usage.reserved_media_seconds + usage.consumed_media_seconds + estimate
        > quota.monthly_media_seconds
    ):
        raise QuotaExceededError
    usage.reserved_media_seconds += estimate
    usage.version += 1
    reservation = QuotaReservation(
        organization_id=job.organization_id,
        usage_period_id=usage.id,
        job_id=job.id,
        state="reserved",
        reserved_seconds=estimate,
        expires_at=instant + UPLOAD_RESERVATION_TTL,
    )
    session.add(reservation)
    session.flush()
    return reservation


def reconcile_measured_media(
    session: Session,
    job_id: uuid.UUID,
    owner_token: str,
    *,
    actual_seconds: float | int | Decimal,
    now: datetime | None = None,
) -> bool:
    """Consume measured usage and persist duration under the worker fence.

    Returns ``False`` for stale ownership. A quota failure releases the
    estimate before raising so it cannot strand monthly capacity.
    """

    instant = now or datetime.now(UTC)
    actual = _seconds(actual_seconds, unknown_max=False)
    job = session.execute(
        sa.select(Job)
        .where(
            Job.id == job_id,
            Job.status == JobState.PROCESSING.value,
            Job.worker_id == owner_token,
            Job.lease_expires_at.is_not(None),
            Job.lease_expires_at > sa.func.now(),
        )
        .with_for_update()
    ).scalar_one_or_none()
    if job is None:
        return False
    quota = _locked_quota(session, job.organization_id)
    usage = _locked_usage(session, job.organization_id, instant)
    reservation = session.execute(
        sa.select(QuotaReservation)
        .where(
            QuotaReservation.organization_id == job.organization_id,
            QuotaReservation.job_id == job.id,
        )
        .with_for_update()
    ).scalar_one_or_none()

    if reservation is not None and reservation.state == "consumed":
        if reservation.actual_seconds != actual:
            raise QuotaStateError("measured duration changed after quota consumption")
        job.duration_secs = actual
        session.commit()
        return True
    if reservation is not None and reservation.state != "reserved":
        raise QuotaStateError("job quota reservation is no longer consumable")

    reserved = reservation.reserved_seconds if reservation is not None else Decimal("0")
    projected = usage.reserved_media_seconds - reserved + usage.consumed_media_seconds + actual
    if projected > quota.monthly_media_seconds:
        if reservation is not None:
            usage.reserved_media_seconds -= reserved
            usage.version += 1
            reservation.state = "released"
            reservation.finalized_at = instant
            session.commit()
        raise QuotaExceededError

    usage.reserved_media_seconds -= reserved
    usage.consumed_media_seconds += actual
    usage.version += 1
    if reservation is None:
        reservation = QuotaReservation(
            organization_id=job.organization_id,
            usage_period_id=usage.id,
            job_id=job.id,
            state="consumed",
            reserved_seconds=actual,
            actual_seconds=actual,
            expires_at=instant + UPLOAD_RESERVATION_TTL,
            finalized_at=instant,
        )
        session.add(reservation)
    else:
        reservation.state = "consumed"
        reservation.actual_seconds = actual
        reservation.finalized_at = instant
    job.duration_secs = actual
    session.commit()
    return True


def release_job_media(
    session: Session,
    organization_id: uuid.UUID,
    job_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> bool:
    """Release an unconsumed reservation during cancel/expiry/failure."""

    instant = now or datetime.now(UTC)
    reservation = session.execute(
        sa.select(QuotaReservation)
        .where(
            QuotaReservation.organization_id == organization_id,
            QuotaReservation.job_id == job_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if reservation is None or reservation.state != "reserved":
        return False
    usage = session.execute(
        sa.select(OrganizationUsagePeriod)
        .where(
            OrganizationUsagePeriod.organization_id == organization_id,
            OrganizationUsagePeriod.id == reservation.usage_period_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if usage is None or usage.reserved_media_seconds < reservation.reserved_seconds:
        raise QuotaStateError("quota reservation has no matching usage balance")
    usage.reserved_media_seconds -= reservation.reserved_seconds
    usage.version += 1
    reservation.state = "released"
    reservation.finalized_at = instant
    session.flush()
    return True
