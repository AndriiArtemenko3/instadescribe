"""Tenant, trace and decision coverage for the Browser Investigation API."""

import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
import sqlalchemy as sa
from app.api.integrations.auth import authenticate_integration_principal
from app.core.tenancy import PORTFOLIO_ORGANIZATION_ID, PrincipalContext
from app.domain.states import JobState
from app.models import (
    Asset,
    AuditEvent,
    BeliefSnapshot,
    EvidenceItem,
    IdempotencyRecord,
    Investigation,
    Job,
    Project,
    SourceRecord,
)
from app.repositories.investigations import get_investigation
from app.schemas.investigations import EvidenceObservation, InvestigationCreateRequest
from app.services.investigations import evidence_body, keyframe_body
from investigation_support import seed_deterministic_result
from pydantic import ValidationError
from sqlalchemy.orm import Session
from test_app_api import (
    _browser_token,
    _configure_cognito,
    _headers,
    _jwk,
    _seed_member,
    requires_db,
    signing_keys,
)

__all__ = ["signing_keys"]

requires_s3 = pytest.mark.skipif(
    not os.environ.get("INSTADESCRIBE_TEST_S3"),
    reason="INSTADESCRIBE_TEST_S3 not set (use `make cloud-test` or CI)",
)


def _payload() -> dict:
    return {
        "name": "Public video verification",
        "kind": "geolocateProvenance",
        "connectivityPolicy": "local",
        "video": {
            "fileName": "public-evidence.mp4",
            "contentType": "video/mp4",
            "sizeBytes": 4096,
            "durationSeconds": 30,
        },
        "source": {
            "publisherUrl": "https://publisher.example.test/video/1",
            "publishedAt": "2026-08-30T12:00:00Z",
            "legalBasis": "licensed",
            "license": "CC BY 4.0",
            "redistributionPolicy": "metadataOnly",
            "retentionDays": 14,
        },
    }


@pytest.mark.parametrize("duration", (30, 180))
def test_investigation_duration_boundaries_are_explicit(duration):
    payload = _payload()
    payload["video"]["durationSeconds"] = duration
    assert InvestigationCreateRequest.model_validate(payload).video.duration_seconds == duration


@pytest.mark.parametrize("duration", (None, 29.999, 180.001))
def test_investigation_rejects_missing_or_out_of_range_duration(duration):
    payload = _payload()
    if duration is None:
        payload["video"].pop("durationSeconds")
    else:
        payload["video"]["durationSeconds"] = duration
    with pytest.raises(ValidationError):
        InvestigationCreateRequest.model_validate(payload)


def test_pr2_openapi_excludes_crop_approval_mutation():
    from app.main import app

    paths = app.openapi()["paths"]
    assert not any("/egress/" in path for path in paths)


def test_investigation_writes_document_validation_as_problem_details():
    from app.main import app

    paths = app.openapi()["paths"]
    create_response = paths["/api/app/v1/investigations"]["post"]["responses"]["422"]
    assert "unavailable" in create_response["description"]
    assert set(create_response["content"]) == {"application/problem+json"}
    example = create_response["content"]["application/problem+json"]["examples"]["modeUnavailable"]
    assert example["value"]["code"] == "investigation_mode_unavailable"

    validation_paths = (
        "/api/app/v1/investigations/{investigation_id}/cancel",
        "/api/app/v1/investigations/{investigation_id}/decision",
        "/api/app/v1/jobs/{job_id}/uploads/complete",
    )
    for path in validation_paths:
        response = paths[path]["post"]["responses"]["422"]
        assert response["description"] == "Request validation failed"
        assert set(response["content"]) == {"application/problem+json"}


def test_browser_evidence_projection_omits_internal_observation_details():
    from app.main import app

    sentinel = "INTERNAL_OBSERVATION_SENTINEL"
    item = EvidenceItem(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        investigation_id=uuid.uuid4(),
        kind="keyframe",
        observation={
            "summary": "A bounded public observation.",
            "details": {"sentinel": sentinel, "contributions": [{"score": 0.9}]},
        },
        frame_time_ms=4_000,
        bbox=None,
        polarity="neutral",
        reliability=Decimal("0.95"),
        verification_state="proposed",
        correlation_group="frame-4000",
        created_at=datetime(2026, 8, 30, 12, tzinfo=UTC),
    )

    with pytest.raises(ValidationError):
        EvidenceObservation.model_validate(item.observation)
    observation_schema = app.openapi()["components"]["schemas"]["EvidenceObservation"]
    assert set(observation_schema["properties"]) == {"summary"}

    evidence = evidence_body(item)
    keyframe = keyframe_body(item)
    assert evidence["observation"] == {"summary": "A bounded public observation."}
    assert keyframe["observation"] == {"summary": "A bounded public observation."}
    assert item.observation["details"]["sentinel"] == sentinel
    assert sentinel not in str(evidence)
    assert sentinel not in str(keyframe)


