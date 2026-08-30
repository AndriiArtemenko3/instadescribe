"""Owner invitation authorization, tenancy and fail-closed provisioning proofs."""

from __future__ import annotations

import uuid
from unittest.mock import Mock

import pytest
import sqlalchemy as sa
from app.api.browser import auth as browser_auth
from app.api.browser import invitations as invitation_routes
from app.models import (
    AuditEvent,
    Organization,
    OrganizationInvitation,
    OrganizationMembership,
    Principal,
)
from app.services.browser_assertion import VerifiedBrowserAssertion
from app.services.cognito_invitations import (
    CognitoInvitationConflict,
    CognitoInvitationResult,
    CognitoInvitationUnavailable,
)
from app.services.cognito_jwt import VerifiedCognitoClaims
from sqlalchemy.orm import Session


def _member(
    engine,
    *,
    role: str,
    organization: Organization | None = None,
) -> tuple[uuid.UUID, uuid.UUID, str]:
    subject = f"subject-{uuid.uuid4()}"
    with Session(engine) as db:
        if organization is None:
            organization = Organization(
                slug=f"invite-{uuid.uuid4().hex[:12]}",
                name="Invitation tenant",
            )
            db.add(organization)
            db.flush()
        principal = Principal(
            kind="human",
            display_name=role.title(),
            external_subject=subject,
        )
        db.add(principal)
        db.flush()
        db.add(
            OrganizationMembership(
                organization_id=sa.inspect(organization).identity[0],
                principal_id=principal.id,
                role=role,
            )
        )
        db.commit()
        return sa.inspect(organization).identity[0], principal.id, subject


def _authenticate(monkeypatch, *, subject: str, mfa: bool = True) -> dict[str, str]:
    monkeypatch.setattr(
        browser_auth,
        "verify_cognito_access_token",
        lambda _token: VerifiedCognitoClaims(subject=subject),
    )
    monkeypatch.setattr(
        browser_auth,
        "verify_browser_assertion",
        lambda _token, _value: VerifiedBrowserAssertion(
            email=f"{subject}@example.test",
            mfa_verified=mfa,
        ),
    )
    return {
        "Authorization": "Bearer test-access-token",
        "X-InstaDescribe-Browser-Assertion": "test-browser-assertion",
        "Idempotency-Key": "invite-operation-1",
    }


def _provisioned(email: str, invitation_id: uuid.UUID) -> CognitoInvitationResult:
    del email
    return CognitoInvitationResult(
        subject=f"cognito-{invitation_id}",
        username=f"provider-{invitation_id}",
    )


def test_owner_with_verified_mfa_invites_canonical_member_idempotently(
    api_db_client,
    db_engine,
    monkeypatch,
):
    organization_id, owner_id, subject = _member(db_engine, role="owner")
    headers = _authenticate(monkeypatch, subject=subject)
    provider_mock = Mock(side_effect=_provisioned)
    monkeypatch.setattr(invitation_routes, "provision_invited_user", provider_mock)

    payload = {"email": " New.User@Example.COM ", "role": "reviewer"}
    first = api_db_client.post(
        "/api/app/v1/organization/invitations",
        json=payload,
        headers=headers,
    )
    replay = api_db_client.post(
        "/api/app/v1/organization/invitations",
        json=payload,
        headers=headers,
    )

    assert first.status_code == 201, first.text
    assert first.json() == {
        "invitationId": first.json()["invitationId"],
        "email": "new.user@example.com",
        "role": "reviewer",
        "state": "active",
    }
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert replay.headers["idempotent-replayed"] == "true"
    assert provider_mock.call_count == 1
    with Session(db_engine) as db:
        invitation = db.execute(
            sa.select(OrganizationInvitation).where(
                OrganizationInvitation.organization_id == organization_id
            )
        ).scalar_one()
        membership = db.execute(
            sa.select(OrganizationMembership).where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.principal_id == invitation.principal_id,
            )
        ).scalar_one()
        invited = db.get(Principal, invitation.principal_id)
        audit = db.execute(
            sa.select(AuditEvent).where(AuditEvent.action == "member.invited")
        ).scalar_one()
        assert invitation.state == "active"
        assert membership.is_active is True
        assert invited is not None and invited.is_active is True
        assert invited.external_subject == f"cognito-{invitation.id}"
        assert audit.organization_id == organization_id
        assert audit.actor_principal_id == owner_id
        assert audit.details == {"outcome": "succeeded"}


@pytest.mark.parametrize(
    ("role", "mfa"),
    [("editor", True), ("reviewer", True), ("viewer", True), ("owner", False)],
)
def test_invite_requires_owner_and_authoritative_mfa(
    api_db_client,
    db_engine,
    monkeypatch,
    role,
    mfa,
):
    _organization_id, _principal_id, subject = _member(db_engine, role=role)
    headers = _authenticate(monkeypatch, subject=subject, mfa=mfa)
    provider = Mock()
    monkeypatch.setattr(invitation_routes, "provision_invited_user", provider)

    response = api_db_client.post(
        "/api/app/v1/organization/invitations",
        json={"email": "member@example.com", "role": "viewer"},
        headers=headers,
    )

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"
    provider.assert_not_called()


