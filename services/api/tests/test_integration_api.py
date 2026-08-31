"""Public integration surface: problem details, state projection and tenancy."""

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from app.api.integrations.auth import authenticate_integration_principal
from app.api.integrations.pagination import decode_cursor, encode_cursor
from app.api.integrations.problems import IntegrationProblem
from app.core.tenancy import PORTFOLIO_ORGANIZATION_ID, PrincipalContext
from app.db.session import get_db
from app.domain.public_states import PublicJobState, to_public_state
from app.domain.states import JobState
from app.main import app
from app.models import (
    ApiKey,
    Asset,
    IdempotencyRecord,
    Job,
    JobEvent,
    Project,
)
from app.services.api_keys import (
    ApiKeyLimitError,
    create_service_account,
    issue_api_key,
    revoke_api_key,
    verify_api_key,
)
from app.services.idempotency import claim as claim_idempotency
from app.services.idempotency import complete as complete_idempotency
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from instadescribe_contracts.provider import (
    TTS_BETA_MAX_ACTIVE_PREVIEWS_PER_ORGANIZATION,
    TTS_BETA_MAX_APPROVED_SCENES_PER_REVIEW,
    TTS_BETA_MAX_FINAL_SYNTHESIS_CALLS_PER_REVIEW,
    TTS_BETA_MAX_PREVIEW_ATTEMPTS_PER_REQUEST,
    TTS_BETA_MAX_PREVIEW_REQUESTS_PER_JOB,
    TTS_BETA_MAX_PREVIEW_REQUESTS_PER_ORGANIZATION,
    TTS_BETA_MAX_RENDER_ATTEMPTS_PER_REVIEW,
    TTS_BETA_PREVIEW_WINDOW_SECS,
)
from sqlalchemy.orm import Session

requires_db = pytest.mark.skipif(
    not os.environ.get("INSTADESCRIBE_TEST_DATABASE_URL"),
    reason="INSTADESCRIBE_TEST_DATABASE_URL not set (use `make cloud-test` or CI)",
)


def test_integration_auth_and_unknown_routes_use_rfc9457_without_database_access():
    client = TestClient(app)
    unauthorized = client.get("/api/integrations/v1/capabilities")
    assert unauthorized.status_code == 401
    assert unauthorized.headers["content-type"].startswith("application/problem+json")
    body = unauthorized.json()
    assert body["type"].endswith("/unauthorized")
    assert body["status"] == 401
    assert body["code"] == "unauthorized"
    assert body["instance"] == "/api/integrations/v1/capabilities"
    assert body["retryable"] is False
    uuid.UUID(body["requestId"])

    missing = client.get("/api/integrations/v1/not-a-resource")
    assert missing.status_code == 404
    assert missing.headers["content-type"].startswith("application/problem+json")
    assert missing.json()["code"] == "not_found"


def test_capabilities_and_public_state_projection_are_closed_and_stable():
    principal = PrincipalContext(
        organization_id=PORTFOLIO_ORGANIZATION_ID,
        principal_id=uuid.uuid4(),
        principal_type="service_account",
        scopes=frozenset({"*"}),
    )
    app.dependency_overrides[authenticate_integration_principal] = lambda: principal
    try:
        response = TestClient(app).get("/api/integrations/v1/capabilities")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["brand"] == "InstaDescribe"
    assert response.json()["review"] == {"mode": "web"}
    assert response.json()["tts"] == {
        "maxApprovedScenesPerReview": TTS_BETA_MAX_APPROVED_SCENES_PER_REVIEW,
        "maxRenderAttemptsPerReview": TTS_BETA_MAX_RENDER_ATTEMPTS_PER_REVIEW,
        "maxFinalSynthesisCallsPerReview": TTS_BETA_MAX_FINAL_SYNTHESIS_CALLS_PER_REVIEW,
        "previews": {
            "rollingWindowSeconds": TTS_BETA_PREVIEW_WINDOW_SECS,
            "maxRequestsPerJob": TTS_BETA_MAX_PREVIEW_REQUESTS_PER_JOB,
            "maxRequestsPerOrganization": TTS_BETA_MAX_PREVIEW_REQUESTS_PER_ORGANIZATION,
            "maxActivePerOrganization": TTS_BETA_MAX_ACTIVE_PREVIEWS_PER_ORGANIZATION,
            "maxAttemptsPerRequest": TTS_BETA_MAX_PREVIEW_ATTEMPTS_PER_REQUEST,
        },
    }
    assert response.json()["jobStates"] == [state.value for state in PublicJobState]
    assert set(JobState) == {
        state for state in JobState if to_public_state(state) in set(PublicJobState)
    }
    assert to_public_state(JobState.READY_FOR_REVIEW) == PublicJobState.NEEDS_REVIEW
    assert to_public_state(JobState.UPLOAD_COMPLETE) == PublicJobState.QUEUED