def _stub_presigning(monkeypatch) -> list[tuple[str, str, dict]]:
    import app.api.browser.investigations as routes

    calls: list[tuple[str, str, dict]] = []

    def sign(key, content_type, **kwargs):
        calls.append((key, content_type, kwargs))
        fields = {"key": key, "Content-Type": content_type}
        if retention_tag := kwargs.get("retention_tag"):
            fields["tagging"] = retention_tag
        return {
            "url": "https://uploads.example.test/",
            "fields": fields,
            "expires_at": datetime.now(UTC) + timedelta(minutes=15),
        }

    monkeypatch.setattr(
        routes,
        "generate_upload_post",
        sign,
    )
    return calls


@requires_db
def test_create_is_atomic_idempotent_and_source_aware(
    api_db_client,
    db_engine,
    monkeypatch,
    signing_keys,
):
    from app.core.config import get_settings

    # The API may still serve a single-attempt OpenAI AD deployment. Local
    # investigation retry policy is independent and remains three attempts.
    monkeypatch.setenv("INSTADESCRIBE_PROVIDER", "openai")
    monkeypatch.setenv("INSTADESCRIBE_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("INSTADESCRIBE_MAX_DURATION_SECS", "120")
    get_settings.cache_clear()
    private_key = signing_keys[0]
    _configure_cognito(monkeypatch, {"keys": [_jwk(private_key, "primary")]})
    organization_id, principal_id, subject = _seed_member(db_engine, role="editor")
    presign_calls = _stub_presigning(monkeypatch)
    headers = _headers(
        _browser_token(private_key, subject, "editor"),
        idempotency_key="investigation-create",
    )

    first = api_db_client.post("/api/app/v1/investigations", json=_payload(), headers=headers)
    replay = api_db_client.post("/api/app/v1/investigations", json=_payload(), headers=headers)

    assert first.status_code == replay.status_code == 201, first.text
    assert first.content == replay.content
    assert replay.headers["idempotent-replayed"] == "true"
    detail = first.json()["investigation"]
    assert detail["name"] == "Public video verification"
    assert detail["kind"] == "geolocateProvenance"
    assert detail["connectivityPolicy"] == "local"
    assert detail["status"] == "awaitingUpload"
    assert detail["modelProvenance"] == {"executedLocally": False}
    assert first.json()["upload"]["fields"]["key"].startswith(
        f"uploads/orgs/{organization_id}/jobs/{detail['jobId']}/source/"
    )
    retention_tag = (
        "<Tagging><TagSet><Tag><Key>instadescribe-retention-days</Key>"
        "<Value>14</Value></Tag></TagSet></Tagging>"
    )
    assert first.json()["upload"]["fields"]["tagging"] == retention_tag
    assert len(presign_calls) == 1
    assert presign_calls[0][2] == {
        "max_bytes": 4096,
        "retention_tag": retention_tag,
    }
    legacy_projects = api_db_client.get("/api/app/v1/projects", headers=headers)
    assert legacy_projects.status_code == 200
    assert legacy_projects.json()["data"] == []

    with Session(db_engine) as session:
        investigation = session.get(Investigation, uuid.UUID(detail["investigationId"]))
        job = session.get(Job, investigation.job_id)
        source = session.scalar(
            sa.select(SourceRecord).where(
                SourceRecord.organization_id == organization_id,
                SourceRecord.investigation_id == investigation.id,
            )
        )
        assert job.workflow_kind == "video_investigation"
        assert (job.provider, job.model, job.max_attempts) == ("local", None, 3)
        assert job.settings == {
            "workflow_kind": "video_investigation",
            "investigation_id": str(investigation.id),
            "investigation_kind": "geolocate_provenance",
            "connectivity_policy": "local",
        }
        assert source is not None
        assert (source.media_sha256, source.legal_basis, source.retention_days) == (
            None,
            "licensed",
            14,
        )
        assert source.purge_after == source.collected_at + timedelta(days=14)
        asset = session.scalar(
            sa.select(Asset).where(
                Asset.organization_id == organization_id,
                Asset.job_id == job.id,
                Asset.asset_type == "source_video",
            )
        )
        assert asset is not None
        assert asset.purge_after == source.purge_after
        audits = list(session.scalars(sa.select(AuditEvent).order_by(AuditEvent.action)))
        assert {event.action for event in audits} == {
            "project.created",
            "job.created",
            "investigation.created",
        }
        assert all(event.actor_principal_id == principal_id for event in audits)
        assert session.scalar(sa.select(sa.func.count()).select_from(IdempotencyRecord)) == 1


@requires_db
@requires_s3
def test_replayed_investigation_post_tags_versions_created_after_source_pin(
    api_db_client,
    db_engine,
    media_bucket,
    monkeypatch,
    signing_keys,
):
    """A still-live replayed form cannot create an untagged orphan version."""

    import app.api.jobs as legacy_jobs

    private_key = signing_keys[0]
    _configure_cognito(monkeypatch, {"keys": [_jwk(private_key, "primary")]})
    organization_id, _principal_id, subject = _seed_member(db_engine, role="editor")
    browser_assertion = _browser_token(private_key, subject, "editor")
    create_headers = _headers(
        browser_assertion,
        idempotency_key="investigation-retention-reuse",
    )
    payload = _payload()
    payload["source"]["retentionDays"] = 7

    created = api_db_client.post(
        "/api/app/v1/investigations",
        json=payload,
        headers=create_headers,
    )
    replayed = api_db_client.post(
        "/api/app/v1/investigations",
        json=payload,
        headers=create_headers,
    )
    assert created.status_code == replayed.status_code == 201, created.text
    assert replayed.headers["idempotent-replayed"] == "true"
    assert replayed.json()["upload"] == created.json()["upload"]
    upload = created.json()["upload"]
    assert upload["fields"]["tagging"] == (
        "<Tagging><TagSet><Tag><Key>instadescribe-retention-days</Key>"
        "<Value>7</Value></Tag></TagSet></Tagging>"
    )

    def post_version(byte: int) -> None:
        response = httpx.post(
            upload["url"],
            data=upload["fields"],
            files={"file": ("public-evidence.mp4", bytes([byte]) * 4096, "video/mp4")},
            timeout=30,
        )
        assert response.status_code in {200, 201, 204}, response.text

    post_version(1)
    monkeypatch.setattr(legacy_jobs, "send_investigation_task_message", lambda _message: None)
    job_id = created.json()["investigation"]["jobId"]
    completed = api_db_client.post(
        f"/api/app/v1/jobs/{job_id}/uploads/complete",
        headers=_headers(browser_assertion, idempotency_key="investigation-retention-pin"),
    )
    assert completed.status_code == 202, completed.text

    with Session(db_engine) as session:
        pinned_version = session.scalar(
            sa.select(Asset.version_id).where(
                Asset.organization_id == organization_id,
                Asset.job_id == uuid.UUID(job_id),
                Asset.asset_type == "source_video",
            )
        )
    assert pinned_version

    # Reuse the exact replayed form after v1 was pinned. This creates a newer
    # untracked version at the same key; its signed tag is its lifecycle guard.
    post_version(2)
    bucket, s3 = media_bucket
    key = upload["fields"]["key"]
    versions = s3.list_object_versions(Bucket=bucket, Prefix=key).get("Versions", [])
    exact_versions = [version for version in versions if version["Key"] == key]
    assert len(exact_versions) == 2
    assert {version["VersionId"] for version in exact_versions} > {pinned_version}
    assert any(
        version["VersionId"] == pinned_version and not version["IsLatest"]
        for version in exact_versions
    )
    assert any(
        version["VersionId"] != pinned_version and version["IsLatest"] for version in exact_versions
    )
    for version in exact_versions:
        tags = s3.get_object_tagging(
            Bucket=bucket,
            Key=key,
            VersionId=version["VersionId"],
        )["TagSet"]
        assert tags == [{"Key": "instadescribe-retention-days", "Value": "7"}]

    # Cancellation removes the unpinned Asset record, so the signed object
    # tag is also the cleanup guard for upload-without-complete bytes.
    abandoned_payload = _payload()
    abandoned_payload["name"] = "Cancelled upload retention"
    abandoned_payload["source"]["retentionDays"] = 3
    abandoned = api_db_client.post(
        "/api/app/v1/investigations",
        json=abandoned_payload,
        headers=_headers(
            browser_assertion,
            idempotency_key="investigation-retention-cancel-create",
        ),
    )
    assert abandoned.status_code == 201, abandoned.text
    abandoned_upload = abandoned.json()["upload"]
    abandoned_post = httpx.post(
        abandoned_upload["url"],
        data=abandoned_upload["fields"],
        files={"file": ("public-evidence.mp4", b"\x03" * 4096, "video/mp4")},
        timeout=30,
    )
    assert abandoned_post.status_code in {200, 201, 204}, abandoned_post.text
    abandoned_detail = abandoned.json()["investigation"]
    cancelled = api_db_client.post(
        f"/api/app/v1/investigations/{abandoned_detail['investigationId']}/cancel",
        headers=_headers(
            browser_assertion,
            idempotency_key="investigation-retention-cancel",
        ),
    )
    assert cancelled.status_code == 200, cancelled.text
    with Session(db_engine) as session:
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(Asset)
                .where(Asset.job_id == uuid.UUID(abandoned_detail["jobId"]))
            )
            == 0
        )
    assert s3.get_object_tagging(
        Bucket=bucket,
        Key=abandoned_upload["fields"]["key"],
    )["TagSet"] == [{"Key": "instadescribe-retention-days", "Value": "3"}]


