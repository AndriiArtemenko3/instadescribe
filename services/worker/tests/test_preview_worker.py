"""PostgreSQL proofs for fenced, exact-version TTS preview execution."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from app.domain.states import JobState
from app.models import (
    Job,
    Organization,
    Principal,
    Project,
    Review,
    TtsPreview,
    TtsPreviewArtifact,
)
from app.services.tts_previews import claim_preview, preview_object_key, renew_preview_lease
from instadescribe_worker.db import get_sessionmaker
from instadescribe_worker.preview import (
    PreviewWorkerFailure,
    cleanup_expired_previews,
    cleanup_orphaned_preview_artifacts,
    run_preview_once,
)
from sqlalchemy.orm import Session


class FakeS3:
    def __init__(self) -> None:
        self.puts: list[dict] = []
        self.deletes: list[dict] = []
        self.on_put = None

    def put_object(self, **kwargs):
        body = kwargs["Body"]
        payload = body.read()
        self.puts.append({**kwargs, "Body": payload})
        if self.on_put is not None:
            self.on_put()
        return {"VersionId": f"preview-version-{len(self.puts)}"}

    def delete_object(self, **kwargs):
        assert kwargs.get("VersionId")
        self.deletes.append(kwargs)
        return {}


def _seed_preview(session: Session, *, created_at: datetime | None = None) -> TtsPreview:
    organization = Organization(
        slug=f"preview-{uuid.uuid4().hex[:12]}",
        name="Preview tenant",
    )
    principal = Principal(
        kind="human",
        display_name="Preview reviewer",
        external_subject=f"preview-subject-{uuid.uuid4()}",
    )
    session.add_all((organization, principal))
    session.flush()
    project = Project(organization_id=organization.id, name="Preview source")
    session.add(project)
    session.flush()
    job = Job(
        organization_id=organization.id,
        project_id=project.id,
        pipeline_revision="test",
        status=JobState.READY_FOR_REVIEW.value,
        settings={},
    )
    session.add(job)
    session.flush()
    session.add(Review(organization_id=organization.id, job_id=job.id, state="open"))
    preview = TtsPreview(
        organization_id=organization.id,
        job_id=job.id,
        requested_by_principal_id=principal.id,
        scene_id="scene_1",
        text="A cyclist passes a red doorway.",
        voice="onyx",
        speed=1,
        request_hash="a" * 64,
        **({"created_at": created_at} if created_at is not None else {}),
        expires_at=(created_at or datetime.now(UTC)) + timedelta(hours=24),
    )
    session.add(preview)
    session.commit()
    session.refresh(preview)
    return preview


def _bytes_synthesizer(text: str, voice: str, speed: float, destination):
    assert text and voice == "onyx" and speed == 1
    destination.write_bytes(b"ID3\x04\x00\x00bounded-preview-audio")
    return destination


def test_preview_worker_publishes_only_version_pinned_audio(db_session, worker_env):
    preview = _seed_preview(db_session)
    preview_id = preview.id
    s3 = FakeS3()

    outcome = run_preview_once(
        worker_env,
        session_factory=get_sessionmaker(),
        s3=s3,
        synthesizer=_bytes_synthesizer,
    )

    assert outcome == "success"
    with Session(db_session.bind) as session:
        stored = session.get(TtsPreview, preview_id)
        assert stored is not None
        assert stored.state == "completed"
        assert stored.worker_id is None and stored.lease_expires_at is None
        assert stored.version_id == "preview-version-1"
        assert stored.object_key == preview_object_key(stored, stored.fence_token)
        assert stored.content_type == "audio/mpeg"
        assert stored.size_bytes == len(b"ID3\x04\x00\x00bounded-preview-audio")
        assert len(stored.checksum_sha256 or "") == 64
        assert session.scalar(sa.select(sa.func.count()).select_from(TtsPreviewArtifact)) == 0
    assert len(s3.puts) == 1
    assert s3.puts[0]["ServerSideEncryption"] == "AES256"
    assert s3.puts[0]["ContentType"] == "audio/mpeg"
    assert s3.deletes == []


def test_cancel_during_upload_cannot_publish_and_deletes_exact_version(db_session, worker_env):
    preview = _seed_preview(db_session)
    preview_id = preview.id
    job_id = preview.job_id
    s3 = FakeS3()

    def cancel_job() -> None:
        with Session(db_session.bind) as other:
            job = other.get(Job, job_id)
            assert job is not None
            job.status = JobState.CANCELLED.value
            job.completed_at = datetime.now(UTC)
            other.commit()

    s3.on_put = cancel_job
    first = run_preview_once(
        worker_env,
        session_factory=get_sessionmaker(),
        s3=s3,
        synthesizer=_bytes_synthesizer,
    )
    second = run_preview_once(
        worker_env,
        session_factory=get_sessionmaker(),
        s3=s3,
        synthesizer=_bytes_synthesizer,
    )

    assert first == "stale_preview"
    assert second == "empty"
    assert len(s3.puts) == 1
    assert s3.deletes == [
        {
            "Bucket": worker_env.media_bucket,
            "Key": s3.puts[0]["Key"],
            "VersionId": "preview-version-1",
        }
    ]
    with Session(db_session.bind) as session:
        stored = session.get(TtsPreview, preview_id)
        assert stored is not None
        assert stored.state == "cancelled"
        assert stored.object_key is None and stored.version_id is None
        assert session.scalar(sa.select(sa.func.count()).select_from(TtsPreviewArtifact)) == 0


def test_expired_lease_reclaims_with_new_fence_and_old_owner_cannot_renew(db_session, worker_env):
    preview = _seed_preview(db_session)
    first = claim_preview(
        db_session,
        preview.organization_id,
        preview.id,
        worker_id="old-worker",
        lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    old_fence = first.fence_token

    outcome = run_preview_once(
        worker_env,
        session_factory=get_sessionmaker(),
        s3=FakeS3(),
        synthesizer=_bytes_synthesizer,
    )

    assert outcome == "success"
    with Session(db_session.bind) as session:
        stored = session.get(TtsPreview, preview.id)
        assert stored is not None
        assert stored.fence_token == old_fence + 1
        assert stored.state == "completed"
        assert not renew_preview_lease(
            session,
            stored.organization_id,
            stored.id,
            worker_id="old-worker",
            fence_token=old_fence,
            lease_expires_at=datetime.now(UTC) + timedelta(minutes=1),
        )


def test_retryable_failure_is_bounded_by_lease_and_later_attempt_succeeds(db_session, worker_env):
    preview = _seed_preview(db_session)

    def retryable(_text, _voice, _speed, _destination):
        raise PreviewWorkerFailure(
            "preview_provider_unavailable",
            "The TTS preview provider is temporarily unavailable.",
            retryable=True,
        )

    first = run_preview_once(
        worker_env,
        session_factory=get_sessionmaker(),
        s3=FakeS3(),
        synthesizer=retryable,
    )
    assert first == "infra_error"
    with Session(db_session.bind) as session:
        stored = session.get(TtsPreview, preview.id)
        assert stored is not None and stored.state == "rendering"
        first_fence = stored.fence_token
        stored.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

    second = run_preview_once(
        worker_env,
        session_factory=get_sessionmaker(),
        s3=FakeS3(),
        synthesizer=_bytes_synthesizer,
    )
    assert second == "success"
    with Session(db_session.bind) as session:
        stored = session.get(TtsPreview, preview.id)
        assert stored is not None
        assert stored.state == "completed"
        assert stored.fence_token == first_fence + 1
        assert stored.attempt_count == 2


def test_orphan_and_expired_cleanup_use_only_exact_versions(db_session, worker_env):
    instant = datetime.now(UTC)
    orphan = _seed_preview(db_session)
    orphan.state = "failed"
    orphan.fence_token = 1
    orphan.attempt_count = 1
    orphan.started_at = instant
    orphan.finished_at = instant
    orphan.error_code = "preview_generation_failed"
    orphan.error_message = "The TTS preview could not be generated."
    orphan_key = preview_object_key(orphan, 1)
    db_session.add(
        TtsPreviewArtifact(
            organization_id=orphan.organization_id,
            job_id=orphan.job_id,
            preview_id=orphan.id,
            fence_token=1,
            object_key=orphan_key,
            version_id="orphan-version",
        )
    )
    expired = _seed_preview(db_session, created_at=instant - timedelta(days=2))
    expired.state = "completed"
    expired.fence_token = 1
    expired.attempt_count = 1
    expired.started_at = instant - timedelta(days=2)
    expired.finished_at = instant - timedelta(days=2) + timedelta(minutes=1)
    expired.object_key = preview_object_key(expired, 1)
    expired.version_id = "expired-version"
    expired.content_type = "audio/mpeg"
    expired.size_bytes = 100
    expired.checksum_sha256 = "e" * 64
    db_session.commit()
    expired_id = expired.id
    expired_key = expired.object_key
    s3 = FakeS3()

    assert cleanup_orphaned_preview_artifacts(db_session, worker_env, s3) == 1
    assert cleanup_expired_previews(db_session, worker_env, s3) == 1

    assert {(item["Key"], item["VersionId"]) for item in s3.deletes} == {
        (orphan_key, "orphan-version"),
        (expired_key, "expired-version"),
    }
    assert all(set(item) == {"Bucket", "Key", "VersionId"} for item in s3.deletes)
    assert db_session.get(TtsPreview, expired_id) is None
    assert db_session.scalar(sa.select(sa.func.count()).select_from(TtsPreviewArtifact)) == 0


def test_expired_preview_metadata_cannot_cascade_an_unpurged_attempt_version(
    db_session, worker_env
):
    instant = datetime.now(UTC)
    expired = _seed_preview(db_session, created_at=instant - timedelta(days=2))
    expired.state = "completed"
    expired.fence_token = 2
    expired.attempt_count = 2
    expired.started_at = instant - timedelta(days=2)
    expired.finished_at = instant - timedelta(days=2) + timedelta(minutes=1)
    expired.object_key = preview_object_key(expired, 2)
    expired.version_id = "published-version"
    expired.content_type = "audio/mpeg"
    expired.size_bytes = 100
    expired.checksum_sha256 = "e" * 64
    stale_key = preview_object_key(expired, 1)
    db_session.add(
        TtsPreviewArtifact(
            organization_id=expired.organization_id,
            job_id=expired.job_id,
            preview_id=expired.id,
            fence_token=1,
            object_key=stale_key,
            version_id="stale-version",
        )
    )
    db_session.commit()
    expired_id = expired.id
    published_key = expired.object_key
    s3 = FakeS3()

    assert cleanup_expired_previews(db_session, worker_env, s3) == 0
    assert db_session.get(TtsPreview, expired_id) is not None
    assert s3.deletes == []

    assert cleanup_orphaned_preview_artifacts(db_session, worker_env, s3) == 1
    assert cleanup_expired_previews(db_session, worker_env, s3) == 1
    assert {(item["Key"], item["VersionId"]) for item in s3.deletes} == {
        (stale_key, "stale-version"),
        (published_key, "published-version"),
    }
    assert db_session.get(TtsPreview, expired_id) is None
