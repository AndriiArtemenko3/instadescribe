"""Worker integration: claim/dispatch/retry/DLQ flows against a run-scoped
database and run-owned LocalStack queues, with a controllable fake pipeline
(the REAL pipeline runs in the production-image g5 smoke)."""

import subprocess
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from app.domain.states import JobState
from app.models import Artifact, Job, Project
from instadescribe_contracts.queue import QueueMessage
from instadescribe_worker.claim import (
    claim_job as _claim_job,
)
from instadescribe_worker.claim import (
    exhaust_unclaimable as _exhaust_unclaimable,
)
from instadescribe_worker.claim import (
    guarded_update,
)
from instadescribe_worker.config import WorkerSettings, reset_worker_settings
from instadescribe_worker.consumer import reset_worker_caches, run_once

pytestmark = [
    pytest.mark.skipif(
        not __import__("os").environ.get("INSTADESCRIBE_TEST_DATABASE_URL"),
        reason="INSTADESCRIBE_TEST_DATABASE_URL not set (use `make g5-test`)",
    ),
    pytest.mark.skipif(
        not __import__("os").environ.get("INSTADESCRIBE_TEST_S3"),
        reason="INSTADESCRIBE_TEST_S3 not set (use `make g5-test`)",
    ),
]

SUCCESS_SCRIPT = """
import json, pathlib, sys
job_id = sys.argv[1]
pipeline = pathlib.Path(__file__).resolve().parent
out = pipeline.parent / "App" / "public" / "data" / job_id
out.mkdir(parents=True, exist_ok=True)
jd = pipeline / "jobs" / job_id
jd.joinpath("status.json").write_text(json.dumps(
    {"status": "processing", "progress": 55, "stage": "analyzing_frames",
     "chunks_done": 1, "chunks_total": 2, "error": None}))
payloads = {
    "scenes.json": [{"scene_id": "scene_1", "start": 0.0, "end": 2.0, "caption": "x"}],
    "entities.json": [],
    "audio_events.json": [],
    "ad_placement_gaps.json": [],
    "transcript.json": [],
}
for name, payload in payloads.items():
    (out / name).write_text(json.dumps(payload))
(out / "system_info.json").write_text(json.dumps({
    "video_id": job_id,
    "processing": {"model": "gpt-4.1", "image_detail": "low", "chunk_sizes": [60]},
    "tokens": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
    "status": "completed",
}))
jd.joinpath("result.json").write_text(json.dumps(
    {"data_path": f"/data/{job_id}", "video_file": f"/videos/{job_id}.mp4",
     "scene_count": 1, "tokens_used": 0}))
jd.joinpath("status.json").write_text(json.dumps(
    {"status": "ready", "progress": 100, "stage": "complete",
     "chunks_done": 2, "chunks_total": 2, "error": None}))
"""


def claim_job(*args, configured_max_attempts=3, **kwargs):
    """Test helper: fake-provider rows use the production policy of three."""
    return _claim_job(*args, configured_max_attempts=configured_max_attempts, **kwargs)


def exhaust_unclaimable(*args, configured_max_attempts=3, **kwargs):
    return _exhaust_unclaimable(*args, configured_max_attempts=configured_max_attempts, **kwargs)