@requires_db
def test_investigation_projects_and_jobs_are_absent_from_stable_integration_api(
    api_db_client,
    db_engine,
    monkeypatch,
    signing_keys,
):
    """The month-one pivot must not silently widen the public SDK contract."""

    private_key = signing_keys[0]
    _configure_cognito(monkeypatch, {"keys": [_jwk(private_key, "primary")]})
    organization_id, principal_id, subject = _seed_member(db_engine, role="editor")
    _stub_presigning(monkeypatch)
    created = api_db_client.post(
        "/api/app/v1/investigations",
        json=_payload(),
        headers=_headers(
            _browser_token(private_key, subject, "editor"),
            idempotency_key="investigation-integration-boundary",
        ),
    )
    assert created.status_code == 201, created.text
    detail = created.json()["investigation"]

    integration_principal = PrincipalContext(
        organization_id=organization_id,
        principal_id=principal_id,
        principal_type="service_account",
        scopes=frozenset({"*"}),
    )
    active_app = api_db_client.app
    active_app.dependency_overrides[authenticate_integration_principal] = lambda: (
        integration_principal
    )
    try:
        projects = api_db_client.get("/api/integrations/v1/projects")
        jobs = api_db_client.get("/api/integrations/v1/jobs")
        project = api_db_client.get(f"/api/integrations/v1/projects/{detail['projectId']}")
        job = api_db_client.get(f"/api/integrations/v1/jobs/{detail['jobId']}")
        complete = api_db_client.post(
            f"/api/integrations/v1/jobs/{detail['jobId']}/uploads/complete",
            headers={"Idempotency-Key": "hidden-investigation-complete"},
        )
        cancel = api_db_client.post(
            f"/api/integrations/v1/jobs/{detail['jobId']}/cancel",
            headers={"Idempotency-Key": "hidden-investigation-cancel"},
        )
    finally:
        active_app.dependency_overrides.pop(authenticate_integration_principal, None)

    assert projects.status_code == jobs.status_code == 200
    assert projects.json()["data"] == []
    assert jobs.json()["data"] == []
    assert {project.status_code, job.status_code, complete.status_code, cancel.status_code} == {404}


