import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa
from app.domain.states import JobState
from app.models import (
    Job,
    Organization,
    OrganizationQuota,
    OrganizationUsagePeriod,
    Project,
)
from app.services.quota import (
    MAX_JOB_MEDIA_SECONDS,
    QuotaExceededError,
    reconcile_measured_media,
    reserve_job_media,
)
from conftest import requires_db
from sqlalchemy.orm import Session


def _job(session: Session, *, estimate: float | None = 60) -> Job:
    marker = uuid.uuid4().hex[:10]
    organization = Organization(slug=f"quota-service-{marker}", name="Quota service")
    session.add(organization)
    session.flush()
    project = Project(organization_id=organization.id, name="Quota source")
    session.add(project)
    session.flush()
    job = Job(
        organization_id=organization.id,
        project_id=project.id,
        pipeline_revision="quota-test",
        status=JobState.AWAITING_UPLOAD.value,
        settings={},
        duration_secs=estimate,
    )
    session.add(job)
    session.flush()
    return job


def _claim(session: Session, job: Job, token: str = "fence-token") -> None:
    job.status = JobState.PROCESSING.value
    job.worker_id = token
    job.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
    session.commit()


@requires_db
def test_reserve_then_reconcile_uses_measured_duration_under_fence(db_engine):
    with Session(db_engine) as session:
        job = _job(session)
        reservation = reserve_job_media(session, job, estimated_seconds=60)
        session.commit()
        assert reservation.reserved_seconds == Decimal("60.000")
        _claim(session, job)

        assert reconcile_measured_media(
            session,
            job.id,
            "fence-token",
            actual_seconds=90.125,
        )
        usage = session.get(OrganizationUsagePeriod, reservation.usage_period_id)
        session.refresh(reservation)
        session.refresh(job)
        assert usage.reserved_media_seconds == 0
        assert usage.consumed_media_seconds == Decimal("90.125")
        assert reservation.state == "consumed"
        assert reservation.actual_seconds == Decimal("90.125")
        assert job.duration_secs == Decimal("90.125")


@requires_db
def test_measured_overage_releases_reservation_before_any_provider_call(db_engine):
    with Session(db_engine) as session:
        job = _job(session)
        quota = session.get(OrganizationQuota, job.organization_id)
        quota.monthly_media_seconds = 100
        reservation = reserve_job_media(session, job, estimated_seconds=60)
        session.commit()
        _claim(session, job)

        with pytest.raises(QuotaExceededError):
            reconcile_measured_media(
                session,
                job.id,
                "fence-token",
                actual_seconds=120,
            )
        session.refresh(reservation)
        usage = session.get(OrganizationUsagePeriod, reservation.usage_period_id)
        assert reservation.state == "released"
        assert usage.reserved_media_seconds == 0
        assert usage.consumed_media_seconds == 0


@requires_db
def test_unknown_duration_reserves_full_file_limit_and_cancel_releases_it(db_engine):
    with Session(db_engine) as session:
        job = _job(session, estimate=None)
        reservation = reserve_job_media(session, job, estimated_seconds=None)
        session.commit()
        assert reservation.reserved_seconds == MAX_JOB_MEDIA_SECONDS

        session.execute(
            sa.update(Job)
            .where(Job.id == job.id)
            .values(status=JobState.CANCELLED.value, completed_at=sa.func.now())
        )
        session.commit()
        session.refresh(reservation)
        usage = session.get(OrganizationUsagePeriod, reservation.usage_period_id)
        assert reservation.state == "released"
        assert usage.reserved_media_seconds == 0


@requires_db
def test_stale_worker_cannot_consume_quota(db_engine):
    with Session(db_engine) as session:
        job = _job(session)
        reservation = reserve_job_media(session, job, estimated_seconds=60)
        session.commit()
        _claim(session, job, token="winner")
        assert not reconcile_measured_media(
            session,
            job.id,
            "stale",
            actual_seconds=60,
        )
        session.rollback()
        usage = session.get(OrganizationUsagePeriod, reservation.usage_period_id)
        assert usage.reserved_media_seconds == Decimal("60.000")
        assert usage.consumed_media_seconds == 0