def test_integration_validation_is_also_problem_details():
    principal = PrincipalContext(
        organization_id=PORTFOLIO_ORGANIZATION_ID,
        principal_id=uuid.uuid4(),
        principal_type="service_account",
        scopes=frozenset({"*"}),
    )
    app.dependency_overrides[authenticate_integration_principal] = lambda: principal
    app.dependency_overrides[get_db] = lambda: None
    try:
        response = TestClient(app).post(
            "/api/integrations/v1/projects",
            json={},
            headers={"Idempotency-Key": "validation-test"},
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "invalid_request"
    assert response.json()["errors"]


def test_cursor_round_trip_and_malformed_cursor_problem():
    created_at = datetime.now(UTC).replace(microsecond=123456)
    resource_id = uuid.uuid4()
    decoded = decode_cursor(encode_cursor(created_at, resource_id))
    assert decoded is not None
    assert decoded.created_at == created_at
    assert decoded.resource_id == resource_id
    with pytest.raises(IntegrationProblem) as exc:
        decode_cursor("not/base64!")
    assert getattr(exc.value, "code", None) == "invalid_cursor"


def _integration_key(engine) -> str:
    with Session(engine) as session:
        account = create_service_account(
            session,
            PORTFOLIO_ORGANIZATION_ID,
            name=f"test-client-{uuid.uuid4()}",
        )
        issued = issue_api_key(
            service_account=account,
            session=session,
            label="integration tests",
        )
        session.commit()
        return issued.token


def _locked_job_payload(*, project: dict | None = None, transcript: bool = True) -> dict:
    body = {
        "project": project
        or {
            "name": "Agency launch",
            "externalId": f"project-{uuid.uuid4()}",
        },
        "clientReference": f"job-{uuid.uuid4()}",
        "video": {
            "fileName": "launch clip.mp4",
            "contentType": "video/mp4",
            "sizeBytes": 4096,
            "durationSeconds": 30,
        },
        "settings": {
            "preset": "standard",
            "style": "documentary",
            "detail": 3,
            "language": "en-GB",
            "instructions": "Describe meaningful visual action.",
            "voice": "alloy",
        },
    }
    if transcript:
        body["transcript"] = {
            "fileName": "launch captions.vtt",
            "format": "vtt",
            "contentType": "text/vtt",
            "sizeBytes": 512,
        }
    return body


def _stub_presigning(monkeypatch):
    import app.api.integrations.v1 as integration_routes

    calls = []

    def sign(key, content_type, **kwargs):
        calls.append((key, content_type, kwargs.get("max_bytes")))
        return {
            "url": "https://uploads.example.test/",
            "fields": {"key": key, "Content-Type": content_type},
            "expires_at": datetime.now(UTC) + timedelta(minutes=15),
        }

    monkeypatch.setattr(
        integration_routes,
        "generate_upload_post",
        sign,
    )
    return calls


@requires_db
def test_api_keys_store_versioned_server_peppered_digests_only(db_engine):
    token = _integration_key(db_engine)
    with Session(db_engine) as session:
        key = session.execute(sa.select(ApiKey)).scalar_one()
        assert key.digest_version == 1
        assert len(key.secret_digest) == 64
        assert token not in key.secret_digest
    columns = {column["name"] for column in sa.inspect(db_engine).get_columns("api_keys")}
    assert {"digest_version", "secret_digest"} <= columns
    assert not {"secret", "secret_hash", "secret_salt"} & columns


@requires_db
def test_api_key_rotation_allows_two_live_keys_then_requires_revoke(db_engine):
    with Session(db_engine) as session:
        account = create_service_account(
            session,
            PORTFOLIO_ORGANIZATION_ID,
            name=f"rotation-{uuid.uuid4()}",
        )
        first = issue_api_key(session, account, label="old")
        second = issue_api_key(session, account, label="replacement")
        with pytest.raises(ApiKeyLimitError):
            issue_api_key(session, account, label="unbounded-third")
        assert revoke_api_key(session, account.id, first.record.id) is True
        third = issue_api_key(session, account, label="post-revoke")
        session.commit()

        assert verify_api_key(session, first.token) is None
        assert verify_api_key(session, second.token) is not None
        assert verify_api_key(session, third.token) is not None
        assert revoke_api_key(session, account.id, first.record.id) is False


@requires_db
def test_idempotency_key_scope_includes_method_and_path(db_engine):
    with Session(db_engine) as session:
        first = claim_idempotency(
            session,
            PORTFOLIO_ORGANIZATION_ID,
            key="shared-operation-key",
            method="POST",
            path="/api/integrations/v1/projects",
            body={"name": "One"},
        )
        complete_idempotency(session, first, status=201, body={"id": "one"})
        second = claim_idempotency(
            session,
            PORTFOLIO_ORGANIZATION_ID,
            key="shared-operation-key",
            method="POST",
            path="/api/integrations/v1/projects/project-1/jobs",
            body={"fileName": "clip.mp4"},
        )
        assert second.is_replay is False
        complete_idempotency(session, second, status=201, body={"id": "two"})
        assert (
            session.execute(sa.select(sa.func.count()).select_from(IdempotencyRecord)).scalar_one()
            == 2
        )


@requires_db
def test_project_create_replay_and_cross_tenant_isolation(api_db_client, db_engine):
    token = _integration_key(db_engine)
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "project-create-1"}
    created = api_db_client.post(
        "/api/integrations/v1/projects", json={"name": "Agency launch"}, headers=headers
    )
    assert created.status_code == 201, created.text
    assert created.json()["object"] == "project"

    replay = api_db_client.post(
        "/api/integrations/v1/projects", json={"name": "Agency launch"}, headers=headers
    )
    assert replay.status_code == 201
    assert replay.headers["idempotent-replayed"] == "true"
    assert replay.content == created.content
    assert replay.json() == created.json()

    conflict = api_db_client.post(
        "/api/integrations/v1/projects", json={"name": "Different"}, headers=headers
    )
    assert conflict.status_code == 409
    assert conflict.headers["content-type"].startswith("application/problem+json")
    assert conflict.json()["code"] == "idempotency_key_reused"

    foreign_organization = uuid.uuid4()
    foreign_project = uuid.uuid4()
    foreign_job = uuid.uuid4()
    with db_engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO organizations (id, slug, name) "
                "VALUES (:id, :slug, 'Foreign organization')"
            ),
            {"id": str(foreign_organization), "slug": f"foreign-{uuid.uuid4().hex[:8]}"},
        )
        connection.execute(
            sa.text(
                "INSERT INTO projects (id, organization_id, name) "
                "VALUES (:id, :organization_id, 'Foreign project')"
            ),
            {"id": str(foreign_project), "organization_id": str(foreign_organization)},
        )
        connection.execute(
            sa.text(
                "INSERT INTO jobs "
                "(id, organization_id, project_id, pipeline_revision, status, settings) "
                "VALUES (:id, :organization_id, :project_id, "
                "'test', 'AWAITING_UPLOAD', '{}'::jsonb)"
            ),
            {
                "id": str(foreign_job),
                "organization_id": str(foreign_organization),
                "project_id": str(foreign_project),
            },
        )

    assert (
        api_db_client.get(
            f"/api/integrations/v1/projects/{foreign_project}",
            headers={"Authorization": f"Bearer {token}"},
        ).status_code
        == 404
    )
    legacy = api_db_client.get("/api/v1/jobs", headers={"X-Portfolio-Token": "test-token"})
    assert legacy.status_code == 200
    assert str(foreign_job) not in legacy.json()
    assert (
        api_db_client.get(
            f"/api/v1/jobs/{foreign_job}",
            headers={"X-Portfolio-Token": "test-token"},
        ).status_code
        == 404
    )
    assert (
        api_db_client.patch(
            f"/api/v1/projects/{foreign_project}",
            json={"name": "Cross-tenant overwrite", "expectedVersion": 1},
            headers={"X-Portfolio-Token": "test-token"},
        ).status_code
        == 404
    )


