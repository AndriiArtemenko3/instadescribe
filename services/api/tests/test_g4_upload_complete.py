"""G4 upload-complete: exact source verification, atomic slot acquisition via
the partial index, recoverable DB->SQS ordering, stable message identity."""

import json
import os
import uuid

import httpx
import pytest
import sqlalchemy as sa
from app.models import Job, OrganizationQuota
from instadescribe_contracts.queue import QueueMessage
from sqlalchemy.orm import Session

AUTH = {"X-Portfolio-Token": "test-token"}

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("INSTADESCRIBE_TEST_DATABASE_URL"),
        reason="INSTADESCRIBE_TEST_DATABASE_URL not set (use `make cloud-test` or CI)",
    ),
    pytest.mark.skipif(
        not os.environ.get("INSTADESCRIBE_TEST_S3"),
        reason="INSTADESCRIBE_TEST_S3 not set (LocalStack required; use `make cloud-test` or CI)",
    ),
]


def _create(client, name="G4 clip", size=4096):
    r = client.post(
        "/api/v1/jobs",
        json={
            "name": name,
            "durationSecs": 42.0,
            "fileName": "clip.mp4",
            "contentType": "video/mp4",
            "fileSizeBytes": size,
        },
        headers=AUTH,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _upload(created, content: bytes):
    upload = created["upload"]
    resp = httpx.post(
        upload["url"],
        data=upload["fields"],
        files={"file": ("clip.mp4", content, "video/mp4")},
        timeout=30,
    )
    assert resp.status_code in (200, 201, 204), resp.text
    return upload["fields"]["key"]


def _complete(client, created):
    return client.post(f"/api/v1/jobs/{created['jobId']}/upload-complete", headers=AUTH)


def _receive_all(queue):
    queue_url, client = queue
    out = []
    while True:
        messages = client.receive_message(
            QueueUrl=queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=1
        ).get("Messages", [])
        if not messages:
            return out
        for m in messages:
            out.append(json.loads(m["Body"]))
            client.delete_message(QueueUrl=queue_url, ReceiptHandle=m["ReceiptHandle"])


def _job(db_engine, job_id) -> Job:
    with Session(db_engine) as s:
        return s.get(Job, uuid.UUID(job_id))


def test_golden_path_verify_enqueue_and_queued(api_db_client, db_engine, media_bucket, work_queue):
    created = _create(api_db_client)
    _upload(created, b"\x01" * 4096)
    r = _complete(api_db_client, created)
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "queued"
    assert r.json()["projectId"] == created["projectId"]

    job = _job(db_engine, created["jobId"])
    assert job.status == "QUEUED"
    assert job.source_etag and '"' not in job.source_etag  # normalized, stored
    assert job.source_version_id  # C1: versioned bucket, identity pinned once
    assert job.upload_verified_at is not None
    assert job.enqueued_at is not None
    assert job.enqueue_attempt_count == 1
    assert job.enqueue_failed_at is None and job.enqueue_error is None
    # No false checksum claim: only stored when S3 genuinely supplied one.
    if job.source_checksum_sha256 is not None:
        assert job.source_checksum_sha256 != job.source_etag

    bodies = _receive_all(work_queue)
    assert len(bodies) == 1
    message = QueueMessage.model_validate(bodies[0])
    assert str(message.job_id) == created["jobId"]
    assert message.message_id == job.enqueue_message_id
    assert message.task_type == "ANALYZE"


def test_declared_size_mismatch_is_rejected_without_send(
    api_db_client, db_engine, media_bucket, work_queue
):
    # Exactly the G3 transport-test shape: declared 5,000,000, uploaded 4,096.
    created = _create(api_db_client, size=5_000_000)
    _upload(created, b"\x00" * 4096)
    r = _complete(api_db_client, created)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "source_mismatch"
    assert "size" in r.json()["detail"]["checks"]
    assert _job(db_engine, created["jobId"]).status == "AWAITING_UPLOAD"
    assert _receive_all(work_queue) == []


def test_missing_object_is_retryable_409_without_send(
    api_db_client, db_engine, media_bucket, work_queue
):
    created = _create(api_db_client)  # nothing uploaded
    r = _complete(api_db_client, created)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "source_not_visible"
    assert _job(db_engine, created["jobId"]).status == "AWAITING_UPLOAD"
    assert _receive_all(work_queue) == []


def test_wrong_content_type_rejected(api_db_client, db_engine, media_bucket, work_queue):
    bucket, s3 = media_bucket
    created = _create(api_db_client)
    key = created["upload"]["fields"]["key"]
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=b"\x00" * 4096,
        ContentType="video/webm",
        ServerSideEncryption="AES256",
    )
    r = _complete(api_db_client, created)
    assert r.status_code == 422
    assert "content_type" in r.json()["detail"]["checks"]
    assert _job(db_engine, created["jobId"]).status == "AWAITING_UPLOAD"
    assert _receive_all(work_queue) == []


