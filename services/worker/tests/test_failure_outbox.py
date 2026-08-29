"""Analysis-worker terminal failures share the public transactional outbox."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from app.domain.states import JobState
from app.models import Job, JobEvent, Project
from instadescribe_worker.claim import guarded_transition

pytestmark = pytest.mark.skipif(
    not os.environ.get("INSTADESCRIBE_TEST_DATABASE_URL"),
    reason="INSTADESCRIBE_TEST_DATABASE_URL not set (use the disposable PostgreSQL suite)",
)


def test_fenced_analysis_failure_and_outbox_commit_together(db_session):
    project = Project(name="analysis failure")
    db_session.add(project)
    db_session.flush()
    job = Job(
        project_id=project.id,
        pipeline_revision="test",
        status=JobState.PROCESSING.value,
        settings={},
        worker_id="current-fence",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    db_session.add(job)
    db_session.commit()
    original_version = job.version

    assert not guarded_transition(
        db_session,
        job.id,
        "stale-fence",
        JobState.FAILED,
        error_code="invalid_media",
        error_message="safe public message",
        completed_at=sa.func.now(),
        worker_id=None,
    )
    assert (
        db_session.execute(
            sa.select(JobEvent).where(JobEvent.job_id == job.id)
        ).scalar_one_or_none()
        is None
    )

    assert guarded_transition(
        db_session,
        job.id,
        "current-fence",
        JobState.FAILED,
        error_code="invalid_media",
        error_message="safe public message",
        completed_at=sa.func.now(),
        worker_id=None,
    )
    db_session.expire_all()
    stored = db_session.get(Job, job.id)
    event = db_session.execute(sa.select(JobEvent).where(JobEvent.job_id == job.id)).scalar_one()

    assert stored.status == JobState.FAILED.value
    assert stored.version == original_version + 1
    assert event.job_version == stored.version
    assert event.event_type == "job.failed"
    assert event.payload["jobId"] == str(job.id)
    assert event.payload["state"] == "failed"
    assert event.payload["error"] == {"code": "invalid_media"}
    assert "safe public message" not in str(event.payload)


def test_failure_event_id_is_payload_identity(db_session):
    project = Project(name="event identity")
    db_session.add(project)
    db_session.flush()
    job = Job(
        project_id=project.id,
        pipeline_revision="test",
        status=JobState.PROCESSING.value,
        settings={},
        worker_id="current-fence",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    db_session.add(job)
    db_session.commit()

    assert guarded_transition(
        db_session,
        job.id,
        "current-fence",
        JobState.FAILED,
        error_code="retry_exhausted",
        completed_at=sa.func.now(),
        worker_id=None,
    )
    event = db_session.execute(sa.select(JobEvent).where(JobEvent.job_id == job.id)).scalar_one()
    assert uuid.UUID(event.payload["id"]) == event.id