@requires_db
def test_portfolio_legacy_routes_hide_investigation_workflow(api_db_client, db_engine):
    with Session(db_engine) as session:
        project = Project(
            organization_id=PORTFOLIO_ORGANIZATION_ID,
            name="Hidden investigation",
        )
        session.add(project)
        session.flush()
        job = Job(
            organization_id=PORTFOLIO_ORGANIZATION_ID,
            project_id=project.id,
            workflow_kind="video_investigation",
            pipeline_revision="test",
            status=JobState.READY_FOR_REVIEW.value,
            provider="local",
            max_attempts=3,
            settings={},
        )
        session.add(job)
        session.commit()
        job_id = job.id
        project_id = project.id

    auth = {"X-Portfolio-Token": "test-token"}
    listing = api_db_client.get("/api/v1/jobs", headers=auth)
    assert listing.status_code == 200
    assert str(job_id) not in listing.json()
    assert api_db_client.get(f"/api/v1/jobs/{job_id}", headers=auth).status_code == 404
    assert (
        api_db_client.post(f"/api/v1/jobs/{job_id}/upload-complete", headers=auth).status_code
        == 404
    )
    assert api_db_client.get(f"/api/v1/jobs/{job_id}/manifest", headers=auth).status_code == 404
    assert api_db_client.get(f"/api/v1/jobs/{job_id}/overrides", headers=auth).status_code == 404
    assert (
        api_db_client.patch(
            f"/api/v1/jobs/{job_id}/scenes/scene_1",
            headers=auth,
            json={"ad": "must remain unreachable"},
        ).status_code
        == 404
    )
    assert (
        api_db_client.patch(
            f"/api/v1/projects/{project_id}",
            headers=auth,
            json={"name": "must remain unreachable", "expectedVersion": 1},
        ).status_code
        == 404
    )


