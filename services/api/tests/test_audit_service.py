"""Sanitized audit rows participate in the caller-owned transaction."""

import inspect
import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from app.core.tenancy import (
    PORTFOLIO_ORGANIZATION_ID,
    PORTFOLIO_PRINCIPAL_ID,
    PrincipalContext,
)
from app.models import AuditEvent, IdempotencyRecord, Project
from app.services.api_keys import create_service_account, issue_api_key
from app.services.audit import append_succeeded, bounded_request_id
from sqlalchemy.orm import Session

pytestmark = pytest.mark.skipif(
    not os.environ.get("INSTADESCRIBE_TEST_DATABASE_URL"),
    reason="INSTADESCRIBE_TEST_DATABASE_URL not set (use `make cloud-test` or CI)",
)

PRINCIPAL = PrincipalContext(
    organization_id=PORTFOLIO_ORGANIZATION_ID,
    principal_id=PORTFOLIO_PRINCIPAL_ID,
    principal_type="service_account",
    scopes=frozenset({"*"}),
)


def _integration_identity(engine) -> tuple[str, uuid.UUID]:
    with Session(engine) as session:
        account = create_service_account(
            session,
            PORTFOLIO_ORGANIZATION_ID,
            name=f"audit-client-{uuid.uuid4()}",
        )
        issued = issue_api_key(session=session, service_account=account, label="audit tests")
        session.commit()
        return issued.token, account.principal_id


def _job_payload(reference: str) -> dict:
    return {
        "project": {
            "name": f"Audit project {reference}",
            "externalId": f"external-{reference}",
        },
        "clientReference": f"client-{reference}",
        "video": {
            "fileName": "sensitive-customer-file.mp4",
            "contentType": "video/mp4",
            "sizeBytes": 4096,
            "durationSeconds": 30,
        },
        "settings": {
            "preset": "standard",
            "style": "documentary",
            "detail": 3,
            "instructions": "sensitive customer prompt",
        },
    }


def test_success_audit_is_closed_sanitized_and_actor_scoped(db_engine):
    resource_id = uuid.uuid4()
    with Session(db_engine) as session:
        append_succeeded(
            session,
            PRINCIPAL,
            action="job.created",
            resource_id=resource_id,
            request_id="request-1234",
        )
        session.commit()

    with Session(db_engine) as session:
        event = session.execute(sa.select(AuditEvent)).scalar_one()
        assert event.organization_id == PRINCIPAL.organization_id
        assert event.actor_principal_id == PRINCIPAL.principal_id
        assert (event.action, event.resource_type, event.resource_id) == (
            "job.created",
            "job",
            str(resource_id),
        )
        assert event.request_id == "request-1234"
        assert event.details == {"outcome": "succeeded"}
        serialized = json.dumps(
            {
                "action": event.action,
                "resourceType": event.resource_type,
                "resourceId": event.resource_id,
                "requestId": event.request_id,
                "details": event.details,
            },
            sort_keys=True,
        )
        for forbidden in (
            "Authorization",
            "apiKeyPrefix",
            "mediaUrl",
            "prompt",
            "fileName",
            "email",
            "remoteAddress",
        ):
            assert forbidden not in serialized

    # The helper has no arbitrary body/details escape hatch, and unsafe
    # request identifiers are omitted rather than copied into persistence.
    assert "details" not in inspect.signature(append_succeeded).parameters
    assert bounded_request_id("contains spaces and bearer material") is None


def test_audit_insert_disappears_with_outer_transaction_rollback(db_engine):
    with Session(db_engine) as session:
        append_succeeded(
            session,
            PRINCIPAL,
            action="project.updated",
            resource_id=uuid.uuid4(),
        )
        session.flush()
        assert session.scalar(sa.select(sa.func.count()).select_from(AuditEvent)) == 1
        session.rollback()

    with Session(db_engine) as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(AuditEvent)) == 0