@pytest.fixture(scope="session")
def tiny_clip(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("clip") / "tiny.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1:size=64x64:rate=10",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path


@pytest.fixture()
def fake_pipeline(tmp_path, monkeypatch):
    """A controllable pipeline source; tests may rewrite run_job.py."""
    source = tmp_path / "pipeline_source"
    source.mkdir()
    (source / "run_job.py").write_text(SUCCESS_SCRIPT)
    monkeypatch.setenv("INSTADESCRIBE_PIPELINE_SOURCE", str(source))
    monkeypatch.setenv("INSTADESCRIBE_RETRY_VISIBILITY_DELAY_SECS", "0")
    reset_worker_settings()
    reset_worker_caches()
    yield source
    reset_worker_settings()
    reset_worker_caches()


def _seed_job(db_session, aws, clip: Path, status=JobState.QUEUED, attempts=0, send=True):
    """Persist a verified job the way G3+G4 would have left it, upload the
    real object, and (optionally) enqueue its canonical message."""
    body = clip.read_bytes()
    project = Project(name="worker-int")
    db_session.add(project)
    db_session.flush()
    job_id = uuid.uuid4()
    key = f"uploads/{job_id}/source/tiny.mp4"
    put = aws["s3"].put_object(
        Bucket=aws["bucket"],
        Key=key,
        Body=body,
        ContentType="video/mp4",
        ServerSideEncryption="AES256",
    )
    message_id = uuid.uuid4()
    requested_at = datetime.now(UTC).replace(microsecond=0)
    job = Job(
        id=job_id,
        project_id=project.id,
        pipeline_revision="test",
        status=status.value,
        provider="fake",
        model="gpt-4.1",
        # The full stored-settings contract shape (G5.1 B4) — the worker
        # strictly validates this document before any source/model work.
        settings={
            "model": "gpt-4.1",
            "frame_quality": "low",
            "fps": 1.0,
            "chunk_size": 60,
            "audio_extraction": False,
            "custom_prompt": "",
            "language": None,
            "detail_level": 3,
            "preset_style": "documentary",
            "project_name": "worker-int",
            "duration_secs": 1.0,
        },
        input_object_key=key,
        input_content_type="video/mp4",
        input_size_bytes=len(body),
        duration_secs=1,
        source_etag=put["ETag"].strip('"'),
        source_version_id=put["VersionId"],  # C1: versioned bucket, pinned source
        upload_verified_at=requested_at,
        enqueue_message_id=message_id,
        enqueue_requested_at=requested_at,
        enqueue_attempt_count=1,
        attempt_count=attempts,
    )
    db_session.add(job)
    db_session.commit()
    message = QueueMessage(
        schema_version=1,
        message_id=message_id,
        task_type="ANALYZE",
        job_id=job_id,
        requested_at=requested_at,
    )
    if send:
        aws["sqs"].send_message(QueueUrl=aws["queue_url"], MessageBody=message.to_body())
    return job, message


def _queue_empty(aws) -> bool:
    # "Empty" means DELETED — a retained message may be in-flight (invisible)
    # under the visibility timeout, so count visible + not-visible.
    attrs = aws["sqs"].get_queue_attributes(
        QueueUrl=aws["queue_url"],
        AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
    )["Attributes"]
    total = int(attrs["ApproximateNumberOfMessages"]) + int(
        attrs["ApproximateNumberOfMessagesNotVisible"]
    )
    return total == 0


def test_golden_path_success_ordering(db_session, aws_resources, fake_pipeline, tiny_clip):
    job, message = _seed_job(db_session, aws_resources, tiny_clip)
    assert run_once() == "success"
    db_session.expire_all()
    fresh = db_session.get(Job, job.id)
    assert fresh.status == "READY_FOR_REVIEW"
    assert fresh.progress == 100 and fresh.stage == "complete"
    assert fresh.attempt_count == 1  # exactly one increment per claim
    assert fresh.completed_at is not None
    rows = {a.artifact_type: a for a in db_session.execute(sa.select(Artifact)).scalars()}
    expected = {
        "scenes_json",
        "entities_json",
        "audio_events_json",
        "ad_placement_gaps_json",
        "transcript_json",
        "system_info_json",
        "source_video",
    }
    assert expected <= set(rows)
    scenes = rows["scenes_json"]
    assert scenes.object_key == f"jobs/{job.id}/attempts/1/analysis/scenes.json"
    assert rows["source_video"].meta["version_id"] == job.source_version_id
    assert float(fresh.duration_secs) == pytest.approx(1.0, abs=0.3)  # measured, not hint
    obj = aws_resources["s3"].get_object(Bucket=aws_resources["bucket"], Key=scenes.object_key)
    body = obj["Body"].read()
    import hashlib

    assert hashlib.sha256(body).hexdigest() == scenes.checksum_sha256
    assert obj.get("ServerSideEncryption") == "AES256"
    assert rows["source_video"].object_key == job.input_object_key
    assert _queue_empty(aws_resources)  # deleted only after commit


def test_provider_mismatch_leaves_job_message_and_artifacts_untouched(
    db_session, aws_resources, fake_pipeline, tiny_clip, monkeypatch
):
    """A G12 worker must not claim an older fake-provider queue item."""
    job, _ = _seed_job(db_session, aws_resources, tiny_clip)
    monkeypatch.setenv("INSTADESCRIBE_PROVIDER", "openai")
    monkeypatch.setenv("INSTADESCRIBE_MAX_DURATION_SECS", "120")
    monkeypatch.setenv("INSTADESCRIBE_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-secret")
    settings = WorkerSettings()

    assert run_once(settings) == "provider_mismatch"

    db_session.expire_all()
    fresh = db_session.get(Job, job.id)
    assert fresh.status == JobState.QUEUED.value
    assert fresh.attempt_count == 0
    assert fresh.worker_id is None
    assert db_session.scalar(sa.select(sa.func.count()).select_from(Artifact)) == 0
    assert not _queue_empty(aws_resources)

    # The same provider predicate also fences a load-to-claim race.
    assert (
        claim_job(
            db_session,
            job.id,
            job.enqueue_message_id,
            job.enqueue_requested_at,
            provider="openai",
        )
        is None
    )
    db_session.expire_all()
    assert db_session.get(Job, job.id).attempt_count == 0


def test_openai_attempt_policy_mismatch_fails_before_provider_execution(
    db_session, aws_resources, fake_pipeline, tiny_clip, monkeypatch
):
    """A persisted OpenAI row with the fake-mode retry budget (3) cannot be
    claimed by the one-attempt OpenAI deployment or reach run_job.py."""
    marker = fake_pipeline / "provider-was-started"
    (fake_pipeline / "run_job.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n"
    )
    job, _ = _seed_job(db_session, aws_resources, tiny_clip)
    db_session.execute(
        sa.update(Job).where(Job.id == job.id).values(provider="openai", max_attempts=3)
    )
    db_session.commit()
    monkeypatch.setenv("INSTADESCRIBE_PROVIDER", "openai")
    monkeypatch.setenv("INSTADESCRIBE_MAX_DURATION_SECS", "120")
    monkeypatch.setenv("INSTADESCRIBE_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-a-real-secret")
    settings = WorkerSettings()

    assert run_once(settings) == "failed_attempt_policy"
    assert not marker.exists()
    db_session.expire_all()
    fresh = db_session.get(Job, job.id)
    assert (
        fresh.status,
        fresh.error_code,
        fresh.attempt_count,
        fresh.worker_id,
        fresh.lease_expires_at,
    ) == ("FAILED", "invalid_settings", 0, None, None)
    assert db_session.scalar(sa.select(sa.func.count()).select_from(Artifact)) == 0
    assert _queue_empty(aws_resources)


def test_persisted_version_survives_source_overwrite(
    db_session, aws_resources, fake_pipeline, tiny_clip
):
    """G5.1 C1 regression (reproduced failure #9): after processing, a NEW
    version written to the same upload key must not change what the pinned
    VersionId serves — the analyzed bytes stay byte-for-byte fetchable."""
    original = tiny_clip.read_bytes()
    job, _ = _seed_job(db_session, aws_resources, tiny_clip)
    assert run_once() == "success"
    # Overwrite the SAME key with different bytes (the reusable-POST hazard).
    aws_resources["s3"].put_object(
        Bucket=aws_resources["bucket"],
        Key=job.input_object_key,
        Body=b"ENTIRELY DIFFERENT BYTES",
        ContentType="video/mp4",
        ServerSideEncryption="AES256",
    )
    latest = (
        aws_resources["s3"]
        .get_object(Bucket=aws_resources["bucket"], Key=job.input_object_key)["Body"]
        .read()
    )
    assert latest == b"ENTIRELY DIFFERENT BYTES"  # "latest" is now video B
    pinned = (
        aws_resources["s3"]
        .get_object(
            Bucket=aws_resources["bucket"],
            Key=job.input_object_key,
            VersionId=job.source_version_id,
        )["Body"]
        .read()
    )
    assert pinned == original  # the processed source remains video A, exactly
    db_session.expire_all()
    row = db_session.execute(
        sa.select(Artifact).where(Artifact.artifact_type == "source_video")
    ).scalar_one()
    assert row.meta["version_id"] == job.source_version_id


def test_unpinned_source_fails_deterministically_before_any_download(
    db_session, aws_resources, fake_pipeline, tiny_clip
):
    """G5.1 C1: a job without a pinned VersionId must never process 'latest'."""
    job, _ = _seed_job(db_session, aws_resources, tiny_clip)
    db_session.execute(sa.update(Job).where(Job.id == job.id).values(source_version_id=None))
    db_session.commit()
    assert run_once() == "failed_deterministic"
    db_session.expire_all()
    fresh = db_session.get(Job, job.id)
    assert (fresh.status, fresh.error_code) == ("FAILED", "source_identity_mismatch")


def test_measured_duration_replaces_the_declared_hint(
    db_session, aws_resources, fake_pipeline, tiny_clip
):
    """G5.1 C3: a wrong-but-in-bounds declared duration is replaced by the
    ffprobe measurement before model work."""
    job, _ = _seed_job(db_session, aws_resources, tiny_clip)
    db_session.execute(sa.update(Job).where(Job.id == job.id).values(duration_secs=250))
    db_session.commit()
    assert run_once() == "success"
    db_session.expire_all()
    fresh = db_session.get(Job, job.id)
    assert float(fresh.duration_secs) == pytest.approx(1.0, abs=0.3)


def test_actual_over_limit_video_is_rejected_before_model_work(
    db_session, aws_resources, fake_pipeline, tmp_path, monkeypatch
):
    """G5.1 C3: a short DECLARED duration cannot bypass the cap; the actual
    over-limit video fails deterministically with no pipeline run."""
    long_clip = tmp_path / "long.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=3:size=64x64:rate=10",
            "-pix_fmt",
            "yuv420p",
            str(long_clip),
        ],
        check=True,
    )
    monkeypatch.setenv("INSTADESCRIBE_MAX_DURATION_SECS", "1")
    reset_worker_settings()
    job, _ = _seed_job(db_session, aws_resources, long_clip)  # declared 1s, actual 3s
    assert run_once() == "failed_deterministic"
    db_session.expire_all()
    fresh = db_session.get(Job, job.id)
    assert (fresh.status, fresh.error_code) == ("FAILED", "invalid_media")
    count = db_session.execute(sa.select(sa.func.count()).select_from(Artifact)).scalar_one()
    assert count == 0  # no model/pipeline work produced anything
    reset_worker_settings()