@requires_db
def test_job_create_is_tenant_scoped_and_idempotent(api_db_client, db_engine, monkeypatch):
    import app.api.integrations.v1 as integration_routes

    token = _integration_key(db_engine)
    with Session(db_engine) as session:
        project = Project(
            organization_id=PORTFOLIO_ORGANIZATION_ID,
            name="Integration job project",
        )
        session.add(project)
        session.commit()
        project_id = project.id

    monkeypatch.setattr(
        integration_routes,
        "generate_upload_post",
        lambda key, content_type, **_kwargs: {
            "url": "https://uploads.example.test/",
            "fields": {"key": key, "Content-Type": content_type},
            "expires_at": datetime.now(UTC) + timedelta(minutes=15),
        },
    )
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "job-create-1"}
    payload = {
        "durationSeconds": 30,
        "fileName": "clip.mp4",
        "contentType": "video/mp4",
        "sizeBytes": 4096,
    }
    created = api_db_client.post(
        f"/api/integrations/v1/projects/{project_id}/jobs",
        json=payload,
        headers=headers,
    )
    assert created.status_code == 201, created.text
    assert created.json()["state"] == "awaiting_upload"
    assert created.json()["upload"]["method"] == "POST"

    replay = api_db_client.post(
        f"/api/integrations/v1/projects/{project_id}/jobs",
        json=payload,
        headers=headers,
    )
    assert replay.status_code == 201
    assert replay.json() == created.json()
    with Session(db_engine) as session:
        assert session.execute(sa.select(sa.func.count()).select_from(Job)).scalar_one() == 1