@requires_db
@pytest.mark.parametrize(
    ("kind", "policy"),
    [
        ("damageChange", "local"),
        ("geolocateProvenance", "textOnly"),
        ("geolocateProvenance", "approvedCrops"),
        ("geolocateProvenance", "connected"),
    ],
)
def test_create_rejects_modes_without_an_implemented_pipeline(
    api_db_client,
    db_engine,
    monkeypatch,
    signing_keys,
    kind,
    policy,
):
    private_key = signing_keys[0]
    _configure_cognito(monkeypatch, {"keys": [_jwk(private_key, "primary")]})
    _organization_id, _principal_id, subject = _seed_member(db_engine, role="editor")
    payload = _payload()
    payload["kind"] = kind
    payload["connectivityPolicy"] = policy
    response = api_db_client.post(
        "/api/app/v1/investigations",
        json=payload,
        headers=_headers(
            _browser_token(private_key, subject, "editor"),
            idempotency_key=f"unavailable-{kind}-{policy}",
        ),
    )
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "investigation_mode_unavailable"
    with Session(db_engine) as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(Project)) == 0
        assert session.scalar(sa.select(sa.func.count()).select_from(Investigation)) == 0
        assert session.scalar(sa.select(sa.func.count()).select_from(IdempotencyRecord)) == 0


@requires_db
def test_database_rejects_cross_tenant_investigation_job_attachment(db_engine):
    first_org, _first_principal, _first_subject = _seed_member(db_engine, role="editor")
    second_org, _second_principal, _second_subject = _seed_member(db_engine, role="editor")
    with Session(db_engine) as session:
        foreign_project = Project(organization_id=second_org, name="Foreign project")
        session.add(foreign_project)
        session.flush()
        foreign_job = Job(
            organization_id=second_org,
            workflow_kind="video_investigation",
            project_id=foreign_project.id,
            pipeline_revision="test",
            status=JobState.AWAITING_UPLOAD.value,
            provider="local",
            settings={},
        )
        session.add(foreign_job)
        session.flush()
        session.add(
            Investigation(
                organization_id=first_org,
                job_id=foreign_job.id,
                kind="geolocate_provenance",
                connectivity_policy="local",
                status="awaiting_upload",
                model_provenance={"executedLocally": False},
                runtime_provenance={},
            )
        )
        with pytest.raises(sa.exc.IntegrityError):
            session.flush()


@requires_db
def test_upload_complete_queues_investigation_before_publish_and_repairs_replay(
    api_db_client,
    db_engine,
    monkeypatch,
    signing_keys,
):
    import app.api.integrations.v1 as integration_routes
    import app.api.jobs as legacy_jobs

    private_key = signing_keys[0]
    _configure_cognito(monkeypatch, {"keys": [_jwk(private_key, "primary")]})
    organization_id, _principal_id, subject = _seed_member(db_engine, role="editor")
    _stub_presigning(monkeypatch)
    browser_assertion = _browser_token(private_key, subject, "editor")
    created = api_db_client.post(
        "/api/app/v1/investigations",
        json=_payload(),
        headers=_headers(browser_assertion, idempotency_key="investigation-upload-create"),
    )
    assert created.status_code == 201, created.text
    detail = created.json()["investigation"]
    job_id = uuid.UUID(detail["jobId"])
    investigation_id = uuid.UUID(detail["investigationId"])

    def head(_key: str) -> dict:
        return {
            "ContentLength": 4096,
            "ContentType": "video/mp4",
            "ServerSideEncryption": "AES256",
            "ETag": '"investigation-etag"',
            "VersionId": "investigation-video-v1",
        }

    states_at_publish: list[tuple[str, str]] = []

    def publish(_message) -> None:
        with Session(db_engine) as session:
            states_at_publish.append(
                (
                    session.get(Job, job_id).status,
                    session.get(Investigation, investigation_id).status,
                )
            )

    monkeypatch.setattr(integration_routes, "head_source", head)
    monkeypatch.setattr(legacy_jobs, "head_source", head)
    monkeypatch.setattr(
        legacy_jobs,
        "send_task_message",
        lambda _message: pytest.fail("investigation leaked onto audio-description queue"),
    )
    monkeypatch.setattr(legacy_jobs, "send_investigation_task_message", publish)
    complete_headers = _headers(
        browser_assertion,
        idempotency_key="investigation-upload-complete",
    )
    completed = api_db_client.post(
        f"/api/app/v1/jobs/{job_id}/uploads/complete",
        headers=complete_headers,
    )
    assert completed.status_code == 202, completed.text
    assert states_at_publish == [(JobState.UPLOAD_COMPLETE.value, "queued")]

    # Simulate the only split-brain possible during a rolling deploy. The
    # Browser seam must reconcile it before byte-identical idempotency replay.
    with Session(db_engine) as session:
        session.execute(
            sa.update(Investigation)
            .where(Investigation.id == investigation_id)
            .values(status="awaiting_upload")
        )
        session.commit()
    replay = api_db_client.post(
        f"/api/app/v1/jobs/{job_id}/uploads/complete",
        headers=complete_headers,
    )
    assert replay.status_code == 202
    assert replay.headers["idempotent-replayed"] == "true"
    assert replay.content == completed.content
    assert len(states_at_publish) == 1

    with Session(db_engine) as session:
        assert session.get(Job, job_id).status == JobState.QUEUED.value
        queued_investigation = session.get(Investigation, investigation_id)
        assert queued_investigation.status == "queued"
        assert queued_investigation.model_provenance == {"executedLocally": False}
        source = session.scalar(
            sa.select(SourceRecord).where(SourceRecord.investigation_id == investigation_id)
        )
        assert source.organization_id == organization_id