def test_terminal_duplicate_is_acknowledged_without_rerun(
    db_session, aws_resources, fake_pipeline, tiny_clip
):
    job, message = _seed_job(db_session, aws_resources, tiny_clip)
    assert run_once() == "success"
    aws_resources["sqs"].send_message(
        QueueUrl=aws_resources["queue_url"], MessageBody=message.to_body()
    )
    assert run_once() == "duplicate_success"
    assert _queue_empty(aws_resources)
    # No duplicate artifacts: unique (job_id, type) upserts stay singular.
    count = db_session.execute(sa.select(sa.func.count()).select_from(Artifact)).scalar_one()
    assert count == 7


def test_stale_identity_and_processing_rows_are_never_mutated(
    db_session, aws_resources, fake_pipeline, tiny_clip, monkeypatch
):
    job, message = _seed_job(db_session, aws_resources, tiny_clip, send=False)
    forged = QueueMessage(
        schema_version=1,
        message_id=uuid.uuid4(),
        task_type="ANALYZE",
        job_id=job.id,
        requested_at=message.requested_at,
    )
    aws_resources["sqs"].send_message(
        QueueUrl=aws_resources["queue_url"], MessageBody=forged.to_body()
    )
    assert run_once() == "stale"
    db_session.expire_all()
    assert db_session.get(Job, job.id).status == "QUEUED"  # untouched

    # Another worker's PROCESSING row: leave the true message unacknowledged.
    db_session.execute(
        sa.update(Job)
        .where(Job.id == job.id)
        .values(
            status="PROCESSING",
            worker_id="other",
            lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    )
    db_session.commit()
    aws_resources["sqs"].send_message(
        QueueUrl=aws_resources["queue_url"], MessageBody=message.to_body()
    )
    from instadescribe_worker import consumer

    visibility_calls = []
    real_change_visibility = consumer._sqs().change_message_visibility

    def record_visibility(**kwargs):
        visibility_calls.append(kwargs)
        return real_change_visibility(**kwargs)

    monkeypatch.setattr(consumer._sqs(), "change_message_visibility", record_visibility)
    assert run_once() == "in_progress"
    db_session.expire_all()
    fresh = db_session.get(Job, job.id)
    assert (fresh.status, fresh.worker_id) == ("PROCESSING", "other")
    assert len(visibility_calls) == 1
    assert visibility_calls[0]["QueueUrl"] == aws_resources["queue_url"]
    assert visibility_calls[0]["VisibilityTimeout"] == 300
    assert isinstance(visibility_calls[0]["ReceiptHandle"], str)
    assert not _queue_empty(aws_resources)  # message left for redrive/lease work


def test_in_progress_visibility_failure_is_sanitized_and_non_mutating(
    db_session, aws_resources, fake_pipeline, tiny_clip, monkeypatch, capsys
):
    from instadescribe_worker import consumer

    job, message = _seed_job(db_session, aws_resources, tiny_clip)
    db_session.execute(
        sa.update(Job)
        .where(Job.id == job.id)
        .values(
            status="PROCESSING",
            worker_id="other-owner",
            lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    )
    db_session.commit()

    def unavailable(**kwargs):
        raise RuntimeError("secret queue endpoint and credential text")

    monkeypatch.setattr(consumer._sqs(), "change_message_visibility", unavailable)
    capsys.readouterr()
    assert run_once() == "in_progress"
    logs = capsys.readouterr().out
    assert "in_progress_visibility_failed" in logs
    assert "secret" not in logs and "credential" not in logs
    db_session.expire_all()
    fresh = db_session.get(Job, job.id)
    assert (fresh.status, fresh.worker_id, fresh.attempt_count) == (
        "PROCESSING",
        "other-owner",
        0,
    )


def test_forged_identity_against_terminal_job_is_never_acknowledged(
    db_session, aws_resources, fake_pipeline, tiny_clip
):
    """G5.1 A2 regression (reproduced failure #1): a forged logical message
    naming a READY_FOR_REVIEW job must NOT be deleted — identity is validated
    before every terminal policy."""
    job, message = _seed_job(db_session, aws_resources, tiny_clip, send=False)
    db_session.execute(sa.update(Job).where(Job.id == job.id).values(status="READY_FOR_REVIEW"))
    db_session.commit()
    for forged in (
        QueueMessage(
            schema_version=1,
            message_id=uuid.uuid4(),  # wrong messageId
            task_type="ANALYZE",
            job_id=job.id,
            requested_at=message.requested_at,
        ),
        QueueMessage(
            schema_version=1,
            message_id=message.message_id,
            task_type="ANALYZE",
            job_id=job.id,
            requested_at=message.requested_at.replace(year=2001),  # wrong requestedAt
        ),
    ):
        aws_resources["sqs"].send_message(
            QueueUrl=aws_resources["queue_url"], MessageBody=forged.to_body()
        )
        assert run_once() == "stale"
        assert not _queue_empty(aws_resources)  # retained for redrive
        db_session.expire_all()
        fresh = db_session.get(Job, job.id)
        assert fresh.status == "READY_FOR_REVIEW" and fresh.error_code is None
        # Drain the retained forged message so the next case starts clean.
        _drain_queue(aws_resources)


def _drain_queue(aws) -> None:
    while True:
        got = (
            aws["sqs"]
            .receive_message(
                QueueUrl=aws["queue_url"],
                MaxNumberOfMessages=10,
                WaitTimeSeconds=0,
                VisibilityTimeout=0,
            )
            .get("Messages", [])
        )
        if not got:
            if _queue_empty(aws):
                return
            time.sleep(0.5)
            continue
        for m in got:
            aws["sqs"].delete_message(QueueUrl=aws["queue_url"], ReceiptHandle=m["ReceiptHandle"])


def test_terminal_duplicate_acknowledgement_matrix(
    db_session, aws_resources, fake_pipeline, tiny_clip
):
    """G5.1 A2: canonical duplicates — CANCELLED acked (documented policy),
    non-retryable FAILED codes acked, retryable/unknown/exhausted codes left
    for DLQ/repair."""
    cases = [
        ("CANCELLED", None, "duplicate_cancelled", True),
        ("FAILED", "invalid_media", "duplicate_failed", True),
        ("FAILED", "invalid_settings", "duplicate_failed", True),
        ("FAILED", "source_identity_mismatch", "duplicate_failed", True),
        ("FAILED", "pipeline_revision_mismatch", "duplicate_failed", True),
        ("FAILED", "pipeline_failed", "failed_await_repair", False),
        ("FAILED", "retry_exhausted", "exhausted_await_dlq", False),
        ("FAILED", "totally_unknown_code", "failed_await_repair", False),
    ]
    for status, error_code, expected, acked in cases:
        job, message = _seed_job(db_session, aws_resources, tiny_clip)
        db_session.execute(
            sa.update(Job).where(Job.id == job.id).values(status=status, error_code=error_code)
        )
        db_session.commit()
        assert run_once() == expected, (status, error_code)
        assert _queue_empty(aws_resources) is acked, (status, error_code)
        db_session.expire_all()
        fresh = db_session.get(Job, job.id)
        assert (fresh.status, fresh.error_code) == (status, error_code)  # never mutated
        _drain_queue(aws_resources)


def test_ack_failure_after_success_is_ack_pending_not_processing_failure(
    db_session, aws_resources, fake_pipeline, tiny_clip, monkeypatch
):
    """G5.1 A4 regression (reproduced failure #8): a successful commit whose
    SQS delete fails is success_ack_pending — the job stays READY_FOR_REVIEW
    with no failure transition, and canonical redelivery acknowledges."""
    from instadescribe_worker import consumer

    job, message = _seed_job(db_session, aws_resources, tiny_clip)
    client = consumer._sqs()
    real_delete = client.delete_message

    def _refuse(**kwargs):
        raise RuntimeError("simulated queue outage http://secret-queue-host/token#abc")

    monkeypatch.setattr(client, "delete_message", _refuse)
    assert run_once() == "success_ack_pending"
    db_session.expire_all()
    fresh = db_session.get(Job, job.id)
    assert fresh.status == "READY_FOR_REVIEW"
    assert fresh.error_code is None  # no failure transition, no requeue
    assert not _queue_empty(aws_resources)  # message retained for re-ack

    monkeypatch.setattr(client, "delete_message", real_delete)
    # Canonical redelivery acknowledges without any pipeline/artifact rerun.
    aws_resources["sqs"].send_message(
        QueueUrl=aws_resources["queue_url"], MessageBody=message.to_body()
    )
    assert run_once() == "duplicate_success"
    count = db_session.execute(sa.select(sa.func.count()).select_from(Artifact)).scalar_one()
    assert count == 7


def test_deterministic_failure_ack_failure_is_ack_pending(
    db_session, aws_resources, fake_pipeline, tmp_path, monkeypatch
):
    """G5.1 A4: the FAILED commit is durable even when its acknowledgement
    delete fails; no visibility shortening, no state change."""
    from instadescribe_worker import consumer

    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"this is not video content" * 100)
    job, _ = _seed_job(db_session, aws_resources, junk)
    client = consumer._sqs()

    def _refuse(**kwargs):
        raise RuntimeError("simulated queue outage")

    monkeypatch.setattr(client, "delete_message", _refuse)
    assert run_once() == "failed_deterministic_ack_pending"
    db_session.expire_all()
    fresh = db_session.get(Job, job.id)
    assert (fresh.status, fresh.error_code) == ("FAILED", "invalid_media")
    assert not _queue_empty(aws_resources)


def test_stolen_ownership_during_failure_never_deletes_or_claims_failure(
    db_session, aws_resources, fake_pipeline, tiny_clip, tmp_path
):
    """G5.1 A3 regression (reproduced failure #2): when the guarded
    transition returns False (ownership stolen mid-run), the worker reports a
    stale-owner outcome, deletes nothing and writes nothing."""
    control = tmp_path / "control"
    control.mkdir()
    flag = control / "child-started"
    steal_then_fail = (
        "import pathlib, sys, time\n"
        f"pathlib.Path({str(flag)!r}).write_text('x')\n"
        f"while not pathlib.Path({str(control / 'proceed')!r}).exists(): time.sleep(0.05)\n"
        "sys.exit(7)\n"
    )
    (fake_pipeline / "run_job.py").write_text(steal_then_fail)
    job, _ = _seed_job(db_session, aws_resources, tiny_clip)

    def _steal_when_started():
        deadline = time.monotonic() + 30
        while not flag.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        db_session.execute(
            sa.update(Job).where(Job.id == job.id).values(worker_id="stolen-by-other-claim")
        )
        db_session.commit()
        (control / "proceed").write_text("x")

    thief = threading.Thread(target=_steal_when_started)
    thief.start()
    outcome = run_once()
    thief.join(timeout=30)
    assert outcome == "stale_owner"
    db_session.expire_all()
    fresh = db_session.get(Job, job.id)
    assert (fresh.status, fresh.worker_id) == ("PROCESSING", "stolen-by-other-claim")
    assert fresh.error_code is None  # the stale attempt claimed no failure
    assert not _queue_empty(aws_resources)  # and deleted nothing


def test_pre_claim_db_failure_is_sanitized_infra_error(
    db_session, aws_resources, fake_pipeline, tiny_clip, monkeypatch, capsys
):
    """G5.1 B3 regression (reproduced failure #6): a pre-claim database
    outage surfaces as a sanitized infra_error — no DSN, no traceback, and
    the message is never deleted."""
    from instadescribe_worker.config import reset_worker_settings
    from instadescribe_worker.consumer import reset_worker_caches
    from instadescribe_worker.db import reset_db_caches

    _job, _message = _seed_job(db_session, aws_resources, tiny_clip)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://worker:sekret-pw@127.0.0.1:59996/nope")
    reset_worker_settings()
    reset_db_caches()
    capsys.readouterr()  # clear
    assert run_once() == "infra_error"
    captured = capsys.readouterr()
    for stream in (captured.out, captured.err):
        assert "sekret-pw" not in stream
        assert "Traceback" not in stream
        assert "59996" not in stream
    assert not _queue_empty(aws_resources)
    reset_worker_settings()
    reset_db_caches()
    reset_worker_caches()


def test_retryable_failure_requeues_then_redelivery_succeeds(
    db_session, aws_resources, fake_pipeline, tiny_clip, tmp_path
):
    control = tmp_path / "control"
    control.mkdir()
    fail_once = (
        "import pathlib, sys\n"
        f"flag = pathlib.Path({str(control / 'failed-once')!r})\n"
        "if not flag.exists():\n"
        "    flag.write_text('x')\n"
        "    sys.exit(7)\n" + SUCCESS_SCRIPT
    )
    (fake_pipeline / "run_job.py").write_text(fail_once)
    job, _ = _seed_job(db_session, aws_resources, tiny_clip)
    assert run_once() == "retry_requeued"
    db_session.expire_all()
    fresh = db_session.get(Job, job.id)
    assert fresh.status == "QUEUED"
    assert fresh.worker_id is None
    assert fresh.error_code == "pipeline_failed"
    assert fresh.attempt_count == 1
    assert len(fresh.error_message) <= 200  # bounded, classified
    # Message was NOT deleted; visibility delay 0 → redeliver now.
    assert run_once() == "success"
    db_session.expire_all()
    fresh = db_session.get(Job, job.id)
    assert fresh.status == "READY_FOR_REVIEW"
    assert fresh.attempt_count == 2
    # D2: the winning rows point at the WINNING attempt's scoped prefix.
    rows = {a.artifact_type: a for a in db_session.execute(sa.select(Artifact)).scalars()}
    assert rows["scenes_json"].object_key == f"jobs/{job.id}/attempts/2/analysis/scenes.json"


def test_deterministic_failure_fails_and_deletes(
    db_session, aws_resources, fake_pipeline, tmp_path
):
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"this is not video content" * 100)
    job, _ = _seed_job(db_session, aws_resources, junk)  # ffprobe will reject
    assert run_once() == "failed_deterministic"
    db_session.expire_all()
    fresh = db_session.get(Job, job.id)
    assert fresh.status == "FAILED"
    assert fresh.error_code == "invalid_media"
    assert fresh.worker_id is None
    assert _queue_empty(aws_resources)  # safe to acknowledge

    # A duplicate of a deterministic failure is acknowledged too.
    message = QueueMessage(
        schema_version=1,
        message_id=fresh.enqueue_message_id,
        task_type="ANALYZE",
        job_id=fresh.id,
        requested_at=fresh.enqueue_requested_at,
    )
    aws_resources["sqs"].send_message(
        QueueUrl=aws_resources["queue_url"], MessageBody=message.to_body()
    )
    assert run_once() == "duplicate_failed"
    assert _queue_empty(aws_resources)