def test_missing_sse_rejected_via_injection(
    api_db_client, db_engine, media_bucket, work_queue, monkeypatch
):
    # Bucket default-encryption makes a genuinely SSE-less object unproducible
    # here, so inject the doctored HeadObject deterministically.
    import app.api.jobs as jobs_module

    created = _create(api_db_client)
    _upload(created, b"\x01" * 4096)
    real = jobs_module.head_source

    def doctored(key):
        head = dict(real(key))
        head.pop("ServerSideEncryption", None)
        return head

    monkeypatch.setattr(jobs_module, "head_source", doctored)
    r = _complete(api_db_client, created)
    assert r.status_code == 422
    assert "encryption" in r.json()["detail"]["checks"]
    assert _job(db_engine, created["jobId"]).status == "AWAITING_UPLOAD"
    assert _receive_all(work_queue) == []


def test_checksum_only_partial_marker_cannot_be_completed_from_later_head(
    api_db_client, db_engine, media_bucket, work_queue, monkeypatch
):
    import app.api.jobs as jobs_module

    created = _create(api_db_client, "partial checksum marker")
    _upload(created, b"\x01" * 4096)
    checksum = "persisted-checksum-only"
    with Session(db_engine) as session:
        session.execute(
            sa.update(Job)
            .where(Job.id == uuid.UUID(created["jobId"]))
            .values(source_checksum_sha256=checksum)
        )
        session.commit()
    real_head = jobs_module.head_source

    def matching_head(key):
        head = dict(real_head(key))
        head["ChecksumSHA256"] = checksum
        return head

    monkeypatch.setattr(jobs_module, "head_source", matching_head)
    response = _complete(api_db_client, created)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "source_identity_changed"
    job = _job(db_engine, created["jobId"])
    assert job.status == "AWAITING_UPLOAD"
    assert job.source_checksum_sha256 == checksum
    assert job.source_etag is None
    assert job.source_version_id is None
    assert job.upload_verified_at is None
    assert _receive_all(work_queue) == []


def test_slot_conflict_preserves_verified_source_and_retries_same_job(
    api_db_client, db_engine, media_bucket, work_queue
):
    first = _create(api_db_client, "holder")
    second = _create(api_db_client, "blocked")
    before = api_db_client.get("/api/v1/jobs", headers=AUTH).json()[second["jobId"]]
    assert before["canonicalState"] == "AWAITING_UPLOAD"
    assert before["sourceUploaded"] is False

    _upload(first, b"\x01" * 4096)
    _upload(second, b"\x02" * 4096)
    # The beta default is ten queued jobs. Reduce this organization's explicit
    # quota to one so the recovery test exercises a real capacity conflict.
    with Session(db_engine) as session:
        organization_id = session.get(Job, uuid.UUID(first["jobId"])).organization_id
        session.get(OrganizationQuota, organization_id).max_queued_jobs = 1
        session.commit()
    assert _complete(api_db_client, first).status_code == 202
    r = _complete(api_db_client, second)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "capacity_conflict"

    blocked = _job(db_engine, second["jobId"])
    assert blocked.status == "AWAITING_UPLOAD"
    assert blocked.source_etag
    assert blocked.source_version_id
    assert blocked.upload_verified_at is not None
    pinned_identity = (
        blocked.source_etag,
        blocked.source_version_id,
        blocked.source_checksum_sha256,
    )
    pending = api_db_client.get("/api/v1/jobs", headers=AUTH).json()[second["jobId"]]
    assert pending["canonicalState"] == "AWAITING_UPLOAD"
    assert pending["sourceUploaded"] is True
    assert not {"upload", "url", "fields"} & pending.keys()

    bodies = _receive_all(work_queue)
    assert len(bodies) == 1  # only the holder's message
    assert bodies[0]["jobId"] == first["jobId"]

    # Releasing the slot permits same-job completion recovery. No new create
    # or S3 upload is performed; the pinned object identity is reused.
    with Session(db_engine) as s:
        s.execute(
            sa.update(Job).where(Job.id == uuid.UUID(first["jobId"])).values(status="COMPLETED")
        )
        s.commit()
    retried = _complete(api_db_client, second)
    assert retried.status_code == 202
    assert retried.json()["jobId"] == second["jobId"]
    recovered = _job(db_engine, second["jobId"])
    assert recovered.status == "QUEUED"
    assert (
        recovered.source_etag,
        recovered.source_version_id,
        recovered.source_checksum_sha256,
    ) == pinned_identity
    retried_messages = _receive_all(work_queue)
    assert len(retried_messages) == 1
    assert retried_messages[0]["jobId"] == second["jobId"]