@requires_db
def test_list_and_cancel_investigation_are_tenant_scoped_and_idempotent(
    api_db_client,
    db_engine,
    monkeypatch,
    signing_keys,
):
    private_key = signing_keys[0]
    _configure_cognito(monkeypatch, {"keys": [_jwk(private_key, "primary")]})
    organization_id, _principal_id, subject = _seed_member(db_engine, role="editor")
    _seed_member(db_engine, role="viewer")
    _stub_presigning(monkeypatch)
    browser_assertion = _browser_token(private_key, subject, "editor")
    created = api_db_client.post(
        "/api/app/v1/investigations",
        json=_payload(),
        headers=_headers(browser_assertion, idempotency_key="investigation-cancel-create"),
    )
    assert created.status_code == 201, created.text
    detail = created.json()["investigation"]

    listed = api_db_client.get(
        "/api/app/v1/investigations",
        headers=_headers(browser_assertion),
    )
    assert listed.status_code == 200
    assert [item["investigationId"] for item in listed.json()["data"]] == [
        detail["investigationId"]
    ]

    cancel_headers = _headers(browser_assertion, idempotency_key="investigation-cancel")
    cancelled = api_db_client.post(
        f"/api/app/v1/investigations/{detail['investigationId']}/cancel",
        headers=cancel_headers,
    )
    replay = api_db_client.post(
        f"/api/app/v1/investigations/{detail['investigationId']}/cancel",
        headers=cancel_headers,
    )
    assert cancelled.status_code == replay.status_code == 200
    assert cancelled.content == replay.content
    assert replay.headers["idempotent-replayed"] == "true"
    assert cancelled.json()["status"] == "cancelled"

    with Session(db_engine) as session:
        job_id = uuid.UUID(detail["jobId"])
        assert session.get(Job, job_id).status == JobState.CANCELLED.value
        assert (
            session.get(Investigation, uuid.UUID(detail["investigationId"])).status == "cancelled"
        )
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(SourceRecord)
                .where(SourceRecord.organization_id == organization_id)
            )
            == 1
        )


def _seed_trace(
    db_engine,
    organization_id: uuid.UUID,
    principal_id: uuid.UUID,
    foreign_org_id: uuid.UUID,
) -> tuple[uuid.UUID, uuid.UUID]:
    with Session(db_engine) as session:
        own_project = Project(organization_id=organization_id, name="Own investigation")
        foreign_project = Project(organization_id=foreign_org_id, name="Foreign investigation")
        session.add_all((own_project, foreign_project))
        session.flush()
        own_job = Job(
            organization_id=organization_id,
            workflow_kind="video_investigation",
            project_id=own_project.id,
            pipeline_revision="test",
            status=JobState.PROCESSING.value,
            stage="investigating",
            settings={},
        )
        foreign_job = Job(
            organization_id=foreign_org_id,
            workflow_kind="video_investigation",
            project_id=foreign_project.id,
            pipeline_revision="test",
            status=JobState.READY_FOR_REVIEW.value,
            settings={},
        )
        session.add_all((own_job, foreign_job))
        session.flush()
        own = Investigation(
            organization_id=organization_id,
            job_id=own_job.id,
            kind="geolocate_provenance",
            connectivity_policy="local",
            status="investigating",
            model_provenance={"executedLocally": True},
            runtime_provenance={},
        )
        foreign = Investigation(
            organization_id=foreign_org_id,
            job_id=foreign_job.id,
            kind="geolocate_provenance",
            connectivity_policy="local",
            status="needs_review",
            model_provenance={"executedLocally": True},
            runtime_provenance={},
        )
        session.add_all((own, foreign))
        session.flush()
        session.add(
            SourceRecord(
                organization_id=organization_id,
                job_id=own_job.id,
                investigation_id=own.id,
                publisher_url="https://publisher.example.test/source/trace",
                collected_at=datetime(2026, 8, 30, 12, tzinfo=UTC),
                legal_basis="licensed",
                license_name="CC BY 4.0",
                media_sha256="a" * 64,
                redistribution_policy="metadata_only",
                retention_days=14,
                purge_after=datetime(2026, 9, 13, 12, tzinfo=UTC),
            )
        )
        principal = PrincipalContext(
            organization_id=organization_id,
            principal_id=principal_id,
            principal_type="human",
            scopes=frozenset(),
        )
        row = get_investigation(session, principal, own.id)
        assert row is not None
        seed_deterministic_result(session, principal, row)
        session.commit()
        return own.id, foreign.id