def test_exhausted_attempts_fail_durably_and_leave_message_for_dlq(
    db_session, aws_resources, fake_pipeline, tiny_clip
):
    job, message = _seed_job(db_session, aws_resources, tiny_clip, attempts=3)
    assert run_once() == "exhausted"
    db_session.expire_all()
    fresh = db_session.get(Job, job.id)
    assert fresh.status == "FAILED"
    assert fresh.error_code == "retry_exhausted"
    assert not _queue_empty(aws_resources)  # left for the redrive policy
    # The untouched message stays in-flight until the 5s test visibility
    # window lapses; a REAL SQS redelivery must still not be acknowledged.
    outcome = "empty"
    deadline = time.monotonic() + 20
    while outcome == "empty" and time.monotonic() < deadline:
        time.sleep(0.5)
        outcome = run_once()
    assert outcome == "exhausted_await_dlq"
    assert not _queue_empty(aws_resources)


def test_hostile_poison_reaches_dlq_with_zero_mutation_and_no_text_leak(
    db_session, aws_resources, fake_pipeline, capsys
):
    """G5.1 D3 #10: hostile malformed AND oversized bodies pass through the
    REAL consumer to actual DLQ arrival — zero job mutation, and none of the
    hostile text ever reaches the logs."""
    import os
    import secrets

    from queue_support import make_queue_pair

    hostile = (
        '{"schemaVersion": true, "junk": "SECRET-MARKER-A '
        'https://signed.example/x?sig=HOSTILE-SIG AKIAHOSTILEKEY"}'
    )
    oversized = '{"schemaVersion": 1, "pad": "' + "H0STILE-PAD" * 2000 + '"}'  # > 8 KiB

    # Run-owned short-visibility queue so redrive happens quickly.
    base = f"instadescribe-worker-poison-{os.getpid()}-{secrets.token_hex(3)}"
    queue_url, dlq_url = make_queue_pair(aws_resources["sqs"], base, visibility="0")
    try:
        os.environ["INSTADESCRIBE_WORK_QUEUE_URL"] = queue_url
        reset_worker_settings()
        reset_worker_caches()
        for body in (hostile, oversized):
            aws_resources["sqs"].send_message(QueueUrl=queue_url, MessageBody=body)
        capsys.readouterr()  # start clean
        outcomes = [run_once() for _ in range(8)]
        assert outcomes.count("poison") >= 3  # each receive refuses to delete
        deadline = time.monotonic() + 30
        dlq_bodies: list[str] = []
        while len(dlq_bodies) < 2 and time.monotonic() < deadline:
            run_once()  # receives trigger the redrive policy
            got = (
                aws_resources["sqs"]
                .receive_message(QueueUrl=dlq_url, MaxNumberOfMessages=10, WaitTimeSeconds=1)
                .get("Messages", [])
            )
            dlq_bodies.extend(m["Body"] for m in got)
        assert len(dlq_bodies) == 2, "both hostile messages must reach the DLQ via redrive"
        assert sorted(dlq_bodies) == sorted([hostile, oversized])  # bytes intact for repair
        jobs = db_session.execute(sa.select(sa.func.count()).select_from(Job)).scalar_one()
        assert jobs == 0  # no job was created or mutated
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        for leak in ("SECRET-MARKER-A", "HOSTILE-SIG", "AKIAHOSTILEKEY", "H0STILE-PAD"):
            assert leak not in combined  # category-only logging, never the body
    finally:
        for url in (queue_url, dlq_url):
            try:
                aws_resources["sqs"].delete_queue(QueueUrl=url)
            except Exception:
                pass
        os.environ.pop("INSTADESCRIBE_WORK_QUEUE_URL", None)
        reset_worker_settings()
        reset_worker_caches()


