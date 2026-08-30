"""Part A4: REAL concurrent upload-completion (barriers, separate requests)
and injected S3-transport / post-send-DB / failure-metadata outages."""

import os
import threading
import uuid

import httpx
import pytest
import sqlalchemy as sa
from app.models import Job, OrganizationQuota
from botocore.exceptions import EndpointConnectionError
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


def _create(client, name="Concurrent clip", size=4096):
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
    assert resp.status_code in (200, 201, 204)


def _drain(queue):
    queue_url, client = queue
    import json

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


def _parallel_complete(job_ids: list[str], n_threads: int) -> list:
    """Fire upload-complete for the given job ids simultaneously from
    independent clients/threads released by one barrier."""
    from app.main import app
    from fastapi.testclient import TestClient

    barrier = threading.Barrier(n_threads)
    results: list = [None] * n_threads

    def run(i: int, job_id: str):
        client = TestClient(app)  # independent client; sessions are per-request
        barrier.wait()
        r = client.post(f"/api/v1/jobs/{job_id}/upload-complete", headers=AUTH)
        results[i] = r

    threads = [
        threading.Thread(target=run, args=(i, job_ids[i % len(job_ids)])) for i in range(n_threads)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert all(r is not None for r in results)
    return results


def test_concurrent_same_job_completions_share_one_identity(
    api_db_client, db_engine, media_bucket, work_queue
):
    created = _create(api_db_client, "same-job race")
    _upload(created, b"\x01" * 4096)
    results = _parallel_complete([created["jobId"]], 2)
    assert all(r.status_code in (200, 202) for r in results), [
        (r.status_code, r.text) for r in results
    ]
    with Session(db_engine) as s:
        job = s.get(Job, uuid.UUID(created["jobId"]))
        assert job.status == "QUEUED"  # monotonic single winner
        persisted_id = str(job.enqueue_message_id)
    bodies = _drain(work_queue)
    assert bodies, "at least one message expected"
    # Duplicate physical messages are allowed; ALL share one logical identity.
    assert {b["messageId"] for b in bodies} == {persisted_id}


def test_concurrent_competing_jobs_yield_exactly_one_slot_winner(
    api_db_client, db_engine, media_bucket, work_queue
):
    first = _create(api_db_client, "racer-a")
    second = _create(api_db_client, "racer-b")
    _upload(first, b"\x01" * 4096)
    _upload(second, b"\x02" * 4096)
    # Beta defaults permit ten queued jobs per organization. Pin this race to
    # one slot so it continues to prove that the database quota transition has
    # exactly one winner under concurrent completion.
    with Session(db_engine) as session:
        organization_id = session.get(Job, uuid.UUID(first["jobId"])).organization_id
        session.get(OrganizationQuota, organization_id).max_queued_jobs = 1
        session.commit()
    results = _parallel_complete([first["jobId"], second["jobId"]], 2)
    codes = sorted(r.status_code for r in results)
    assert codes == [202, 409], [(r.status_code, r.text) for r in results]
    loser = next(r for r in results if r.status_code == 409)
    assert loser.json()["detail"]["code"] == "capacity_conflict"
    with Session(db_engine) as s:
        jobs = s.execute(sa.select(Job)).scalars().all()
        statuses = sorted(job.status for job in jobs)
        assert statuses == ["AWAITING_UPLOAD", "QUEUED"]
        loser_job_id = str(next(job for job in jobs if job.status == "AWAITING_UPLOAD").id)
        loser_job = s.get(Job, uuid.UUID(loser_job_id))
        assert loser_job.source_etag
        assert loser_job.source_version_id
        assert loser_job.upload_verified_at is not None
    pending = api_db_client.get("/api/v1/jobs", headers=AUTH).json()[loser_job_id]
    assert pending["canonicalState"] == "AWAITING_UPLOAD"
    assert pending["sourceUploaded"] is True
    bodies = _drain(work_queue)
    assert len({b["jobId"] for b in bodies}) == 1  # only the winner enqueued


def test_s3_transport_failure_is_sanitized_503(
    api_db_client, db_engine, media_bucket, work_queue, monkeypatch, caplog
):
    import logging

    import app.api.jobs as jobs_module

    created = _create(api_db_client, "transport")
    _upload(created, b"\x01" * 4096)
    monkeypatch.setattr(
        jobs_module,
        "head_source",
        lambda key: (_ for _ in ()).throw(
            EndpointConnectionError(endpoint_url="http://localstack:4566/secret")
        ),
    )
    with caplog.at_level(logging.WARNING, logger="app.jobs"):
        r = api_db_client.post(f"/api/v1/jobs/{created['jobId']}/upload-complete", headers=AUTH)
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "storage_unavailable"
    surface = r.text + caplog.text
    for leak in ("localstack", "secret", "Endpoint", "Traceback"):
        assert leak not in surface
    with Session(db_engine) as s:
        assert s.get(Job, uuid.UUID(created["jobId"])).status == "AWAITING_UPLOAD"


def test_verification_marker_db_outage_is_sanitized_and_rolled_back(
    api_db_client, db_engine, media_bucket, work_queue, monkeypatch, caplog
):
    import logging

    created = _create(api_db_client, "verification persistence outage")
    _upload(created, b"\x01" * 4096)
    real_execute = Session.execute

    def fail_marker_write(self, statement, *args, **kwargs):
        values = getattr(statement, "_values", {})
        value_keys = {getattr(column, "key", str(column)) for column in values}
        if "source_etag" in value_keys:
            raise sa.exc.OperationalError(
                "UPDATE jobs SET source_etag=:secret",
                {},
                RuntimeError("dsn=postgresql://secret@internal"),
            )
        return real_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(Session, "execute", fail_marker_write)
    with caplog.at_level(logging.WARNING, logger="app.jobs"):
        response = api_db_client.post(
            f"/api/v1/jobs/{created['jobId']}/upload-complete",
            headers=AUTH,
        )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "persistence_unavailable"
    surface = response.text + caplog.text
    for leak in ("dsn=", "secret@internal", "source_etag=:secret", "Traceback"):
        assert leak not in surface
    assert "verification-persistence" in caplog.text
    monkeypatch.setattr(Session, "execute", real_execute)
    with Session(db_engine) as session:
        job = session.get(Job, uuid.UUID(created["jobId"]))
        assert job.status == "AWAITING_UPLOAD"
        assert job.source_etag is None
        assert job.source_version_id is None
        assert job.upload_verified_at is None
    assert _drain(work_queue) == []


def test_post_send_db_outage_returns_accepted_with_precaptured_identity(
    api_db_client, db_engine, media_bucket, work_queue, monkeypatch
):
    import app.api.jobs as jobs_module

    created = _create(api_db_client, "post-send outage")
    _upload(created, b"\x01" * 4096)
    real_transition = jobs_module.transition_job
    calls = {"n": 0}

    def flaky(db, job_id, expected, to_state, **kw):
        calls["n"] += 1
        if calls["n"] == 2:  # the finalize after a successful send
            raise sa.exc.OperationalError("stmt", {}, RuntimeError("db gone"))
        return real_transition(db, job_id, expected, to_state, **kw)

    monkeypatch.setattr(jobs_module, "transition_job", flaky)
    r = api_db_client.post(f"/api/v1/jobs/{created['jobId']}/upload-complete", headers=AUTH)
    # Accepted with the identity captured BEFORE the send — no lazy refresh.
    assert r.status_code == 202, r.text
    assert r.json()["projectId"] == created["projectId"]
    assert r.json()["jobId"] == created["jobId"]
    with Session(db_engine) as s:
        assert s.get(Job, uuid.UUID(created["jobId"])).status == "UPLOAD_COMPLETE"
    assert len(_drain(work_queue)) == 1  # the send happened exactly once


def test_failure_metadata_write_outage_still_returns_sanitized_retryable(
    api_db_client, db_engine, media_bucket, work_queue, monkeypatch, caplog
):
    import logging

    import app.api.jobs as jobs_module

    created = _create(api_db_client, "metadata outage")
    _upload(created, b"\x01" * 4096)

    class BrokenDB:
        def execute(self, *args, **kwargs):
            raise RuntimeError("db down: dsn=postgresql://secret@nowhere")

        def rollback(self):
            raise RuntimeError("rollback down too")

    real_record = jobs_module._record_enqueue_failure
    monkeypatch.setattr(
        jobs_module,
        "send_task_message",
        lambda m: (_ for _ in ()).throw(RuntimeError("sqs down")),
    )
    monkeypatch.setattr(
        jobs_module,
        "_record_enqueue_failure",
        lambda db, jid, now: real_record(BrokenDB(), jid, now),
    )
    with caplog.at_level(logging.WARNING, logger="app.jobs"):
        r = api_db_client.post(f"/api/v1/jobs/{created['jobId']}/upload-complete", headers=AUTH)
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "enqueue_unavailable"
    surface = r.text + caplog.text
    for leak in ("dsn=", "secret@nowhere", "Traceback"):
        assert leak not in surface
    assert "failure-metadata" in caplog.text  # stable category only
    with Session(db_engine) as s:
        # The already-committed UPLOAD_COMPLETE is retained.
        assert s.get(Job, uuid.UUID(created["jobId"])).status == "UPLOAD_COMPLETE"