@requires_db
def test_trace_finalize_report_and_idor_matrix(
    api_db_client,
    db_engine,
    monkeypatch,
    signing_keys,
):
    private_key = signing_keys[0]
    _configure_cognito(monkeypatch, {"keys": [_jwk(private_key, "primary")]})
    organization_id, principal_id, subject = _seed_member(db_engine, role="owner")
    foreign_org_id, _foreign_principal, _foreign_subject = _seed_member(db_engine, role="viewer")
    own_id, foreign_id = _seed_trace(db_engine, organization_id, principal_id, foreign_org_id)
    headers = _headers(_browser_token(private_key, subject, "owner"))

    resources = {
        "detail": api_db_client.get(f"/api/app/v1/investigations/{own_id}", headers=headers),
        "steps": api_db_client.get(f"/api/app/v1/investigations/{own_id}/steps", headers=headers),
        "evidence": api_db_client.get(
            f"/api/app/v1/investigations/{own_id}/evidence", headers=headers
        ),
        "keyframes": api_db_client.get(
            f"/api/app/v1/investigations/{own_id}/keyframes", headers=headers
        ),
        "beliefs": api_db_client.get(
            f"/api/app/v1/investigations/{own_id}/beliefs", headers=headers
        ),
    }
    assert {response.status_code for response in resources.values()} == {200}
    assert resources["detail"].json()["status"] == "needsReview"
    assert len(resources["steps"].json()["data"]) == 1
    assert len(resources["evidence"].json()["data"]) == 2
    assert len(resources["keyframes"].json()["data"]) == 1
    assert len(resources["beliefs"].json()["data"]) == 2
    assert all(
        set(item["observation"]) == {"summary"} for item in resources["evidence"].json()["data"]
    )
    assert all(
        set(item["observation"]) == {"summary"} for item in resources["keyframes"].json()["data"]
    )

    evidence_decisions = [
        {"evidenceId": item["evidenceId"], "decision": "accepted"}
        for item in resources["evidence"].json()["data"]
    ]
    finalized = api_db_client.post(
        f"/api/app/v1/investigations/{own_id}/decision",
        json={
            "evidenceDecisions": evidence_decisions,
            "finalHypothesis": {
                "id": "candidate-a",
                "label": "Candidate A",
            },
            "abstain": False,
            "notes": "Reviewed against the visible evidence.",
        },
        headers={**headers, "Idempotency-Key": "finalize-investigation"},
    )
    report = api_db_client.get(f"/api/app/v1/investigations/{own_id}/report", headers=headers)
    assert finalized.status_code == report.status_code == 200, finalized.text
    assert finalized.json()["investigation"]["status"] == "completed"
    assert finalized.json()["investigation"]["calibratedConfidence"] is None
    assert report.json()["latestBelief"]["sequence"] == 2
    assert report.json()["source"] == {
        "sourceRecordId": report.json()["source"]["sourceRecordId"],
        "publisherUrl": "https://publisher.example.test/source/trace",
        "collectedAt": "2026-08-30T12:00:00Z",
        "legalBasis": "licensed",
        "license": "CC BY 4.0",
        "mediaSha256": "a" * 64,
        "redistributionPolicy": "metadataOnly",
        "retentionDays": 14,
        "purgeAfter": "2026-09-13T12:00:00Z",
    }
    assert all(item["verificationState"] == "proposed" for item in report.json()["evidence"])
    assert all(set(item["observation"]) == {"summary"} for item in report.json()["evidence"])
    assert {item["decision"] for item in report.json()["decision"]["evidenceDecisions"]} == {
        "accepted"
    }
    with Session(db_engine) as session:
        own_job = session.scalar(
            sa.select(Job)
            .join(Investigation, Investigation.job_id == Job.id)
            .where(Investigation.id == own_id)
        )
        assert own_job.status == JobState.COMPLETED.value
        assert set(
            session.scalars(
                sa.select(AuditEvent.action).where(AuditEvent.organization_id == organization_id)
            )
        ) == {"investigation.finalized"}

    missing_id = uuid.uuid4()
    for suffix in ("", "/steps", "/evidence", "/keyframes", "/beliefs", "/report"):
        hidden = api_db_client.get(
            f"/api/app/v1/investigations/{foreign_id}{suffix}", headers=headers
        )
        missing = api_db_client.get(
            f"/api/app/v1/investigations/{missing_id}{suffix}", headers=headers
        )
        assert hidden.status_code == missing.status_code == 404
        for key in ("type", "title", "status", "detail", "code"):
            assert hidden.json()[key] == missing.json()[key]