def test_three_real_retryable_failures_reach_durable_exhaustion_and_the_dlq(
    db_session, aws_resources, fake_pipeline, tiny_clip
):
    """G5.1 D3 #9: three REAL processing failures from attempt 1 through
    max_attempts, a durable FAILED/retry_exhausted row, and the retained
    message's ACTUAL arrival in the run-owned DLQ."""
    (fake_pipeline / "run_job.py").write_text("import sys\nsys.exit(7)\n")  # always fails
    job, message = _seed_job(db_session, aws_resources, tiny_clip)

    assert run_once() == "retry_requeued"  # attempt 1 (visibility reset to 0)
    assert run_once() == "retry_requeued"  # attempt 2
    db_session.expire_all()
    assert db_session.get(Job, job.id).attempt_count == 2
    assert run_once() == "failed_exhausted"  # attempt 3: exhaustion committed
    db_session.expire_all()
    fresh = db_session.get(Job, job.id)
    assert (fresh.status, fresh.error_code) == ("FAILED", "retry_exhausted")
    assert fresh.attempt_count == 3
    assert not _queue_empty(aws_resources)  # left for the redrive policy

    # The retained message must ACTUALLY arrive in the DLQ: after the third
    # receive, the next visible receive crosses maxReceiveCount=3.
    deadline = time.monotonic() + 45
    dlq_bodies: list[dict] = []
    while not dlq_bodies and time.monotonic() < deadline:
        run_once()  # renewed receives trigger the redrive transfer
        got = (
            aws_resources["sqs"]
            .receive_message(
                QueueUrl=aws_resources["dlq_url"], MaxNumberOfMessages=10, WaitTimeSeconds=1
            )
            .get("Messages", [])
        )
        dlq_bodies.extend(__import__("json").loads(m["Body"]) for m in got)
        if not dlq_bodies:
            time.sleep(1)
    assert dlq_bodies, "exhausted message must reach the DLQ"
    assert dlq_bodies[0]["messageId"] == str(message.message_id)
    assert dlq_bodies[0]["jobId"] == str(job.id)
    db_session.expire_all()
    fresh = db_session.get(Job, job.id)
    assert (fresh.status, fresh.error_code) == ("FAILED", "retry_exhausted")  # unchanged


def test_s3_upload_failure_before_finalization_is_retryable_without_rows(
    db_session, aws_resources, fake_pipeline, tiny_clip, monkeypatch
):
    """G5.1 D3 #5: an S3 artifact-upload failure before the DB transaction
    requeues retryably — no artifact rows, no delete, no success claim."""
    from instadescribe_worker import consumer

    job, _ = _seed_job(db_session, aws_resources, tiny_clip)
    client = consumer._s3()
    real_put = client.put_object

    def _refuse(**kwargs):
        if kwargs.get("Key", "").startswith("jobs/"):
            raise RuntimeError("simulated S3 outage http://secret-endpoint")
        return real_put(**kwargs)

    monkeypatch.setattr(client, "put_object", _refuse)
    assert run_once() == "retry_requeued"
    db_session.expire_all()
    fresh = db_session.get(Job, job.id)
    assert fresh.status == "QUEUED"
    assert fresh.error_code == "internal_error"
    count = db_session.execute(sa.select(sa.func.count()).select_from(Artifact)).scalar_one()
    assert count == 0  # nothing persisted before the failure
    assert not _queue_empty(aws_resources)