def test_interruption_before_first_response_keeps_same_job_recoverable(
    api_db_client, db_engine, media_bucket, work_queue, monkeypatch
):
    """A response-path interruption after verification cannot erase proof.

    This models the server-side durable boundary relevant to a client that
    never receives its first upload-complete response: verification commits,
    the request aborts before slot acquisition/response, and a later list plus
    retry recovers the same job without another create or S3 POST.
    """
    import app.api.jobs as jobs_module

    created = _create(api_db_client, "response interrupted")
    _upload(created, b"\x03" * 4096)
    real_transition = jobs_module.transition_job

    class SimulatedResponseInterruption(Exception):
        pass

    monkeypatch.setattr(
        jobs_module,
        "transition_job",
        lambda *args, **kwargs: (_ for _ in ()).throw(SimulatedResponseInterruption()),
    )
    with pytest.raises(SimulatedResponseInterruption):
        _complete(api_db_client, created)

    interrupted = _job(db_engine, created["jobId"])
    assert interrupted.status == "AWAITING_UPLOAD"
    assert interrupted.source_etag
    assert interrupted.source_version_id
    assert interrupted.upload_verified_at is not None
    pending = api_db_client.get("/api/v1/jobs", headers=AUTH).json()[created["jobId"]]
    assert pending["canonicalState"] == "AWAITING_UPLOAD"
    assert pending["sourceUploaded"] is True
    assert _receive_all(work_queue) == []

    monkeypatch.setattr(jobs_module, "transition_job", real_transition)
    retried = _complete(api_db_client, created)
    assert retried.status_code == 202
    assert retried.json()["jobId"] == created["jobId"]
    assert _job(db_engine, created["jobId"]).status == "QUEUED"
    messages = _receive_all(work_queue)
    assert len(messages) == 1
    assert messages[0]["jobId"] == created["jobId"]


def test_sqs_failure_keeps_durable_upload_complete_then_retry_reuses_identity(
    api_db_client, db_engine, media_bucket, work_queue, monkeypatch
):
    import app.api.jobs as jobs_module

    created = _create(api_db_client)
    _upload(created, b"\x01" * 4096)

    def boom(message):
        raise RuntimeError("sqs exploded")

    monkeypatch.setattr(jobs_module, "send_task_message", boom)
    r = _complete(api_db_client, created)
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "enqueue_unavailable"
    job = _job(db_engine, created["jobId"])
    assert job.status == "UPLOAD_COMPLETE"
    assert job.enqueue_failed_at is not None
    assert job.enqueue_error == "sqs_send_failed"  # classified, no AWS text
    first_identity = (job.enqueue_message_id, job.enqueue_requested_at)
    assert first_identity[0] is not None

    monkeypatch.undo()
    r2 = _complete(api_db_client, created)
    assert r2.status_code == 202
    job = _job(db_engine, created["jobId"])
    assert job.status == "QUEUED"
    assert (job.enqueue_message_id, job.enqueue_requested_at) == first_identity
    assert job.enqueue_attempt_count == 2
    assert job.enqueue_failed_at is None and job.enqueue_error is None
    bodies = _receive_all(work_queue)
    assert len(bodies) == 1
    assert bodies[0]["messageId"] == str(first_identity[0])