def test_provider_failure_leaves_durable_inactive_state_and_same_key_retries(
    api_db_client,
    db_engine,
    monkeypatch,
):
    organization_id, _owner_id, subject = _member(db_engine, role="owner")
    headers = _authenticate(monkeypatch, subject=subject)
    calls = 0

    def provider(email: str, invitation_id: uuid.UUID) -> CognitoInvitationResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise CognitoInvitationUnavailable
        return _provisioned(email, invitation_id)

    monkeypatch.setattr(invitation_routes, "provision_invited_user", provider)
    payload = {"email": "retry@example.com", "role": "editor"}

    failed = api_db_client.post(
        "/api/app/v1/organization/invitations",
        json=payload,
        headers=headers,
    )
    assert failed.status_code == 503
    assert failed.json()["code"] == "invitation_unavailable"
    assert failed.json()["retryable"] is True
    with Session(db_engine) as db:
        invitation = db.execute(
            sa.select(OrganizationInvitation).where(
                OrganizationInvitation.organization_id == organization_id
            )
        ).scalar_one()
        membership = db.execute(
            sa.select(OrganizationMembership).where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.principal_id == invitation.principal_id,
            )
        ).scalar_one()
        invited = db.get(Principal, invitation.principal_id)
        assert invitation.state == "pending"
        assert membership.is_active is False
        assert invited is not None and invited.is_active is False
        assert invited.external_subject is None

    retried = api_db_client.post(
        "/api/app/v1/organization/invitations",
        json=payload,
        headers=headers,
    )
    assert retried.status_code == 200
    assert retried.headers["idempotent-replayed"] == "true"
    assert calls == 2


def test_preexisting_provider_identity_returns_stable_enumeration_safe_conflict(
    api_db_client,
    db_engine,
    monkeypatch,
):
    organization_id, _owner_id, subject = _member(db_engine, role="owner")
    headers = _authenticate(monkeypatch, subject=subject)
    provider = Mock(side_effect=CognitoInvitationConflict)
    monkeypatch.setattr(invitation_routes, "provision_invited_user", provider)
    payload = {"email": "existing@example.com", "role": "viewer"}

    first = api_db_client.post(
        "/api/app/v1/organization/invitations",
        json=payload,
        headers=headers,
    )
    replay = api_db_client.post(
        "/api/app/v1/organization/invitations",
        json=payload,
        headers=headers,
    )

    assert first.status_code == replay.status_code == 409
    assert first.json()["code"] == replay.json()["code"] == "invitation_conflict"
    assert (
        first.json()["detail"]
        == replay.json()["detail"]
        == ("The invitation could not be completed.")
    )
    assert provider.call_count == 1
    with Session(db_engine) as db:
        invitation = db.execute(
            sa.select(OrganizationInvitation).where(
                OrganizationInvitation.organization_id == organization_id
            )
        ).scalar_one()
        membership = db.execute(
            sa.select(OrganizationMembership).where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.principal_id == invitation.principal_id,
            )
        ).scalar_one()
        invited = db.get(Principal, invitation.principal_id)
        assert invitation.state == "provider_conflict"
        assert membership.is_active is False
        assert invited is not None and invited.is_active is False


def test_canonical_email_is_global_in_single_org_browser_beta_without_tenant_leak(
    api_db_client,
    db_engine,
    monkeypatch,
):
    organization_a = Organization(slug=f"invite-a-{uuid.uuid4().hex[:8]}", name="A")
    organization_b = Organization(slug=f"invite-b-{uuid.uuid4().hex[:8]}", name="B")
    with Session(db_engine) as db:
        db.add_all([organization_a, organization_b])
        db.commit()
    organization_a_id, _a_principal, subject_a = _member(
        db_engine, role="owner", organization=organization_a
    )
    organization_b_id, _b_principal, subject_b = _member(
        db_engine, role="owner", organization=organization_b
    )
    provider = Mock(side_effect=_provisioned)
    monkeypatch.setattr(invitation_routes, "provision_invited_user", provider)

    first = api_db_client.post(
        "/api/app/v1/organization/invitations",
        json={"email": "one.person@example.com", "role": "viewer"},
        headers=_authenticate(monkeypatch, subject=subject_a),
    )
    assert first.status_code == 201
    second_headers = _authenticate(monkeypatch, subject=subject_b)
    second_headers["Idempotency-Key"] = "other-tenant-same-email"
    second = api_db_client.post(
        "/api/app/v1/organization/invitations",
        json={"email": "ONE.PERSON@example.com", "role": "viewer"},
        headers=second_headers,
    )
    assert second.status_code == 409
    assert second.json()["code"] == "invitation_conflict"
    assert second.json()["detail"] == "The invitation could not be completed."
    assert provider.call_count == 1
    with Session(db_engine) as db:
        assert (
            db.scalar(
                sa.select(sa.func.count())
                .select_from(OrganizationInvitation)
                .where(OrganizationInvitation.organization_id == organization_a_id)
            )
            == 1
        )
        assert (
            db.scalar(
                sa.select(sa.func.count())
                .select_from(OrganizationInvitation)
                .where(OrganizationInvitation.organization_id == organization_b_id)
            )
            == 0
        )


def test_idempotency_key_payload_mismatch_and_owner_role_escalation_are_closed(
    api_db_client,
    db_engine,
    monkeypatch,
):
    _organization_id, _owner_id, subject = _member(db_engine, role="owner")
    headers = _authenticate(monkeypatch, subject=subject)
    provider = Mock(side_effect=_provisioned)
    monkeypatch.setattr(invitation_routes, "provision_invited_user", provider)
    first = api_db_client.post(
        "/api/app/v1/organization/invitations",
        json={"email": "member@example.com", "role": "editor"},
        headers=headers,
    )
    mismatch = api_db_client.post(
        "/api/app/v1/organization/invitations",
        json={"email": "other@example.com", "role": "viewer"},
        headers=headers,
    )
    owner = api_db_client.post(
        "/api/app/v1/organization/invitations",
        json={"email": "owner-two@example.com", "role": "owner"},
        headers={**headers, "Idempotency-Key": "owner-escalation"},
    )
    assert first.status_code == 201
    assert mismatch.status_code == 409
    assert owner.status_code == 422
    assert provider.call_count == 1