def test_locked_create_shape_and_public_openapi_contract():
    principal = PrincipalContext(
        organization_id=PORTFOLIO_ORGANIZATION_ID,
        principal_id=uuid.uuid4(),
        principal_type="service_account",
        scopes=frozenset({"*"}),
    )
    app.dependency_overrides[authenticate_integration_principal] = lambda: principal
    app.dependency_overrides[get_db] = lambda: None
    try:
        client = TestClient(app)
        both = _locked_job_payload(project={"id": str(uuid.uuid4()), "name": "Both"})
        response = client.post(
            "/api/integrations/v1/jobs",
            json=both,
            headers={"Idempotency-Key": "selector-validation"},
        )
        missing = _locked_job_payload()
        missing["project"] = {}
        missing_response = client.post(
            "/api/integrations/v1/jobs",
            json=missing,
            headers={"Idempotency-Key": "selector-validation-2"},
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422
    assert missing_response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")

    document = app.openapi()
    paths = document["paths"]
    create = paths["/api/integrations/v1/jobs"]["post"]
    assert create["operationId"] == "createJob"
    assert create["x-sdk-public"] is True
    assert "/api/integrations/v1/projects/{project_id}/jobs" not in paths
    expected = {
        "capabilities",
        "createJob",
        "completeUpload",
        "listJobs",
        "getJob",
        "cancelJob",
    }
    operations = {
        operation["operationId"]
        for path in paths.values()
        for operation in path.values()
        if isinstance(operation, dict) and operation.get("x-sdk-public") is True
    }
    assert expected <= operations
    assert "needs_review" in PublicJobState
    assert "review_required" not in PublicJobState


def test_transcript_presign_policy_has_an_independent_ten_mib_cap(monkeypatch):
    from app.services import s3 as s3_service

    captured = {}

    class Presigner:
        def generate_presigned_post(self, **kwargs):
            captured.update(kwargs)
            return {"url": "https://uploads.example.test/", "fields": {}}

    monkeypatch.setattr(s3_service, "_presign_client", lambda: Presigner())
    s3_service.generate_upload_post(
        "uploads/orgs/org/jobs/job/transcript/captions.vtt",
        "text/vtt",
        max_bytes=10 * 1024 * 1024,
    )

    assert ["content-length-range", 1, 10 * 1024 * 1024] in captured["Conditions"]
    assert "tagging" not in captured["Fields"]
    assert not any(
        isinstance(condition, list) and len(condition) >= 2 and condition[1] == "$tagging"
        for condition in captured["Conditions"]
    )


def test_investigation_retention_tag_is_an_exact_presigned_post_condition(monkeypatch):
    from app.services import s3 as s3_service

    captured = {}

    class Presigner:
        def generate_presigned_post(self, **kwargs):
            captured.update(kwargs)
            return {"url": "https://uploads.example.test/", "fields": kwargs["Fields"]}

    monkeypatch.setattr(s3_service, "_presign_client", lambda: Presigner())
    retention_tag = s3_service.investigation_retention_tag(14)
    result = s3_service.generate_upload_post(
        "uploads/orgs/org/jobs/job/source/clip.mp4",
        "video/mp4",
        max_bytes=4096,
        retention_tag=retention_tag,
    )

    assert retention_tag == (
        "<Tagging><TagSet><Tag><Key>instadescribe-retention-days</Key>"
        "<Value>14</Value></Tag></TagSet></Tagging>"
    )
    assert captured["Fields"]["tagging"] == retention_tag
    assert ["eq", "$tagging", retention_tag] in captured["Conditions"]
    assert result["fields"]["tagging"] == retention_tag


@pytest.mark.parametrize(
    "retention_tag",
    [
        "",
        "<Tagging><TagSet><Tag><Key>instadescribe-retention-days</Key><Value>0</Value></Tag></TagSet></Tagging>",
        "<Tagging><TagSet><Tag><Key>instadescribe-retention-days</Key><Value>31</Value></Tag></TagSet></Tagging>",
        "<Tagging><TagSet><Tag><Key>instadescribe-retention-days</Key><Value>01</Value></Tag></TagSet></Tagging>",
        "<Tagging><TagSet><Tag><Key>other-retention-days</Key><Value>14</Value></Tag></TagSet></Tagging>",
        "instadescribe-retention-days=14",
        "not-xml",
    ],
)
def test_investigation_retention_tag_rejects_noncanonical_tiers(
    monkeypatch,
    retention_tag,
):
    from app.services import s3 as s3_service

    monkeypatch.setattr(
        s3_service,
        "_presign_client",
        lambda: pytest.fail("invalid tag reached the S3 presigner"),
    )
    with pytest.raises(ValueError, match="retention tag"):
        s3_service.generate_upload_post(
            "uploads/orgs/org/jobs/job/source/clip.mp4",
            "video/mp4",
            retention_tag=retention_tag,
        )


@requires_db
def test_top_level_job_create_is_atomic_durable_and_org_prefixed(
    api_db_client, db_engine, monkeypatch
):
    presign_calls = _stub_presigning(monkeypatch)
    token = _integration_key(db_engine)
    payload = _locked_job_payload()
    headers = {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": "locked-create-1",
    }
    created = api_db_client.post("/api/integrations/v1/jobs", json=payload, headers=headers)
    assert created.status_code == 201, created.text
    assert set(created.json()) == {"job", "uploads"}
    assert set(created.json()["uploads"]) == {"video", "transcript"}
    assert created.json()["job"]["state"] == "awaiting_upload"
    assert created.json()["job"]["clientReference"] == payload["clientReference"]
    assert created.json()["job"]["createdAt"].endswith("Z")
    assert created.json()["job"]["updatedAt"].endswith("Z")
    assert created.json()["uploads"]["video"]["expiresAt"].endswith("Z")
    assert created.json()["uploads"]["transcript"]["expiresAt"].endswith("Z")
    prefix = f"uploads/orgs/{PORTFOLIO_ORGANIZATION_ID}/jobs/"
    assert created.json()["uploads"]["video"]["fields"]["key"].startswith(prefix)
    assert created.json()["uploads"]["transcript"]["fields"]["key"].startswith(prefix)
    assert [
        ("/source/" in key, content_type, max_bytes)
        for key, content_type, max_bytes in presign_calls
    ] == [
        (True, "video/mp4", payload["video"]["sizeBytes"]),
        (False, "text/vtt", payload["transcript"]["sizeBytes"]),
    ]

    replay = api_db_client.post("/api/integrations/v1/jobs", json=payload, headers=headers)
    assert replay.status_code == 201
    assert replay.headers["idempotent-replayed"] == "true"
    assert replay.json() == created.json()

    with Session(db_engine) as session:
        project = session.execute(sa.select(Project)).scalar_one()
        job = session.execute(sa.select(Job)).scalar_one()
        assets = list(session.execute(sa.select(Asset).order_by(Asset.asset_type)).scalars())
        assert project.external_id == payload["project"]["externalId"]
        assert project.organization_id == PORTFOLIO_ORGANIZATION_ID
        assert job.organization_id == PORTFOLIO_ORGANIZATION_ID
        assert job.client_reference == payload["clientReference"]
        assert {asset.asset_type for asset in assets} == {
            "source_video",
            "source_transcript",
        }
        assert all(asset.object_key.startswith(prefix) for asset in assets)
        project_id = project.id

    second_payload = _locked_job_payload(
        project={
            "id": str(project_id),
            "externalId": payload["project"]["externalId"],
        },
        transcript=False,
    )
    second = api_db_client.post(
        "/api/integrations/v1/jobs",
        json=second_payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": "locked-create-existing-project",
        },
    )
    assert second.status_code == 201, second.text
    assert set(second.json()["uploads"]) == {"video"}
    with Session(db_engine) as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(Project)) == 1
        assert session.scalar(sa.select(sa.func.count()).select_from(Job)) == 2