def test_send_success_final_update_failure_stays_recoverable(
    api_db_client, db_engine, media_bucket, work_queue, monkeypatch
):
    import app.api.jobs as jobs_module

    created = _create(api_db_client)
    _upload(created, b"\x01" * 4096)
    real_transition = jobs_module.transition_job
    calls = {"n": 0}

    def flaky(db, job_id, expected, to_state, **kw):
        calls["n"] += 1
        if calls["n"] == 2:  # the UPLOAD_COMPLETE -> QUEUED finalize
            raise RuntimeError("db blinked")
        return real_transition(db, job_id, expected, to_state, **kw)

    monkeypatch.setattr(jobs_module, "transition_job", flaky)
    r = _complete(api_db_client, created)
    assert r.status_code == 202  # accepted: message sent, durable state recoverable
    assert r.json()["status"] == "queued"  # legacy mapping of UPLOAD_COMPLETE
    job = _job(db_engine, created["jobId"])
    assert job.status == "UPLOAD_COMPLETE"
    assert len(_receive_all(work_queue)) == 1
    monkeypatch.undo()

    # A worker/racer claims it before any retry: the late queue transition
    # must be a no-op, never a regression.
    with Session(db_engine) as s:
        s.execute(
            sa.update(Job)
            .where(Job.id == uuid.UUID(created["jobId"]))
            .values(status="PROCESSING", worker_id="w-sim")
        )
        s.commit()
    r2 = _complete(api_db_client, created)
    assert r2.status_code == 200  # idempotent, no resend
    assert r2.json()["status"] == "processing"
    assert _job(db_engine, created["jobId"]).status == "PROCESSING"
    assert _receive_all(work_queue) == []


def test_retry_after_object_overwrite_refuses_silent_enqueue(
    api_db_client, db_engine, media_bucket, work_queue, monkeypatch
):
    import app.api.jobs as jobs_module

    bucket, s3 = media_bucket
    created = _create(api_db_client)
    key = _upload(created, b"\x01" * 4096)

    monkeypatch.setattr(
        jobs_module, "send_task_message", lambda m: (_ for _ in ()).throw(RuntimeError("down"))
    )
    assert _complete(api_db_client, created).status_code == 503  # durable UPLOAD_COMPLETE
    monkeypatch.undo()

    # Same size/type/SSE but different content => different ETag identity.
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=b"\x02" * 4096,
        ContentType="video/mp4",
        ServerSideEncryption="AES256",
    )
    r = _complete(api_db_client, created)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "source_identity_changed"
    assert _job(db_engine, created["jobId"]).status == "UPLOAD_COMPLETE"
    assert _receive_all(work_queue) == []


def test_retry_after_same_bytes_overwrite_refuses_on_version_change(
    api_db_client, db_engine, media_bucket, work_queue, monkeypatch
):
    """G5.1 C1: identical bytes re-uploaded to the same key keep the ETag but
    mint a NEW VersionId — the publication retry must refuse the changed
    version rather than silently enqueue an unpinned identity."""
    import app.api.jobs as jobs_module

    bucket, s3 = media_bucket
    created = _create(api_db_client)
    key = _upload(created, b"\x01" * 4096)

    monkeypatch.setattr(
        jobs_module, "send_task_message", lambda m: (_ for _ in ()).throw(RuntimeError("down"))
    )
    assert _complete(api_db_client, created).status_code == 503  # durable UPLOAD_COMPLETE
    monkeypatch.undo()
    first_version = _job(db_engine, created["jobId"]).source_version_id
    assert first_version

    s3.put_object(  # SAME bytes -> same ETag, different VersionId
        Bucket=bucket,
        Key=key,
        Body=b"\x01" * 4096,
        ContentType="video/mp4",
        ServerSideEncryption="AES256",
    )
    r = _complete(api_db_client, created)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "source_identity_changed"
    job = _job(db_engine, created["jobId"])
    assert job.status == "UPLOAD_COMPLETE"
    assert job.source_version_id == first_version  # persisted identity untouched
    assert _receive_all(work_queue) == []