def test_integration_mutations_audit_once_without_sensitive_material(
    api_db_client,
    db_engine,
    monkeypatch,
):
    import app.api.integrations.v1 as integration_routes
    import app.api.jobs as legacy_jobs

    token, actor_principal_id = _integration_identity(db_engine)
    monkeypatch.setattr(
        integration_routes,
        "generate_upload_post",
        lambda key, content_type, **_kwargs: {
            "url": "https://sensitive-upload.example.test/",
            "fields": {"key": key, "Content-Type": content_type},
            "expires_at": datetime.now(UTC) + timedelta(minutes=15),
        },
    )
    auth = {"Authorization": f"Bearer {token}"}

    project_headers = {**auth, "Idempotency-Key": "audit-project-create"}
    created_project = api_db_client.post(
        "/api/integrations/v1/projects",
        json={"name": "Sensitive customer project"},
        headers=project_headers,
    )
    assert created_project.status_code == 201, created_project.text
    assert (
        api_db_client.post(
            "/api/integrations/v1/projects",
            json={"name": "Sensitive customer project"},
            headers=project_headers,
        ).headers["idempotent-replayed"]
        == "true"
    )
    project_id = created_project.json()["id"]
    patch_headers = {
        **auth,
        "Idempotency-Key": "audit-project-update",
        "If-Match": created_project.headers["etag"],
    }
    patched = api_db_client.patch(
        f"/api/integrations/v1/projects/{project_id}",
        json={"name": "Sensitive renamed project"},
        headers=patch_headers,
    )
    assert patched.status_code == 200, patched.text
    assert (
        api_db_client.patch(
            f"/api/integrations/v1/projects/{project_id}",
            json={"name": "Sensitive renamed project"},
            headers=patch_headers,
        ).headers["idempotent-replayed"]
        == "true"
    )

    cancel_payload = _job_payload("cancel")
    cancel_headers = {**auth, "Idempotency-Key": "audit-job-cancel-create"}
    created_cancel_job = api_db_client.post(
        "/api/integrations/v1/jobs",
        json=cancel_payload,
        headers=cancel_headers,
    )
    assert created_cancel_job.status_code == 201, created_cancel_job.text
    assert (
        api_db_client.post(
            "/api/integrations/v1/jobs",
            json=cancel_payload,
            headers=cancel_headers,
        ).headers["idempotent-replayed"]
        == "true"
    )
    cancel_job_id = created_cancel_job.json()["job"]["id"]
    cancel_request_headers = {**auth, "Idempotency-Key": "audit-job-cancel"}
    cancelled = api_db_client.post(
        f"/api/integrations/v1/jobs/{cancel_job_id}/cancel",
        headers=cancel_request_headers,
    )
    assert cancelled.status_code == 200, cancelled.text
    assert (
        api_db_client.post(
            f"/api/integrations/v1/jobs/{cancel_job_id}/cancel",
            headers=cancel_request_headers,
        ).headers["idempotent-replayed"]
        == "true"
    )

    complete_payload = _job_payload("complete")
    created_complete_job = api_db_client.post(
        "/api/integrations/v1/jobs",
        json=complete_payload,
        headers={**auth, "Idempotency-Key": "audit-job-complete-create"},
    )
    assert created_complete_job.status_code == 201, created_complete_job.text
    complete_job_id = created_complete_job.json()["job"]["id"]

    def head(_key: str) -> dict:
        return {
            "ContentLength": 4096,
            "ContentType": "video/mp4",
            "ServerSideEncryption": "AES256",
            "ETag": '"video-etag"',
            "VersionId": "video-version-1",
        }

    monkeypatch.setattr(integration_routes, "head_source", head)
    monkeypatch.setattr(legacy_jobs, "head_source", head)
    monkeypatch.setattr(legacy_jobs, "send_task_message", lambda _message: None)
    complete_headers = {**auth, "Idempotency-Key": "audit-job-complete"}
    completed = api_db_client.post(
        f"/api/integrations/v1/jobs/{complete_job_id}/uploads/complete",
        headers=complete_headers,
    )
    assert completed.status_code == 202, completed.text
    replayed_complete = api_db_client.post(
        f"/api/integrations/v1/jobs/{complete_job_id}/uploads/complete",
        headers=complete_headers,
    )
    assert replayed_complete.headers["idempotent-replayed"] == "true"

    with Session(db_engine) as session:
        events = list(
            session.execute(
                sa.select(AuditEvent).order_by(AuditEvent.occurred_at, AuditEvent.id)
            ).scalars()
        )
        assert all(event.organization_id == PORTFOLIO_ORGANIZATION_ID for event in events)
        assert all(event.actor_principal_id == actor_principal_id for event in events)
        assert all(event.details == {"outcome": "succeeded"} for event in events)
        assert [event.action for event in events].count("project.created") == 3
        assert [event.action for event in events].count("project.updated") == 1
        assert [event.action for event in events].count("job.created") == 2
        assert [event.action for event in events].count("job.cancelled") == 1
        assert [event.action for event in events].count("job.upload_completed") == 1
        assert sum(event.resource_id == cancel_job_id for event in events) == 2
        assert sum(event.resource_id == complete_job_id for event in events) == 2

        serialized = json.dumps(
            [
                {
                    "organizationId": str(event.organization_id),
                    "actorPrincipalId": str(event.actor_principal_id),
                    "action": event.action,
                    "resourceType": event.resource_type,
                    "resourceId": event.resource_id,
                    "requestId": event.request_id,
                    "details": event.details,
                }
                for event in events
            ],
            sort_keys=True,
        )
        for secret in (
            token,
            token.split(".", 1)[0],
            "sensitive-customer-file.mp4",
            "sensitive customer prompt",
            "https://sensitive-upload.example.test/",
            cancel_payload["clientReference"],
            cancel_payload["project"]["externalId"],
        ):
            assert secret not in serialized


def test_failed_mutation_rolls_back_domain_idempotency_and_audit(
    api_db_client,
    db_engine,
    monkeypatch,
):
    import app.api.integrations.v1 as integration_routes

    token, _actor_principal_id = _integration_identity(db_engine)

    def fail_before_commit(*_args, **_kwargs):
        raise sa.exc.SQLAlchemyError("forced commit failure")

    monkeypatch.setattr(integration_routes.idempotency, "complete", fail_before_commit)
    response = api_db_client.post(
        "/api/integrations/v1/projects",
        json={"name": "Must roll back"},
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": "audit-forced-rollback",
        },
    )
    assert response.status_code == 503

    with Session(db_engine) as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(Project)) == 0
        assert session.scalar(sa.select(sa.func.count()).select_from(AuditEvent)) == 0
        assert session.scalar(sa.select(sa.func.count()).select_from(IdempotencyRecord)) == 0