@requires_db
def test_project_etag_patch_replay_stale_and_cross_tenant(api_db_client, db_engine):
    token = _integration_key(db_engine)
    auth = {"Authorization": f"Bearer {token}"}
    created = api_db_client.post(
        "/api/integrations/v1/projects",
        json={"name": "ETag project"},
        headers={**auth, "Idempotency-Key": "etag-create"},
    )
    assert created.status_code == 201
    project_id = created.json()["id"]
    first_etag = created.headers["etag"]

    missing = api_db_client.patch(
        f"/api/integrations/v1/projects/{project_id}",
        json={"name": "No precondition"},
        headers={**auth, "Idempotency-Key": "etag-missing"},
    )
    assert missing.status_code == 428
    assert missing.json()["code"] == "precondition_required"

    headers = {
        **auth,
        "Idempotency-Key": "etag-patch",
        "If-Match": first_etag,
    }
    patched = api_db_client.patch(
        f"/api/integrations/v1/projects/{project_id}",
        json={"name": "Renamed", "starred": True},
        headers=headers,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["version"] == 2
    assert patched.headers["etag"] != first_etag

    replay = api_db_client.patch(
        f"/api/integrations/v1/projects/{project_id}",
        json={"name": "Renamed", "starred": True},
        headers=headers,
    )
    assert replay.status_code == 200
    assert replay.headers["idempotent-replayed"] == "true"
    assert replay.headers["etag"] == patched.headers["etag"]
    assert replay.json() == patched.json()

    stale = api_db_client.patch(
        f"/api/integrations/v1/projects/{project_id}",
        json={"name": "Stale"},
        headers={
            **auth,
            "Idempotency-Key": "etag-stale",
            "If-Match": first_etag,
        },
    )
    assert stale.status_code == 412
    assert stale.json()["code"] == "precondition_failed"

    foreign_organization = uuid.uuid4()
    foreign_project = uuid.uuid4()
    with db_engine.begin() as connection:
        connection.execute(
            sa.text("INSERT INTO organizations (id, slug, name) VALUES (:id, :slug, 'Foreign')"),
            {"id": str(foreign_organization), "slug": f"foreign-{uuid.uuid4().hex[:8]}"},
        )
        connection.execute(
            sa.text(
                "INSERT INTO projects (id, organization_id, name) "
                "VALUES (:id, :organization_id, 'Foreign')"
            ),
            {"id": str(foreign_project), "organization_id": str(foreign_organization)},
        )
    hidden = api_db_client.patch(
        f"/api/integrations/v1/projects/{foreign_project}",
        json={"name": "Leak"},
        headers={
            **auth,
            "Idempotency-Key": "etag-foreign",
            "If-Match": f'"project-{foreign_project}-v1"',
        },
    )
    assert hidden.status_code == 404
    assert hidden.json()["code"] == "not_found"


@requires_db
def test_upload_complete_verifies_both_assets_enqueues_once_and_replays(
    api_db_client, db_engine, monkeypatch
):
    import app.api.integrations.v1 as integration_routes
    import app.api.jobs as legacy_jobs

    _stub_presigning(monkeypatch)
    token = _integration_key(db_engine)
    payload = _locked_job_payload()
    created = api_db_client.post(
        "/api/integrations/v1/jobs",
        json=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": "complete-create",
        },
    )
    assert created.status_code == 201, created.text
    job_id = created.json()["job"]["id"]

    def head(key: str) -> dict:
        transcript = "/transcript/" in key
        return {
            "ContentLength": 512 if transcript else 4096,
            "ContentType": "text/vtt" if transcript else "video/mp4",
            "ServerSideEncryption": "AES256",
            "ETag": '"transcript-etag"' if transcript else '"video-etag"',
            "VersionId": "transcript-v1" if transcript else "video-v1",
        }

    sent = []
    monkeypatch.setattr(integration_routes, "head_source", head)
    monkeypatch.setattr(legacy_jobs, "head_source", head)
    monkeypatch.setattr(legacy_jobs, "send_task_message", sent.append)
    headers = {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": "complete-job",
    }
    completed = api_db_client.post(
        f"/api/integrations/v1/jobs/{job_id}/uploads/complete",
        headers=headers,
    )
    assert completed.status_code == 202, completed.text
    assert completed.json()["state"] == "queued"
    assert len(sent) == 1

    replay = api_db_client.post(
        f"/api/integrations/v1/jobs/{job_id}/uploads/complete",
        headers=headers,
    )
    assert replay.status_code == 202
    assert replay.headers["idempotent-replayed"] == "true"
    assert replay.content == completed.content
    assert replay.json() == completed.json()
    assert len(sent) == 1

    with Session(db_engine) as session:
        job = session.get(Job, uuid.UUID(job_id))
        assets = list(session.execute(sa.select(Asset).where(Asset.job_id == job.id)).scalars())
        assert job.status == JobState.QUEUED.value
        assert job.source_version_id == "video-v1"
        assert {asset.status for asset in assets} == {"validated"}
        assert {asset.version_id for asset in assets} == {"video-v1", "transcript-v1"}