def test_unversioned_bucket_is_sanitized_infra_failure_without_slot_or_send(
    api_db_client, db_engine, media_bucket, work_queue, monkeypatch, caplog
):
    """G5.1 C1: missing version evidence is an infrastructure/configuration
    failure — sanitized 503, no compute slot acquired, nothing enqueued."""
    import os as _os
    import secrets

    import boto3
    from app.core.config import get_settings
    from app.services.s3 import reset_s3_caches

    client = boto3.client(
        "s3",
        region_name=_os.environ["AWS_DEFAULT_REGION"],
        endpoint_url=_os.environ["INSTADESCRIBE_S3_ENDPOINT_INTERNAL"],
    )
    unversioned = f"instadescribe-test-unversioned-{_os.getpid()}-{secrets.token_hex(3)}"
    client.create_bucket(
        Bucket=unversioned,
        CreateBucketConfiguration={"LocationConstraint": _os.environ["AWS_DEFAULT_REGION"]},
    )
    client.put_bucket_encryption(
        Bucket=unversioned,
        ServerSideEncryptionConfiguration={
            "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
        },
    )
    try:
        monkeypatch.setenv("INSTADESCRIBE_MEDIA_BUCKET", unversioned)
        get_settings.cache_clear()
        reset_s3_caches()
        created = _create(api_db_client)
        _upload(created, b"\x01" * 4096)
        r = _complete(api_db_client, created)
        assert r.status_code == 503
        assert r.json()["detail"]["code"] == "storage_unavailable"
        assert unversioned not in r.text  # sanitized: no bucket/endpoint leak
        job = _job(db_engine, created["jobId"])
        assert job.status == "AWAITING_UPLOAD"  # the slot was never acquired
        assert job.source_version_id is None and job.enqueued_at is None
        assert _receive_all(work_queue) == []
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
        reset_s3_caches()
        try:
            versions = client.list_object_versions(Bucket=unversioned).get("Versions", [])
            for v in versions:
                client.delete_object(Bucket=unversioned, Key=v["Key"], VersionId=v["VersionId"])
            objects = client.list_objects_v2(Bucket=unversioned).get("Contents", [])
            for o in objects:
                client.delete_object(Bucket=unversioned, Key=o["Key"])
            client.delete_bucket(Bucket=unversioned)
        except Exception:
            pass


def test_idempotent_and_terminal_states(api_db_client, db_engine, media_bucket, work_queue):
    created = _create(api_db_client)
    job_id = uuid.UUID(created["jobId"])
    for status, expected_code, expected_legacy in [
        ("QUEUED", 200, "queued"),
        ("PROCESSING", 200, "processing"),
        ("READY_FOR_REVIEW", 200, "ready"),
        ("COMPLETED", 200, "ready"),
    ]:
        with Session(db_engine) as s:
            s.execute(sa.update(Job).where(Job.id == job_id).values(status=status))
            s.commit()
        r = _complete(api_db_client, created)
        assert (r.status_code, r.json()["status"]) == (expected_code, expected_legacy)
    for status in ("FAILED", "CANCELLED"):
        with Session(db_engine) as s:
            s.execute(sa.update(Job).where(Job.id == job_id).values(status=status))
            s.commit()
        r = _complete(api_db_client, created)
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "terminal_conflict"
    assert _receive_all(work_queue) == []  # never a resend


def test_unknown_job_and_unrelated_integrity_are_not_capacity_conflicts(api_db_client, db_engine):
    r = api_db_client.post(f"/api/v1/jobs/{uuid.uuid4()}/upload-complete", headers=AUTH)
    assert r.status_code == 404
    from app.api.jobs import _is_slot_violation
    from sqlalchemy.exc import IntegrityError

    class FakeDiag:
        constraint_name = "uq_artifacts_job_id_artifact_type"

    class FakeOrig(Exception):
        diag = FakeDiag()

    unrelated = IntegrityError("x", None, FakeOrig())
    assert _is_slot_violation(unrelated) is False


def test_sanitized_response_and_log_surfaces(
    api_db_client, db_engine, media_bucket, work_queue, monkeypatch, caplog
):
    import logging

    import app.api.jobs as jobs_module

    created = _create(api_db_client)
    _upload(created, b"\x01" * 4096)
    monkeypatch.setattr(
        jobs_module,
        "send_task_message",
        lambda m: (_ for _ in ()).throw(RuntimeError("AKIA-secret http://localstack:4566")),
    )
    with caplog.at_level(logging.WARNING, logger="app.jobs"):
        r = _complete(api_db_client, created)
    assert r.status_code == 503
    surface = r.text + caplog.text
    for leak in ("AKIA", "localstack", "boto", "Traceback"):
        assert leak not in surface
    job = _job(db_engine, created["jobId"])
    assert job.enqueue_error == "sqs_send_failed"