def test_db_failure_after_uploads_before_commit_never_deletes(
    db_session, aws_resources, fake_pipeline, tiny_clip, monkeypatch
):
    """G5.1 D3 #6: a database failure AFTER the S3 uploads but before the
    success commit leaves the message undeleted and claims no outcome."""
    from instadescribe_worker import artifacts as artifacts_mod

    job, _ = _seed_job(db_session, aws_resources, tiny_clip)
    real_finalize = artifacts_mod.upload_and_finalize

    def _uploads_then_db_down(session, s3, bucket, jb, token, uploads, sha):
        for artifact in uploads:  # the real S3 writes happen first
            s3.put_object(
                Bucket=bucket,
                Key=artifact.object_key,
                Body=artifact.local_path.read_bytes(),
                ContentType=artifact.content_type,
                ServerSideEncryption="AES256",
            )
        raise RuntimeError("database gone after uploads")

    monkeypatch.setattr(artifacts_mod, "upload_and_finalize", _uploads_then_db_down)
    outcome = run_once()
    monkeypatch.setattr(artifacts_mod, "upload_and_finalize", real_finalize)
    # The DB was reachable for the failure handler, so the attempt requeues
    # retryably; the essential invariants: message retained, no success, no rows.
    assert outcome == "retry_requeued"
    db_session.expire_all()
    fresh = db_session.get(Job, job.id)
    assert fresh.status == "QUEUED"
    count = db_session.execute(sa.select(sa.func.count()).select_from(Artifact)).scalar_one()
    assert count == 0
    assert not _queue_empty(aws_resources)


def test_stale_finalize_full_flow_keeps_winner_and_message(
    db_session, aws_resources, fake_pipeline, tiny_clip, tmp_path
):
    """G5.1 D3 #7 plus v0.2 heartbeat fencing: ownership stolen during the
    pipeline is detected before finalization; the stale attempt deletes
    nothing and cannot overwrite the future winner's rows."""
    control = tmp_path / "control"
    control.mkdir()
    flag = control / "child-started"
    gated_success = (
        "import pathlib, time\n"
        f"pathlib.Path({str(flag)!r}).write_text('x')\n"
        f"while not pathlib.Path({str(control / 'proceed')!r}).exists(): time.sleep(0.05)\n"
    ) + SUCCESS_SCRIPT
    (fake_pipeline / "run_job.py").write_text(gated_success)
    job, _ = _seed_job(db_session, aws_resources, tiny_clip)

    def _steal_when_started():
        deadline = time.monotonic() + 30
        while not flag.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        db_session.execute(
            sa.update(Job).where(Job.id == job.id).values(worker_id="the-real-winner")
        )
        db_session.commit()
        (control / "proceed").write_text("x")

    thief = threading.Thread(target=_steal_when_started)
    thief.start()
    outcome = run_once()
    thief.join(timeout=30)
    assert outcome == "stale_owner"
    db_session.expire_all()
    fresh = db_session.get(Job, job.id)
    assert (fresh.status, fresh.worker_id) == ("PROCESSING", "the-real-winner")
    count = db_session.execute(sa.select(sa.func.count()).select_from(Artifact)).scalar_one()
    assert count == 0  # the stale transaction rolled back all rows
    assert not _queue_empty(aws_resources)


def test_failure_handler_db_outage_never_deletes(
    db_session, aws_resources, fake_pipeline, tmp_path, monkeypatch
):
    """G5.1 A3/D3 #6: when even the failure transition cannot commit (DB
    gone), the worker claims nothing and the message survives."""
    from instadescribe_worker import claim as claim_mod

    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"this is not video content" * 100)
    job, _ = _seed_job(db_session, aws_resources, junk)

    def _db_gone(*args, **kwargs):
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(claim_mod, "guarded_transition", _db_gone)
    assert run_once() == "db_unavailable"
    db_session.expire_all()
    fresh = db_session.get(Job, job.id)
    assert fresh.status == "PROCESSING"  # no transition was committed
    assert not _queue_empty(aws_resources)  # and nothing was acknowledged


def test_artifact_row_upsert_is_idempotent_per_type(
    db_session, aws_resources, fake_pipeline, tiny_clip
):
    """G5.1 D3 #4: (job_id, artifact_type) upserts stay singular and the
    winning attempt's keys replace prior rows atomically."""
    from instadescribe_worker.artifacts import upload_and_finalize, validate_outputs
    from instadescribe_worker.workspace import build_workspace

    job, message = _seed_job(db_session, aws_resources, tiny_clip, send=False)
    settings_dir = build_workspace(None, str(fake_pipeline), str(job.id))
    try:
        # Synthesize a completed output tree for attempts 1 and 2.
        data_dir = settings_dir.data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        payloads = {
            "scenes.json": [{"scene_id": "scene_1", "start": 0.0, "end": 2.0}],
            "entities.json": [],
            "audio_events.json": [],
            "ad_placement_gaps.json": [],
            "transcript.json": [],
        }
        import json as _json

        for name, payload in payloads.items():
            (data_dir / name).write_text(_json.dumps(payload))
        (data_dir / "system_info.json").write_text(
            _json.dumps(
                {
                    "video_id": str(job.id),
                    "processing": {
                        "model": "gpt-4.1",
                        "image_detail": "low",
                        "chunk_sizes": [60],
                    },
                    "tokens": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                    "status": "completed",
                }
            )
        )
        settings_dir.job_dir.joinpath("result.json").write_text(
            _json.dumps({"data_path": f"/data/{job.id}", "scene_count": 1})
        )

        claimed = claim_job(db_session, job.id, message.message_id, message.requested_at)
        token = claimed.worker_id
        uploads1 = validate_outputs(settings_dir, str(job.id), 1)
        assert upload_and_finalize(
            db_session,
            aws_resources["s3"],
            aws_resources["bucket"],
            claimed,
            token,
            uploads1,
            "0" * 64,
        )
        rows1 = {
            a.artifact_type: a.object_key for a in db_session.execute(sa.select(Artifact)).scalars()
        }
        assert rows1["scenes_json"].startswith(f"jobs/{job.id}/attempts/1/")

        # A later winning attempt re-upserts the SAME types with its keys.
        db_session.execute(
            sa.update(Job).where(Job.id == job.id).values(status="QUEUED", attempt_count=1)
        )
        db_session.commit()
        claimed2 = claim_job(db_session, job.id, message.message_id, message.requested_at)
        uploads2 = validate_outputs(settings_dir, str(job.id), 2)
        assert upload_and_finalize(
            db_session,
            aws_resources["s3"],
            aws_resources["bucket"],
            claimed2,
            claimed2.worker_id,
            uploads2,
            "0" * 64,
        )
        rows2 = list(db_session.execute(sa.select(Artifact)).scalars())
        by_type = {a.artifact_type: a.object_key for a in rows2}
        assert len(rows2) == len(by_type)  # still singular per type
        assert by_type["scenes_json"].startswith(f"jobs/{job.id}/attempts/2/")
    finally:
        settings_dir.cleanup()