@requires_db
def test_missing_declared_transcript_never_enqueues(api_db_client, db_engine, monkeypatch):
    import app.api.integrations.v1 as integration_routes
    import app.api.jobs as legacy_jobs

    _stub_presigning(monkeypatch)
    token = _integration_key(db_engine)
    created = api_db_client.post(
        "/api/integrations/v1/jobs",
        json=_locked_job_payload(),
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": "missing-transcript-create",
        },
    )
    job_id = created.json()["job"]["id"]

    def missing_transcript(key: str) -> dict:
        if "/transcript/" in key:
            raise ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "missing"}},
                "HeadObject",
            )
        raise AssertionError("video must not be verified before the declared transcript")

    sent = []
    monkeypatch.setattr(integration_routes, "head_source", missing_transcript)
    monkeypatch.setattr(legacy_jobs, "send_task_message", sent.append)
    response = api_db_client.post(
        f"/api/integrations/v1/jobs/{job_id}/uploads/complete",
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": "missing-transcript-complete",
        },
    )
    assert response.status_code == 409
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "source_not_visible"
    assert response.json()["retryable"] is True
    assert sent == []
    with Session(db_engine) as session:
        job = session.get(Job, uuid.UUID(job_id))
        assert job.status == JobState.AWAITING_UPLOAD.value
        assert job.source_version_id is None


