"""Focused persistence tests for the database-polled five-format render worker."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import instadescribe_worker.render as render_worker
import pytest
import sqlalchemy as sa
from app.core.tenancy import (
    PORTFOLIO_ORGANIZATION_ID,
    PORTFOLIO_PRINCIPAL_ID,
    PrincipalContext,
)
from app.models import (
    Artifact,
    Deliverable,
    Job,
    JobEvent,
    Project,
    Render,
    RenderAttemptArtifact,
    Review,
    SceneOverride,
)
from app.services.lifecycle import claim_render
from botocore.exceptions import ClientError
from instadescribe_contracts.provider import TTS_BETA_MAX_RENDER_ATTEMPTS_PER_REVIEW
from instadescribe_worker.render import (
    RenderCancelled,
    RenderLeaseHeartbeat,
    cleanup_orphaned_render_attempts,
    run_render_once,
)
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.skipif(
    not os.environ.get("INSTADESCRIBE_TEST_DATABASE_URL"),
    reason="INSTADESCRIBE_TEST_DATABASE_URL not set (use `make g5-test`)",
)


class FakeBody:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.closed = False

    def iter_chunks(self, chunk_size: int):
        for offset in range(0, len(self.body), chunk_size):
            yield self.body[offset : offset + chunk_size]

    def close(self) -> None:
        self.closed = True


class FakeS3:
    def __init__(
        self,
        objects: dict[tuple[str, str], tuple[bytes, str]],
        *,
        fail_put: int | None = None,
        fail_delete: int | None = None,
        after_put=None,
    ):
        self.objects = objects
        self.fail_put = fail_put
        self.fail_delete = fail_delete
        self.after_put = after_put
        self.puts: list[dict] = []
        self.deletes: list[dict] = []
        self.delete_attempts = 0

    def get_object(self, **kwargs):
        body, etag = self.objects[(kwargs["Key"], kwargs["VersionId"])]
        return {
            "Body": FakeBody(body),
            "ETag": f'"{etag}"',
            "VersionId": kwargs["VersionId"],
        }

    def put_object(self, **kwargs):
        attempt = len(self.puts) + 1
        if self.fail_put == attempt:
            raise ClientError(
                {"Error": {"Code": "ServiceUnavailable", "Message": "not exposed"}},
                "PutObject",
            )
        body = kwargs["Body"].read()
        stored = {**kwargs, "Body": body}
        self.puts.append(stored)
        response = {"VersionId": f"output-version-{attempt}", "ETag": f'"output-{attempt}"'}
        if self.after_put is not None:
            self.after_put(attempt)
        return response

    def delete_object(self, **kwargs):
        self.delete_attempts += 1
        attempt = self.delete_attempts
        if self.fail_delete == attempt:
            raise ClientError(
                {"Error": {"Code": "ServiceUnavailable", "Message": "not exposed"}},
                "DeleteObject",
            )
        assert kwargs.get("VersionId")
        self.deletes.append(kwargs)
        return {"VersionId": kwargs["VersionId"]}


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _seed_render(db_session, *, approved: bool = True, expired: bool = False):
    project = Project(
        organization_id=PORTFOLIO_ORGANIZATION_ID,
        name="Render project",
    )
    db_session.add(project)
    db_session.flush()
    job_id = uuid.uuid4()
    source_key = f"uploads/orgs/{PORTFOLIO_ORGANIZATION_ID}/jobs/{job_id}/source/input.mp4"
    source = b"version-pinned-video"
    scenes = json.dumps(
        [{"scene_id": "scene_1", "start": 0.0, "end": 2.0, "caption": "Generated AD"}],
        separators=(",", ":"),
    ).encode()
    entities = json.dumps([{"id": "char_1", "name": "Person"}], separators=(",", ":")).encode()
    job = Job(
        id=job_id,
        organization_id=PORTFOLIO_ORGANIZATION_ID,
        project_id=project.id,
        pipeline_revision="test",
        status="EXPORTING" if expired else "EXPORT_QUEUED",
        stage="rendering" if expired else "render_queued",
        settings={"project_name": "Locked project title", "voice": "alloy"},
        input_object_key=source_key,
        input_content_type="video/mp4",
        input_size_bytes=len(source),
        source_version_id="source-version",
        source_etag="source-etag",
    )
    db_session.add(job)
    db_session.flush()
    now = datetime.now(UTC)
    review = Review(
        organization_id=PORTFOLIO_ORGANIZATION_ID,
        job_id=job.id,
        state="completed",
        version=2,
        scene_count=1,
        approved_scene_count=1 if approved else 0,
        rejected_scene_count=0 if approved else 1,
        locked_at=now,
        completed_at=now,
        completed_by_principal_id=PORTFOLIO_PRINCIPAL_ID,
        zero_ad_confirmed_at=None if approved else now,
    )
    db_session.add(review)
    db_session.flush()
    render = Render(
        organization_id=PORTFOLIO_ORGANIZATION_ID,
        job_id=job.id,
        review_id=review.id,
        state="rendering" if expired else "queued",
        fence_token=4 if expired else 1,
        attempt_count=1 if expired else 0,
        worker_id="expired-owner" if expired else None,
        lease_expires_at=now - timedelta(seconds=1) if expired else None,
        started_at=now - timedelta(minutes=1) if expired else None,
    )
    db_session.add(render)
    db_session.add(
        SceneOverride(
            job_id=job.id,
            scene_id="scene_1",
            text="Reviewed AD" if approved else "Rejected AD",
            voice="nova" if approved else None,
            speed=1.25 if approved else None,
            review_status="approved" if approved else "rejected",
            reviewed_at=now,
        )
    )
    artifact_specs = (
        (
            "source_video",
            source_key,
            "video/mp4",
            source,
            {"version_id": "source-version", "etag": "source-etag"},
        ),
        (
            "scenes_json",
            f"jobs/{job.id}/attempts/1/analysis/scenes.json",
            "application/json",
            scenes,
            {"version_id": "scenes-version", "scene_ids": ["scene_1"], "scene_count": 1},
        ),
        (
            "entities_json",
            f"jobs/{job.id}/attempts/1/analysis/entities.json",
            "application/json",
            entities,
            {"version_id": "entities-version"},
        ),
    )
    objects: dict[tuple[str, str], tuple[bytes, str]] = {}
    for artifact_type, key, content_type, body, meta in artifact_specs:
        db_session.add(
            Artifact(
                organization_id=job.organization_id,
                job_id=job.id,
                artifact_type=artifact_type,
                object_key=key,
                version_id=meta["version_id"],
                content_type=content_type,
                size_bytes=len(body),
                checksum_sha256=_sha(body),
                meta=meta,
            )
        )
        objects[(key, meta["version_id"])] = (body, meta.get("etag", f"{artifact_type}-etag"))
    db_session.commit()
    return job, render, objects


def _renderer(captured: dict, *, expected_approved: bool):
    def render(**kwargs):
        captured.update(kwargs)
        scenes = kwargs["scenes"]
        assert len(scenes) == 1
        assert scenes[0]["review_state"] == ("approved" if expected_approved else "rejected")
        if expected_approved:
            assert (scenes[0]["text"], scenes[0]["voice"], scenes[0]["speed"]) == (
                "Reviewed AD",
                "nova",
                1.25,
            )
        kwargs["on_progress"]("rendering", 50)
        output_dir = kwargs["output_dir"]
        output_dir.mkdir(parents=True)
        names = {
            "mp4": "described_video.mp4",
            "mp3": "audio_description.mp3",
            "srt": "audio_description.srt",
            "csv": "audio_description.csv",
            "docx": "audio_description.docx",
        }
        outputs = {}
        for format_name, name in names.items():
            path = output_dir / name
            body = b"" if not expected_approved and format_name == "srt" else format_name.encode()
            path.write_bytes(body)
            outputs[format_name] = path
        return outputs

    return render


def _run(db_session, worker_env, tmp_path, s3, renderer):
    maker = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    settings = worker_env.model_copy(
        update={"workspace_root": str(tmp_path), "render_lease_duration_secs": 300}
    )
    return run_render_once(
        settings,
        session_factory=maker,
        s3=s3,
        renderer=renderer,
    )


def test_render_lease_heartbeat_renews_from_an_independent_session(
    db_session, worker_env, monkeypatch
):
    job, _render, _objects = _seed_render(db_session)
    principal = PrincipalContext(
        organization_id=PORTFOLIO_ORGANIZATION_ID,
        principal_id=PORTFOLIO_PRINCIPAL_ID,
        principal_type="worker",
        scopes=frozenset(),
    )
    lease_expires_at = datetime.now(UTC) + timedelta(seconds=60)
    claimed = claim_render(
        db_session,
        principal,
        job.id,
        worker_id="heartbeat-owner",
        lease_expires_at=lease_expires_at,
    )
    maker = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    pulsed = threading.Event()
    original = render_worker.renew_render_lease

    def tracked_renewal(*args, **kwargs):
        result = original(*args, **kwargs)
        pulsed.set()
        return result

    monkeypatch.setattr(render_worker, "renew_render_lease", tracked_renewal)
    heartbeat = RenderLeaseHeartbeat(
        maker,
        worker_env.model_copy(update={"render_lease_duration_secs": 60}),
        principal,
        job.id,
        worker_id=claimed.worker_id,
        fence_token=claimed.fence_token,
        interval_secs=0.01,
    )
    heartbeat.start()
    assert pulsed.wait(timeout=2)
    heartbeat.stop()
    heartbeat.assert_healthy(db_session)

    db_session.expire_all()
    renewed = db_session.get(Render, claimed.id)
    assert renewed.lease_expires_at > lease_expires_at


def test_render_lease_heartbeat_resolves_cancellation_before_publish(db_session, worker_env):
    job, _render, _objects = _seed_render(db_session)
    principal = PrincipalContext(
        organization_id=PORTFOLIO_ORGANIZATION_ID,
        principal_id=PORTFOLIO_PRINCIPAL_ID,
        principal_type="worker",
        scopes=frozenset(),
    )
    claimed = claim_render(
        db_session,
        principal,
        job.id,
        worker_id="cancelled-heartbeat-owner",
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=60),
    )
    maker = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    db_session.execute(
        sa.update(Job)
        .where(Job.organization_id == PORTFOLIO_ORGANIZATION_ID, Job.id == job.id)
        .values(status="CANCELLED", completed_at=sa.func.now())
    )
    db_session.commit()

    heartbeat = RenderLeaseHeartbeat(
        maker,
        worker_env.model_copy(update={"render_lease_duration_secs": 60}),
        principal,
        job.id,
        worker_id=claimed.worker_id,
        fence_token=claimed.fence_token,
        interval_secs=0.01,
    )
    heartbeat.start()
    assert heartbeat._lost.wait(timeout=2)  # noqa: SLF001 - exact thread synchronization
    heartbeat.stop()
    with pytest.raises(RenderCancelled):
        heartbeat.assert_healthy(db_session)

    db_session.expire_all()
    assert db_session.get(Render, claimed.id).state == "cancelled"


def test_complete_render_uploads_and_atomically_publishes_exact_five(
    db_session, worker_env, tmp_path
):
    job, render, objects = _seed_render(db_session)
    s3 = FakeS3(objects)
    captured = {}
    assert _run(
        db_session, worker_env, tmp_path, s3, _renderer(captured, expected_approved=True)
    ) == ("success")

    db_session.expire_all()
    stored_job = db_session.get(Job, job.id)
    stored_render = db_session.get(Render, render.id)
    rows = list(
        db_session.execute(
            sa.select(Deliverable).where(Deliverable.render_id == render.id)
        ).scalars()
    )
    event = db_session.execute(
        sa.select(JobEvent).where(
            JobEvent.job_id == job.id,
            JobEvent.event_type == "job.completed",
        )
    ).scalar_one()
    assert stored_job.status == "COMPLETED"
    assert stored_render.state == "completed" and stored_render.attempt_count == 1
    assert len(rows) == 5 and {row.format for row in rows} == {"mp4", "mp3", "srt", "csv", "docx"}
    assert all(row.state == "published" and row.published_at is not None for row in rows)
    assert all("/attempts/2/" in row.object_key for row in rows)
    assert [item["ServerSideEncryption"] for item in s3.puts] == ["AES256"] * 5
    assert {row.version_id for row in rows} == {f"output-version-{n}" for n in range(1, 6)}
    assert event.payload["state"] == "completed"
    assert stored_render.integrity_manifest["deliverableCount"] == 5
    assert db_session.scalar(sa.select(sa.func.count(RenderAttemptArtifact.id))) == 0
    assert s3.deletes == []
    assert captured["default_voice"] == "alloy"
    assert not Path(captured["output_dir"]).exists()


def test_worker_shutdown_leaves_render_nonterminal_for_lease_reclaim(
    db_session, worker_env, tmp_path
):
    """Scale-in is not a customer-visible render failure.

    The active renderer aborts, uploads/publish never start, and the fenced
    rendering lease remains available for expiry and a later reclaim.
    """

    from instadescribe_worker.executor import WorkerShutdownRequested, request_shutdown

    job, render, objects = _seed_render(db_session)
    s3 = FakeS3(objects)

    def interrupted_renderer(**_kwargs):
        request_shutdown()
        raise WorkerShutdownRequested

    assert _run(db_session, worker_env, tmp_path, s3, interrupted_renderer) == "shutdown"

    db_session.expire_all()
    stored_job = db_session.get(Job, job.id)
    stored_render = db_session.get(Render, render.id)
    assert (stored_job.status, stored_job.error_code, stored_job.completed_at) == (
        "EXPORTING",
        None,
        None,
    )
    assert (
        stored_render.state,
        stored_render.error_code,
        stored_render.completed_at,
    ) == ("rendering", None, None)
    assert stored_render.worker_id is not None
    assert stored_render.lease_expires_at is not None
    assert not s3.puts
    assert not s3.deletes
    assert (
        db_session.scalar(
            sa.select(sa.func.count())
            .select_from(Deliverable)
            .where(Deliverable.render_id == render.id)
        )
        == 0
    )
    assert (
        db_session.scalar(
            sa.select(sa.func.count())
            .select_from(JobEvent)
            .where(
                JobEvent.job_id == job.id,
                JobEvent.event_type.in_({"job.completed", "job.failed"}),
            )
        )
        == 0
    )


def test_zero_ad_review_produces_a_valid_neutral_bundle(db_session, worker_env, tmp_path):
    job, render, objects = _seed_render(db_session, approved=False)
    s3 = FakeS3(objects)
    captured = {}
    assert (
        _run(
            db_session,
            worker_env,
            tmp_path,
            s3,
            _renderer(captured, expected_approved=False),
        )
        == "success"
    )
    db_session.expire_all()
    assert db_session.get(Job, job.id).status == "COMPLETED"
    assert db_session.get(Render, render.id).state == "completed"
    rows = list(
        db_session.execute(
            sa.select(Deliverable).where(Deliverable.render_id == render.id)
        ).scalars()
    )
    assert len(rows) == 5
    srt = next(row for row in rows if row.format == "srt")
    assert srt.size_bytes == 0 and srt.checksum_sha256 == _sha(b"")


def test_render_snapshot_rechecks_tts_budget_before_invoking_renderer(
    db_session, worker_env, tmp_path, monkeypatch
):
    job, render, objects = _seed_render(db_session, approved=True)
    s3 = FakeS3(objects)
    monkeypatch.setattr(render_worker, "TTS_BETA_MAX_APPROVED_SCENES_PER_REVIEW", 0)

    def forbidden_renderer(**_kwargs):
        pytest.fail("over-budget snapshot invoked the TTS-capable renderer")

    assert _run(db_session, worker_env, tmp_path, s3, forbidden_renderer) == "failed"
    db_session.expire_all()
    assert (db_session.get(Job, job.id).status, db_session.get(Job, job.id).error_code) == (
        "FAILED",
        "render_input_invalid",
    )
    assert (
        db_session.get(Render, render.id).state,
        db_session.get(Render, render.id).error_code,
    ) == ("failed", "render_input_invalid")
    assert s3.puts == []


def test_janitor_never_selects_an_exact_identity_with_published_metadata(
    db_session, worker_env, tmp_path
):
    job, render, objects = _seed_render(db_session)
    s3 = FakeS3(objects)
    assert (
        _run(
            db_session,
            worker_env,
            tmp_path,
            s3,
            _renderer({}, expected_approved=True),
        )
        == "success"
    )
    db_session.expire_all()
    published = db_session.scalar(
        sa.select(Deliverable).where(
            Deliverable.render_id == render.id,
            Deliverable.format == "mp4",
        )
    )
    stored_render = db_session.get(Render, render.id)
    db_session.add(
        RenderAttemptArtifact(
            organization_id=published.organization_id,
            job_id=job.id,
            render_id=render.id,
            fence_token=stored_render.fence_token,
            format=published.format,
            object_key=published.object_key,
            version_id=published.version_id,
        )
    )
    db_session.commit()

    assert cleanup_orphaned_render_attempts(db_session, worker_env, s3, limit=20) == 0
    assert s3.deletes == []
    assert db_session.scalar(sa.select(sa.func.count(RenderAttemptArtifact.id))) == 1


def test_partial_upload_never_stages_and_fails_current_fence_atomically(
    db_session, worker_env, tmp_path
):
    job, render, objects = _seed_render(db_session)
    s3 = FakeS3(objects, fail_put=3)
    captured = {}
    assert _run(
        db_session, worker_env, tmp_path, s3, _renderer(captured, expected_approved=True)
    ) == ("failed")
    db_session.expire_all()
    stored_job = db_session.get(Job, job.id)
    stored_render = db_session.get(Render, render.id)
    assert (stored_job.status, stored_job.error_code) == ("FAILED", "deliverable_upload_failed")
    assert (stored_render.state, stored_render.error_code) == (
        "failed",
        "deliverable_upload_failed",
    )
    assert (
        db_session.execute(
            sa.select(sa.func.count())
            .select_from(Deliverable)
            .where(Deliverable.render_id == render.id)
        ).scalar_one()
        == 0
    )
    event = db_session.execute(
        sa.select(JobEvent).where(
            JobEvent.job_id == job.id,
            JobEvent.event_type == "job.failed",
        )
    ).scalar_one()
    assert event.payload["errorCode"] == "deliverable_upload_failed"
    assert len(s3.puts) == 2
    assert [item["VersionId"] for item in s3.deletes] == [
        "output-version-1",
        "output-version-2",
    ]
    assert all(item["Key"].startswith("deliverables/orgs/") for item in s3.deletes)
    assert db_session.scalar(sa.select(sa.func.count(RenderAttemptArtifact.id))) == 0
    assert not Path(captured["output_dir"]).exists()


def test_delete_failure_keeps_exact_journal_for_bounded_janitor_retry(
    db_session, worker_env, tmp_path
):
    job, render, objects = _seed_render(db_session)
    s3 = FakeS3(objects, fail_put=3, fail_delete=1)

    assert (
        _run(
            db_session,
            worker_env,
            tmp_path,
            s3,
            _renderer({}, expected_approved=True),
        )
        == "failed"
    )
    db_session.expire_all()
    journal = list(db_session.scalars(sa.select(RenderAttemptArtifact)))
    assert [(row.object_key, row.version_id) for row in journal] == [
        (
            f"deliverables/orgs/{PORTFOLIO_ORGANIZATION_ID}/jobs/{job.id}/"
            "attempts/2/described_video.mp4",
            "output-version-1",
        )
    ]
    assert [item["VersionId"] for item in s3.deletes] == ["output-version-2"]

    assert cleanup_orphaned_render_attempts(db_session, worker_env, s3, limit=20) == 1
    assert db_session.scalar(sa.select(sa.func.count(RenderAttemptArtifact.id))) == 0
    assert [item["VersionId"] for item in s3.deletes] == [
        "output-version-2",
        "output-version-1",
    ]
    assert db_session.get(Render, render.id).state == "failed"


@pytest.mark.parametrize("race", ["cancel", "reclaim"])
def test_post_upload_cancel_or_reclaim_deletes_only_losing_exact_version(
    db_session, worker_env, tmp_path, race
):
    job, render, objects = _seed_render(db_session)
    maker = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)

    def race_after_first_put(attempt: int) -> None:
        if attempt != 1:
            return
        with maker() as other:
            if race == "cancel":
                other.execute(
                    sa.update(Job)
                    .where(Job.id == job.id)
                    .values(status="CANCELLED", completed_at=sa.func.now())
                )
                other.commit()
                return
            other.execute(
                sa.update(Render)
                .where(Render.id == render.id)
                .values(lease_expires_at=sa.func.now() - sa.text("interval '1 second'"))
            )
            other.commit()
            claim_render(
                other,
                PrincipalContext(
                    organization_id=PORTFOLIO_ORGANIZATION_ID,
                    principal_id=PORTFOLIO_PRINCIPAL_ID,
                    principal_type="worker",
                    scopes=frozenset(),
                ),
                job.id,
                worker_id="new-owner-after-upload",
                lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )

    s3 = FakeS3(objects, after_put=race_after_first_put)
    outcome = _run(
        db_session,
        worker_env,
        tmp_path,
        s3,
        _renderer({}, expected_approved=True),
    )
    assert outcome == ("cancelled" if race == "cancel" else "stale_render")
    assert len(s3.puts) == 1
    assert [(item["Key"], item["VersionId"]) for item in s3.deletes] == [
        (s3.puts[0]["Key"], "output-version-1")
    ]
    assert db_session.scalar(sa.select(sa.func.count(RenderAttemptArtifact.id))) == 0
    assert db_session.scalar(sa.select(sa.func.count(Deliverable.id))) == 0


def test_renderer_failure_is_classified_and_failed_only_by_current_fence(
    db_session, worker_env, tmp_path
):
    job, render, objects = _seed_render(db_session)
    s3 = FakeS3(objects)

    def failing_renderer(**_kwargs):
        raise RuntimeError("private renderer detail")

    assert _run(db_session, worker_env, tmp_path, s3, failing_renderer) == "failed"
    db_session.expire_all()
    stored_job = db_session.get(Job, job.id)
    stored_render = db_session.get(Render, render.id)
    assert (stored_job.status, stored_job.error_code, stored_job.error_message) == (
        "FAILED",
        "render_failed",
        "The five-format render failed.",
    )
    assert (stored_render.state, stored_render.error_code) == ("failed", "render_failed")
    assert not s3.puts
    assert (
        db_session.execute(
            sa.select(sa.func.count())
            .select_from(Deliverable)
            .where(Deliverable.render_id == render.id)
        ).scalar_one()
        == 0
    )


@pytest.mark.parametrize("race", ["cancel", "reclaim"])
def test_cancelled_or_stale_fence_never_uploads_or_publishes(
    db_session, worker_env, tmp_path, race
):
    job, render, objects = _seed_render(db_session)
    s3 = FakeS3(objects)
    maker = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)

    def racing_renderer(**kwargs):
        other = maker()
        try:
            if race == "cancel":
                other.execute(
                    sa.update(Job)
                    .where(Job.id == job.id)
                    .values(status="CANCELLED", completed_at=sa.func.now())
                )
                other.commit()
            else:
                other.execute(
                    sa.update(Render)
                    .where(Render.id == render.id)
                    .values(lease_expires_at=sa.func.now() - sa.text("interval '1 second'"))
                )
                other.commit()
                principal = PrincipalContext(
                    organization_id=PORTFOLIO_ORGANIZATION_ID,
                    principal_id=PORTFOLIO_PRINCIPAL_ID,
                    principal_type="worker",
                    scopes=frozenset(),
                )
                claim_render(
                    other,
                    principal,
                    job.id,
                    worker_id="new-owner",
                    lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
                )
        finally:
            other.close()
        return _renderer({}, expected_approved=True)(**kwargs)

    outcome = _run(db_session, worker_env, tmp_path, s3, racing_renderer)
    db_session.expire_all()
    stored_job = db_session.get(Job, job.id)
    stored_render = db_session.get(Render, render.id)
    if race == "cancel":
        assert outcome == "cancelled"
        assert stored_job.status == "CANCELLED" and stored_render.state == "cancelled"
    else:
        assert outcome == "stale_render"
        assert stored_job.status == "EXPORTING"
        assert stored_render.state == "rendering" and stored_render.worker_id == "new-owner"
    assert not s3.puts
    assert (
        db_session.execute(
            sa.select(sa.func.count())
            .select_from(Deliverable)
            .where(Deliverable.render_id == render.id)
        ).scalar_one()
        == 0
    )
    assert (
        db_session.execute(
            sa.select(sa.func.count())
            .select_from(JobEvent)
            .where(
                JobEvent.job_id == job.id, JobEvent.event_type.in_({"job.completed", "job.failed"})
            )
        ).scalar_one()
        == 0
    )


def test_expired_render_is_reclaimed_with_new_fence_and_attempt_prefix(
    db_session, worker_env, tmp_path
):
    job, render, objects = _seed_render(db_session, expired=True)
    s3 = FakeS3(objects)
    assert _run(db_session, worker_env, tmp_path, s3, _renderer({}, expected_approved=True)) == (
        "success"
    )
    db_session.expire_all()
    stored_render = db_session.get(Render, render.id)
    assert (stored_render.state, stored_render.fence_token, stored_render.attempt_count) == (
        "completed",
        5,
        2,
    )
    keys = [row.object_key for row in db_session.execute(sa.select(Deliverable)).scalars()]
    assert len(keys) == 5 and all("/attempts/5/" in key for key in keys)
    assert db_session.get(Job, job.id).status == "COMPLETED"


def test_expired_render_fails_closed_before_an_unbounded_third_tts_attempt(
    db_session, worker_env, tmp_path
):
    job, render, objects = _seed_render(db_session, expired=True)
    render.attempt_count = TTS_BETA_MAX_RENDER_ATTEMPTS_PER_REVIEW
    db_session.commit()
    s3 = FakeS3(objects)

    def forbidden_renderer(**_kwargs):
        pytest.fail("attempt-exhausted render invoked the TTS-capable renderer")

    assert _run(db_session, worker_env, tmp_path, s3, forbidden_renderer) == "failed"
    db_session.expire_all()
    stored_job = db_session.get(Job, job.id)
    stored_render = db_session.get(Render, render.id)
    assert (stored_job.status, stored_job.error_code) == (
        "FAILED",
        "render_attempt_limit_exceeded",
    )
    assert (stored_render.state, stored_render.error_code, stored_render.attempt_count) == (
        "failed",
        "render_attempt_limit_exceeded",
        TTS_BETA_MAX_RENDER_ATTEMPTS_PER_REVIEW,
    )
    assert (
        db_session.scalar(
            sa.select(sa.func.count())
            .select_from(JobEvent)
            .where(
                JobEvent.job_id == job.id,
                JobEvent.event_type == "job.failed",
            )
        )
        == 1
    )
    assert s3.puts == []