def test_api_finalizer_race_worker_claim_is_monotonic(
    db_session, aws_resources, fake_pipeline, tiny_clip
):
    """C4 #18: the worker claims UPLOAD_COMPLETE (the G4 send-success/
    finalize-failure recovery), and the late API finalizer cannot regress it."""
    from app.repositories.jobs import transition_job

    job, _ = _seed_job(db_session, aws_resources, tiny_clip, status=JobState.UPLOAD_COMPLETE)
    assert run_once() == "success"
    db_session.expire_all()
    assert db_session.get(Job, job.id).status == "READY_FOR_REVIEW"
    # The late API-side UPLOAD_COMPLETE -> QUEUED conditional is a strict no-op.
    late = transition_job(db_session, job.id, JobState.UPLOAD_COMPLETE, JobState.QUEUED)
    db_session.commit()
    assert late is None
    db_session.expire_all()
    assert db_session.get(Job, job.id).status == "READY_FOR_REVIEW"


def test_concurrent_claims_have_exactly_one_winner(
    db_session, aws_resources, tiny_clip, worker_env
):
    from instadescribe_worker.db import get_sessionmaker

    job, message = _seed_job(db_session, aws_resources, tiny_clip, send=False)
    barrier = threading.Barrier(2)
    winners: list = [None, None]

    def attempt(i: int):
        session = get_sessionmaker()()
        try:
            barrier.wait()
            winners[i] = claim_job(session, job.id, message.message_id, message.requested_at)
        finally:
            session.close()

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    claimed = [w for w in winners if w is not None]
    assert len(claimed) == 1  # exactly one winner under a real barrier race
    db_session.expire_all()
    fresh = db_session.get(Job, job.id)
    assert fresh.attempt_count == 1
    assert fresh.worker_id == claimed[0].worker_id