@requires_db
def test_cancel_is_tenant_safe_idempotent_and_survives_a_state_race(
    api_db_client, db_engine, monkeypatch
):
    import app.api.integrations.v1 as integration_routes

    _stub_presigning(monkeypatch)
    token = _integration_key(db_engine)
    created = api_db_client.post(
        "/api/integrations/v1/jobs",
        json=_locked_job_payload(transcript=False),
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": "cancel-create",
        },
    )
    job_id = created.json()["job"]["id"]
    real_transition = integration_routes.transition_job
    calls = 0

    def race_once(session, parsed, expected, to_state, *, values=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            session.execute(
                sa.update(Job).where(Job.id == parsed).values(status=JobState.PROCESSING.value)
            )
            session.flush()
            return None
        return real_transition(session, parsed, expected, to_state, values=values)

    monkeypatch.setattr(integration_routes, "transition_job", race_once)
    headers = {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": "cancel-job",
    }
    cancelled = api_db_client.post(
        f"/api/integrations/v1/jobs/{job_id}/cancel",
        headers=headers,
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["state"] == "cancelled"
    assert calls == 2

    replay = api_db_client.post(
        f"/api/integrations/v1/jobs/{job_id}/cancel",
        headers=headers,
    )
    assert replay.status_code == 200
    assert replay.headers["idempotent-replayed"] == "true"
    assert replay.content == cancelled.content
    assert replay.json() == cancelled.json()
    assert calls == 2
    with Session(db_engine) as session:
        event = session.execute(
            sa.select(JobEvent).where(JobEvent.job_id == uuid.UUID(job_id))
        ).scalar_one()
        assert event.event_type == "job.cancelled"
        assert event.payload["type"] == "job.cancelled"
        assert event.payload["state"] == "cancelled"
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(Asset)
                .where(Asset.job_id == uuid.UUID(job_id))
            )
            == 0
        )

    foreign_organization = uuid.uuid4()
    foreign_project = uuid.uuid4()
    foreign_job = uuid.uuid4()
    with db_engine.begin() as connection:
        connection.execute(
            sa.text("INSERT INTO organizations (id, slug, name) VALUES (:id, :slug, 'Foreign')"),
            {"id": str(foreign_organization), "slug": f"foreign-{uuid.uuid4().hex[:8]}"},
        )
        connection.execute(
            sa.text(
                "INSERT INTO projects (id, organization_id, name) "
                "VALUES (:id, :organization_id, 'Foreign')"
            ),
            {"id": str(foreign_project), "organization_id": str(foreign_organization)},
        )
        connection.execute(
            sa.text(
                "INSERT INTO jobs "
                "(id, organization_id, project_id, pipeline_revision, status, settings) "
                "VALUES (:id, :organization_id, :project_id, 'test', "
                "'AWAITING_UPLOAD', '{}'::jsonb)"
            ),
            {
                "id": str(foreign_job),
                "organization_id": str(foreign_organization),
                "project_id": str(foreign_project),
            },
        )
    hidden = api_db_client.post(
        f"/api/integrations/v1/jobs/{foreign_job}/cancel",
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": "cancel-foreign",
        },
    )
    assert hidden.status_code == 404
    assert hidden.json()["code"] == "not_found"
    hidden_complete = api_db_client.post(
        f"/api/integrations/v1/jobs/{foreign_job}/uploads/complete",
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": "complete-foreign",
        },
    )
    assert hidden_complete.status_code == 404
    assert hidden_complete.json()["code"] == "not_found"
