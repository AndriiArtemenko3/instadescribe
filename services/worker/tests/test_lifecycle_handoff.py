"""Worker persistence handoff into the durable Web Review lifecycle."""

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from app.models import Artifact, Job, JobEvent, Project, Review
from instadescribe_worker.artifacts import LocalArtifact, upload_and_finalize

pytestmark = pytest.mark.skipif(
    not os.environ.get("INSTADESCRIBE_TEST_DATABASE_URL"),
    reason="INSTADESCRIBE_TEST_DATABASE_URL not set (use `make g5-test`)",
)


class RecordingS3:
    def __init__(self) -> None:
        self.puts = []

    def put_object(self, **kwargs):
        self.puts.append(kwargs)
        return {"VersionId": f"version-{len(self.puts)}"}


def test_finalize_atomically_opens_review_and_writes_needs_review_outbox(db_session, tmp_path):
    project = Project(name="lifecycle handoff")
    db_session.add(project)
    db_session.flush()
    job = Job(
        project_id=project.id,
        pipeline_revision="test",
        status="PROCESSING",
        settings={},
        input_object_key=f"uploads/{uuid.uuid4()}/source/input.mp4",
        input_content_type="video/mp4",
        input_size_bytes=3,
        source_version_id="source-version-1",
        source_etag="source-etag",
        worker_id="worker-owner",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    db_session.add(job)
    db_session.commit()

    scenes_path = tmp_path / "scenes.json"
    scenes_path.write_text('[{"scene_id":"scene_1","start":0,"end":1}]')
    scenes = LocalArtifact(
        artifact_type="scenes_json",
        local_path=scenes_path,
        object_key=f"jobs/{job.id}/attempts/1/analysis/scenes.json",
        content_type="application/json",
        meta={"scene_ids": ["scene_1"], "scene_count": 1},
    )
    s3 = RecordingS3()

    # A stale owner may upload attempt-scoped bytes but cannot publish any DB
    # row, review or event.
    assert not upload_and_finalize(
        db_session,
        s3,
        "test-bucket",
        job,
        "stale-owner",
        [scenes],
        "0" * 64,
    )
    assert db_session.execute(sa.select(Artifact).where(Artifact.job_id == job.id)).first() is None
    assert db_session.execute(sa.select(Review).where(Review.job_id == job.id)).first() is None
    assert db_session.execute(sa.select(JobEvent).where(JobEvent.job_id == job.id)).first() is None

    db_session.expire_all()
    job = db_session.get(Job, job.id)
    assert upload_and_finalize(
        db_session,
        s3,
        "test-bucket",
        job,
        "worker-owner",
        [scenes],
        "0" * 64,
    )
    db_session.expire_all()
    stored_job = db_session.get(Job, job.id)
    review = db_session.execute(sa.select(Review).where(Review.job_id == job.id)).scalar_one()
    event = db_session.execute(sa.select(JobEvent).where(JobEvent.job_id == job.id)).scalar_one()
    artifact = db_session.execute(
        sa.select(Artifact).where(
            Artifact.job_id == job.id,
            Artifact.artifact_type == "scenes_json",
        )
    ).scalar_one()

    assert stored_job.status == "READY_FOR_REVIEW"
    assert review.state == "open" and review.organization_id == stored_job.organization_id
    assert artifact.meta == {
        "scene_ids": ["scene_1"],
        "scene_count": 1,
        "version_id": "version-2",
    }
    assert event.event_type == "job.needs_review"
    assert event.job_version == stored_job.version
    assert event.payload["state"] == "needs_review"
    assert event.payload["jobId"] == str(job.id)