def test_two_consumers_loading_queued_bound_the_losing_receipt_visibility(
    db_session, aws_resources, fake_pipeline, tiny_clip, monkeypatch
):
    """Both consumers cross the claim barrier only after loading QUEUED.

    One atomic claim wins; the loser must not leave its distinct duplicate
    receipt at the queue's deliberately long default visibility horizon.
    """
    from instadescribe_worker import consumer

    job, message = _seed_job(db_session, aws_resources, tiny_clip)
    aws_resources["sqs"].set_queue_attributes(
        QueueUrl=aws_resources["queue_url"], Attributes={"VisibilityTimeout": "1800"}
    )
    aws_resources["sqs"].send_message(
        QueueUrl=aws_resources["queue_url"], MessageBody=message.to_body()
    )

    real_claim = consumer.claim_mod.claim_job
    both_loaded_queued = threading.Barrier(2)

    def synchronized_claim(*args, **kwargs):
        both_loaded_queued.wait(timeout=30)
        return real_claim(*args, **kwargs)

    visibility_calls = []
    real_change_visibility = consumer._sqs().change_message_visibility

    def record_visibility(**kwargs):
        visibility_calls.append(kwargs)
        return real_change_visibility(**kwargs)

    monkeypatch.setattr(consumer.claim_mod, "claim_job", synchronized_claim)
    monkeypatch.setattr(consumer._sqs(), "change_message_visibility", record_visibility)
    outcomes = [None, None]
    errors = []

    def consume(index: int):
        try:
            outcomes[index] = run_once()
        except Exception as exc:  # surface thread errors below
            errors.append(exc)

    threads = [threading.Thread(target=consume, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors and all(not thread.is_alive() for thread in threads)
    assert sorted(outcomes) == ["lost_claim", "success"]
    # Winner heartbeats and the loser's race normalization use two distinct
    # receipts, and every call replaces the 1800s queue default with 300s.
    receipts = {call["ReceiptHandle"] for call in visibility_calls}
    assert len(receipts) == 2
    assert {call["VisibilityTimeout"] for call in visibility_calls} == {300}
    db_session.expire_all()
    fresh = db_session.get(Job, job.id)
    assert (fresh.status, fresh.attempt_count) == ("READY_FOR_REVIEW", 1)
    assert db_session.scalar(sa.select(sa.func.count()).select_from(Artifact)) == 7
    assert not _queue_empty(aws_resources)  # losing duplicate remains for bounded re-evaluation


def test_fresh_processing_lease_refuses_a_second_claim(
    db_session, aws_resources, tiny_clip, worker_env
):
    job, message = _seed_job(db_session, aws_resources, tiny_clip, send=False)
    first = claim_job(
        db_session,
        job.id,
        message.message_id,
        message.requested_at,
        lease_duration_secs=300,
    )
    assert first is not None and first.lease_expires_at is not None
    assert (
        claim_job(
            db_session,
            job.id,
            message.message_id,
            message.requested_at,
            lease_duration_secs=300,
        )
        is None
    )
    db_session.expire_all()
    fresh = db_session.get(Job, job.id)
    assert (fresh.status, fresh.worker_id, fresh.attempt_count) == (
        "PROCESSING",
        first.worker_id,
        1,
    )


def test_expired_processing_lease_has_one_concurrent_reclaim_winner(
    db_session, aws_resources, tiny_clip, worker_env
):
    from instadescribe_worker.db import get_sessionmaker

    job, message = _seed_job(db_session, aws_resources, tiny_clip, send=False)
    first = claim_job(db_session, job.id, message.message_id, message.requested_at)
    assert first is not None
    db_session.execute(
        sa.update(Job)
        .where(Job.id == job.id)
        .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    db_session.commit()

    barrier = threading.Barrier(2)
    winners: list = [None, None]
    errors: list = []

    def reclaim(i: int):
        session = get_sessionmaker()()
        try:
            barrier.wait()
            winners[i] = claim_job(
                session,
                job.id,
                message.message_id,
                message.requested_at,
                lease_duration_secs=300,
            )
        except Exception as exc:  # surfaced in the parent thread below
            errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=reclaim, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not errors and all(not thread.is_alive() for thread in threads)
    reclaimed = [winner for winner in winners if winner is not None]
    assert len(reclaimed) == 1
    assert reclaimed[0].worker_id != first.worker_id
    db_session.expire_all()
    fresh = db_session.get(Job, job.id)
    assert (fresh.worker_id, fresh.attempt_count) == (reclaimed[0].worker_id, 2)


def test_reclaim_fences_old_owner_and_prevents_duplicate_terminal_side_effects(
    db_session, aws_resources, tiny_clip, worker_env
):
    from instadescribe_worker.artifacts import upload_and_finalize
    from instadescribe_worker.claim import guarded_transition, renew_lease

    job, message = _seed_job(db_session, aws_resources, tiny_clip, send=False)
    first = claim_job(db_session, job.id, message.message_id, message.requested_at)
    old_token = first.worker_id
    db_session.execute(
        sa.update(Job)
        .where(Job.id == job.id)
        .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    db_session.commit()
    second = claim_job(db_session, job.id, message.message_id, message.requested_at)
    assert second is not None and second.worker_id != old_token

    assert guarded_update(db_session, job.id, old_token, progress=88) is False
    assert renew_lease(db_session, job.id, old_token) is False
    assert (
        guarded_transition(db_session, job.id, old_token, JobState.FAILED, error_code="stale")
        is False
    )
    assert (
        upload_and_finalize(
            db_session,
            aws_resources["s3"],
            aws_resources["bucket"],
            first,
            old_token,
            [],
            "0" * 64,
        )
        is False
    )
    assert renew_lease(db_session, job.id, second.worker_id) is True
    assert upload_and_finalize(
        db_session,
        aws_resources["s3"],
        aws_resources["bucket"],
        second,
        second.worker_id,
        [],
        "0" * 64,
    )
    # The stale owner still cannot replay a terminal transaction.
    assert (
        upload_and_finalize(
            db_session,
            aws_resources["s3"],
            aws_resources["bucket"],
            first,
            old_token,
            [],
            "0" * 64,
        )
        is False
    )
    db_session.expire_all()
    fresh = db_session.get(Job, job.id)
    assert (fresh.status, fresh.attempt_count, fresh.lease_expires_at) == (
        "READY_FOR_REVIEW",
        2,
        None,
    )
    rows = list(db_session.execute(sa.select(Artifact)).scalars())
    assert len(rows) == 1 and rows[0].artifact_type == "source_video"


def test_consumer_reclaims_expired_processing_job(
    db_session, aws_resources, fake_pipeline, tiny_clip
):
    job, message = _seed_job(db_session, aws_resources, tiny_clip)
    first = claim_job(db_session, job.id, message.message_id, message.requested_at)
    old_token = first.worker_id
    db_session.execute(
        sa.update(Job)
        .where(Job.id == job.id)
        .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    db_session.commit()
    assert run_once() == "success"
    db_session.expire_all()
    fresh = db_session.get(Job, job.id)
    assert fresh.status == "READY_FOR_REVIEW"
    assert fresh.attempt_count == 2 and fresh.worker_id != old_token
    assert _queue_empty(aws_resources)


def test_queue_heartbeat_failure_requeues_without_terminal_side_effects(
    db_session, aws_resources, fake_pipeline, tiny_clip, monkeypatch
):
    from instadescribe_worker import consumer

    job, _ = _seed_job(db_session, aws_resources, tiny_clip)

    def unavailable(**kwargs):
        raise RuntimeError("queue endpoint and credential text must stay private")

    monkeypatch.setattr(consumer._sqs(), "change_message_visibility", unavailable)
    assert run_once() == "retry_requeued"
    db_session.expire_all()
    fresh = db_session.get(Job, job.id)
    assert (fresh.status, fresh.error_code, fresh.worker_id, fresh.lease_expires_at) == (
        "QUEUED",
        "heartbeat_failed",
        None,
        None,
    )
    assert db_session.scalar(sa.select(sa.func.count()).select_from(Artifact)) == 0
    assert not _queue_empty(aws_resources)


def test_stale_worker_guarded_updates_are_noops(db_session, aws_resources, tiny_clip, worker_env):
    job, message = _seed_job(db_session, aws_resources, tiny_clip, send=False)
    claimed = claim_job(db_session, job.id, message.message_id, message.requested_at)
    assert claimed is not None
    token = claimed.worker_id
    uuid.UUID(token)  # the fencing value is a fresh claim token, not a label
    assert guarded_update(db_session, job.id, "not-the-token", progress=99) is False
    db_session.expire_all()
    assert db_session.get(Job, job.id).progress == 0
    assert guarded_update(db_session, job.id, token, progress=10) is True


def test_same_label_two_attempts_all_stale_operations_fenced(
    db_session, aws_resources, fake_pipeline, tiny_clip
):
    """G5.1 A1 acceptance: two sequential claims under the SAME configured
    label yield distinct tokens, and attempt 1's token can no longer update
    progress/duration, transition to failure, finalize artifacts, or alter
    attempt 2's state."""
    from instadescribe_worker.artifacts import upload_and_finalize
    from instadescribe_worker.claim import guarded_transition

    job, message = _seed_job(db_session, aws_resources, tiny_clip, send=False)
    first = claim_job(db_session, job.id, message.message_id, message.requested_at)
    assert first is not None
    token1 = first.worker_id
    # Attempt 1 is requeued (the retryable path) and attempt 2 claims.
    assert guarded_transition(
        db_session, job.id, token1, JobState.QUEUED, worker_id=None, error_code="pipeline_failed"
    )
    second = claim_job(db_session, job.id, message.message_id, message.requested_at)
    assert second is not None
    token2 = second.worker_id
    assert token1 != token2 and second.attempt_count == 2

    # Every stale-token operation is a no-op.
    assert guarded_update(db_session, job.id, token1, progress=77) is False
    assert guarded_update(db_session, job.id, token1, duration_secs=999) is False
    assert (
        guarded_transition(db_session, job.id, token1, JobState.FAILED, error_code="invalid_media")
        is False
    )
    assert (
        upload_and_finalize(
            db_session, aws_resources["s3"], aws_resources["bucket"], job, token1, [], "0" * 64
        )
        is False
    )
    db_session.expire_all()
    fresh = db_session.get(Job, job.id)
    assert (fresh.status, fresh.worker_id, fresh.attempt_count) == ("PROCESSING", token2, 2)
    assert fresh.progress == 0 and fresh.error_code is None
    count = db_session.execute(sa.select(sa.func.count()).select_from(Artifact)).scalar_one()
    assert count == 0  # the stale finalize wrote nothing


def test_exhaustion_update_requires_full_identity_and_eligibility(
    db_session, aws_resources, fake_pipeline, tiny_clip
):
    """G5.1 A3: the claim-lost exhaustion UPDATE is atomic on job ID, complete
    message identity, claimable status AND attempt ineligibility."""
    job, message = _seed_job(db_session, aws_resources, tiny_clip, attempts=3, send=False)
    # Identity changed between observation and update -> no write.
    assert not exhaust_unclaimable(db_session, job.id, uuid.uuid4(), message.requested_at)
    assert not exhaust_unclaimable(
        db_session, job.id, message.message_id, message.requested_at + timedelta(seconds=1)
    )
    # State changed to PROCESSING -> no write.
    db_session.execute(
        sa.update(Job)
        .where(Job.id == job.id)
        .values(
            status="PROCESSING",
            lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    )
    db_session.commit()
    assert not exhaust_unclaimable(db_session, job.id, message.message_id, message.requested_at)
    # Attempts became eligible again -> no write.
    db_session.execute(
        sa.update(Job).where(Job.id == job.id).values(status="QUEUED", attempt_count=2)
    )
    db_session.commit()
    assert not exhaust_unclaimable(db_session, job.id, message.message_id, message.requested_at)
    db_session.expire_all()
    assert db_session.get(Job, job.id).status == "QUEUED"  # every miss left the row alone
    # Exact identity + ineligible attempts -> durable FAILED.
    db_session.execute(sa.update(Job).where(Job.id == job.id).values(attempt_count=3))
    db_session.commit()
    assert exhaust_unclaimable(db_session, job.id, message.message_id, message.requested_at)
    db_session.expire_all()
    fresh = db_session.get(Job, job.id)
    assert (fresh.status, fresh.error_code) == ("FAILED", "retry_exhausted")