@requires_db
def test_final_report_requires_accepted_evidence_and_immutable_candidate(
    api_db_client,
    db_engine,
    monkeypatch,
    signing_keys,
):
    private_key = signing_keys[0]
    _configure_cognito(monkeypatch, {"keys": [_jwk(private_key, "primary")]})
    organization_id, principal_id, subject = _seed_member(db_engine, role="owner")
    foreign_org_id, _foreign_principal, _foreign_subject = _seed_member(db_engine, role="viewer")
    investigation_id, _foreign_id = _seed_trace(
        db_engine,
        organization_id,
        principal_id,
        foreign_org_id,
    )
    headers = _headers(_browser_token(private_key, subject, "owner"))
    evidence = api_db_client.get(
        f"/api/app/v1/investigations/{investigation_id}/evidence",
        headers=headers,
    ).json()["data"]
    endpoint = f"/api/app/v1/investigations/{investigation_id}/decision"

    rejected = api_db_client.post(
        endpoint,
        headers={**headers, "Idempotency-Key": "reject-all-investigation-evidence"},
        json={
            "evidenceDecisions": [
                {"evidenceId": item["evidenceId"], "decision": "rejected"} for item in evidence
            ],
            "finalHypothesis": {"id": "candidate-a", "label": "Candidate A"},
            "abstain": False,
        },
    )
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "insufficient_accepted_evidence"

    neutral_only = api_db_client.post(
        endpoint,
        headers={**headers, "Idempotency-Key": "neutral-only-investigation-evidence"},
        json={
            "evidenceDecisions": [
                {
                    "evidenceId": item["evidenceId"],
                    "decision": "accepted" if item["kind"] == "keyframe" else "rejected",
                }
                for item in evidence
            ],
            "finalHypothesis": {"id": "candidate-a", "label": "Candidate A"},
            "abstain": False,
        },
    )
    assert neutral_only.status_code == 409
    assert neutral_only.json()["code"] == "accepted_evidence_does_not_support_hypothesis"

    tampered = api_db_client.post(
        endpoint,
        headers={**headers, "Idempotency-Key": "tampered-investigation-hypothesis"},
        json={
            "evidenceDecisions": [
                {"evidenceId": item["evidenceId"], "decision": "accepted"} for item in evidence
            ],
            "finalHypothesis": {
                "id": "candidate-a",
                "label": "Invented replacement",
                "latitude": 1.0,
                "longitude": 2.0,
            },
            "abstain": False,
        },
    )
    assert tampered.status_code == 409
    assert tampered.json()["code"] == "final_hypothesis_mismatch"

    with Session(db_engine) as session:
        latest_belief = session.scalar(
            sa.select(BeliefSnapshot)
            .where(BeliefSnapshot.investigation_id == investigation_id)
            .order_by(BeliefSnapshot.sequence.desc())
            .limit(1)
        )
        assert latest_belief is not None
        latest_belief.abstained = True
        session.commit()

    abstention_override = api_db_client.post(
        endpoint,
        headers={**headers, "Idempotency-Key": "override-machine-abstention"},
        json={
            "evidenceDecisions": [
                {"evidenceId": item["evidenceId"], "decision": "accepted"} for item in evidence
            ],
            "finalHypothesis": {"id": "candidate-a", "label": "Candidate A"},
            "abstain": False,
        },
    )
    assert abstention_override.status_code == 409
    assert abstention_override.json()["code"] == "belief_requires_abstention"
