"""G3 integration: atomic create, presign policy, browser-style LocalStack
upload, rollback on signing failure, list/get with the legacy adapter."""

import base64
import json
import os
import uuid

import httpx
import pytest
import sqlalchemy as sa
from app.models import Job, Project
from sqlalchemy.orm import Session

AUTH = {"X-Portfolio-Token": "test-token"}

requires_db = pytest.mark.skipif(
    not os.environ.get("INSTADESCRIBE_TEST_DATABASE_URL"),
    reason="INSTADESCRIBE_TEST_DATABASE_URL not set (use `make cloud-test` or CI)",
)
requires_s3 = pytest.mark.skipif(
    not os.environ.get("INSTADESCRIBE_TEST_S3"),
    reason="INSTADESCRIBE_TEST_S3 not set (LocalStack required; use `make cloud-test` or CI)",
)


def payload(name: str = "Integration clip") -> dict:
    return {
        "name": name,
        "durationSecs": 42.0,
        "fileName": "clip.mp4",
        "contentType": "video/mp4",
        "fileSizeBytes": 5_000_000,
    }


def decode_policy(fields: dict) -> dict:
    return json.loads(base64.b64decode(fields["policy"]))


@requires_db
@requires_s3
def test_create_persists_project_and_job_and_returns_browser_visible_post(
    api_db_client, db_engine, media_bucket
):
    r = api_db_client.post("/api/v1/jobs", json=payload(), headers=AUTH)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["projectId"] != body["jobId"]
    assert body["projectVersion"] == 1
    upload = body["upload"]
    assert upload["url"].startswith("http://localhost:4566")
    assert "localstack" not in upload["url"]
    key = upload["fields"]["key"]
    assert key == f"uploads/{body['jobId']}/source/clip.mp4"

    # Exact policy conditions.
    policy = decode_policy(upload["fields"])
    conditions = policy["conditions"]
    assert {"bucket": "instascribe-media"} in conditions
    assert ["eq", "$key", key] in conditions
    assert ["eq", "$Content-Type", "video/mp4"] in conditions
    assert ["eq", "$x-amz-server-side-encryption", "AES256"] in conditions
    assert ["content-length-range", 1, 250 * 1024 * 1024] in conditions

    # Rows persisted with normalized settings + server-side provenance.
    with Session(db_engine) as s:
        job = s.execute(sa.select(Job)).scalar_one()
        project = s.execute(sa.select(Project)).scalar_one()
        assert str(job.id) == body["jobId"]
        assert str(project.id) == body["projectId"]
        assert job.status == "AWAITING_UPLOAD"
        assert job.provider == "fake"
        assert job.pipeline_revision == "test"
        assert job.input_object_key == key
        assert job.settings["chunk_size"] == 60
        assert project.name == "Integration clip"


@requires_db
@requires_s3
def test_openai_create_stamps_single_paid_attempt(
    api_db_client, db_engine, media_bucket, monkeypatch
):
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "provider", "openai")
    monkeypatch.setattr(settings, "max_duration_secs", 120)
    monkeypatch.setattr(settings, "max_attempts", 1)

    response = api_db_client.post("/api/v1/jobs", json=payload("Bounded OpenAI job"), headers=AUTH)
    assert response.status_code == 201, response.text

    with Session(db_engine) as session:
        job = session.execute(sa.select(Job)).scalar_one()
        assert job.provider == "openai"
        assert job.max_attempts == 1


@requires_db
@requires_s3
def test_browser_style_upload_transport(api_db_client, db_engine, media_bucket):
    """Signing/upload TRANSPORT test only: it deliberately declares 5,000,000
    bytes and uploads 4,096 — this LocalStack version has not reliably
    enforced the content-length-range policy, so no size-enforcement claim is
    made here. G4's upload-complete endpoint rejects exactly this declared-
    size mismatch (see test_g4_upload_complete), and the real-S3 policy
    enforcement test remains mandatory at G11."""
    bucket, s3 = media_bucket
    r = api_db_client.post("/api/v1/jobs", json=payload("Upload clip"), headers=AUTH)
    assert r.status_code == 201
    upload = r.json()["upload"]
    content = b"\x00" * 4096  # payload bytes are opaque to the policy
    resp = httpx.post(
        upload["url"],
        data=upload["fields"],
        files={"file": ("clip.mp4", content, "video/mp4")},
        timeout=30,
    )
    assert resp.status_code in (200, 201, 204), resp.text
    head = s3.head_object(Bucket=bucket, Key=upload["fields"]["key"])
    assert head["ContentLength"] == len(content)
    assert head["ContentType"] == "video/mp4"
    assert head.get("ServerSideEncryption") == "AES256"


@requires_db
@requires_s3
def test_oversize_upload_against_a_small_test_policy(api_db_client, media_bucket, monkeypatch):
    from app.core.config import get_settings
    from app.services.s3 import reset_s3_caches

    monkeypatch.setenv("INSTADESCRIBE_MAX_UPLOAD_BYTES", "1024")
    get_settings.cache_clear()
    reset_s3_caches()
    try:
        r = api_db_client.post(
            "/api/v1/jobs",
            json={**payload("Oversize"), "fileSizeBytes": 512},
            headers=AUTH,
        )
        assert r.status_code == 201
        upload = r.json()["upload"]
        policy = decode_policy(upload["fields"])
        assert ["content-length-range", 1, 1024] in policy["conditions"]
        resp = httpx.post(
            upload["url"],
            data=upload["fields"],
            files={"file": ("clip.mp4", b"\x00" * 5000, "video/mp4")},
            timeout=30,
        )
        if resp.status_code not in (400, 403):
            pytest.skip(
                "LocalStack did not enforce content-length-range (observed "
                f"{resp.status_code}); policy-decode proof retained — real-S3 "
                "enforcement test is mandatory at G11"
            )
    finally:
        get_settings.cache_clear()
        reset_s3_caches()


@requires_db
def test_signing_failure_rolls_back_both_rows(api_db_client, db_engine, monkeypatch):
    import app.api.jobs as jobs_module

    def boom(*args, **kwargs):
        raise RuntimeError("signing exploded")

    monkeypatch.setattr(jobs_module, "generate_upload_post", boom)
    r = api_db_client.post("/api/v1/jobs", json=payload("Rollback"), headers=AUTH)
    assert r.status_code == 503
    assert r.json() == {"detail": "upload service unavailable"}
    with Session(db_engine) as s:
        assert s.execute(sa.select(sa.func.count()).select_from(Job)).scalar_one() == 0
        assert s.execute(sa.select(sa.func.count()).select_from(Project)).scalar_one() == 0


@requires_db
@requires_s3
def test_multiple_awaiting_upload_reservations_coexist(api_db_client, media_bucket):
    first = api_db_client.post("/api/v1/jobs", json=payload("First"), headers=AUTH)
    second = api_db_client.post("/api/v1/jobs", json=payload("Second"), headers=AUTH)
    assert first.status_code == second.status_code == 201
    # AWAITING_UPLOAD deliberately does not claim the compute slot (ADR-0008 §2).


@requires_db
@requires_s3
def test_list_and_get_join_projects_with_legacy_adapter(api_db_client, media_bucket):
    created = api_db_client.post("/api/v1/jobs", json=payload("Adapter"), headers=AUTH).json()
    listing = api_db_client.get("/api/v1/jobs", headers=AUTH)
    assert listing.status_code == 200
    entry = listing.json()[created["jobId"]]
    assert entry["id"] == created["jobId"]  # compatibility id == jobId
    assert entry["projectId"] == created["projectId"]
    assert entry["project_name"] == "Adapter"
    assert entry["starred"] is False
    assert entry["projectVersion"] == 1
    assert entry["status"] == "queued"  # legacy mapping of AWAITING_UPLOAD
    assert entry["canonicalState"] == "AWAITING_UPLOAD"
    assert entry["sourceUploaded"] is False
    assert entry["pipeline_revision"] == "test"
    assert entry["chunk_size"] == 60

    got = api_db_client.get(f"/api/v1/jobs/{created['jobId']}", headers=AUTH)
    assert got.status_code == 200
    assert got.json() == entry

    assert (
        api_db_client.get(
            "/api/v1/jobs/00000000-0000-0000-0000-000000000000", headers=AUTH
        ).status_code
        == 404
    )
    assert api_db_client.get("/api/v1/jobs/not-a-uuid", headers=AUTH).status_code == 404


@requires_db
def test_cloud_list_omits_pre_g3_equal_identity_backfill_rows(api_db_client, db_engine):
    legacy_id = uuid.uuid4()
    with Session(db_engine) as session:
        session.add(Project(id=legacy_id, name="pre-G3 migrated row"))
        session.flush()
        session.add(
            Job(
                id=legacy_id,
                project_id=legacy_id,
                pipeline_revision="unknown-pre-g3",
                status="READY_FOR_REVIEW",
                settings={},
            )
        )
        session.commit()

    response = api_db_client.get("/api/v1/jobs", headers=AUTH)
    assert response.status_code == 200
    assert str(legacy_id) not in response.json()
