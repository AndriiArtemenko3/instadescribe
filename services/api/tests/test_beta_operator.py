"""Manual beta operator safety, transaction and PostgreSQL persistence proofs."""

from __future__ import annotations

import json
import os
import stat
import uuid
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import sqlalchemy as sa
from app.models import (
    ApiKey,
    AuditEvent,
    Organization,
    OrganizationJobCapacity,
    OrganizationMembership,
    OrganizationQuota,
    Principal,
    ServiceAccount,
    WebhookEndpoint,
)
from app.services.api_keys import IssuedApiKey, verify_api_key
from app.services.beta_operator import (
    OwnerIdentityUnavailable,
    configure_beta_webhook,
    create_beta_service_account,
    create_organization_with_owner,
    discover_marked_owner,
    issue_beta_api_key,
    revoke_beta_api_key,
    rotate_beta_api_key,
    suspend_beta_organization,
)
from sqlalchemy.orm import Session, sessionmaker

from scripts import beta_operator as operator_script

requires_db = pytest.mark.skipif(
    not os.environ.get("INSTADESCRIBE_TEST_DATABASE_URL"),
    reason="INSTADESCRIBE_TEST_DATABASE_URL not set (use `make cloud-test` or CI)",
)


def _provider_user(email: str, marker: uuid.UUID, *, subject: str = "cognito-owner-subject"):
    return {
        "Username": "provider-generated-username",
        "Enabled": True,
        "UserStatus": "FORCE_CHANGE_PASSWORD",
        "UserAttributes": [
            {"Name": "sub", "Value": subject},
            {"Name": "email", "Value": email},
            {"Name": "custom:invitation_id", "Value": str(marker)},
        ],
    }


def test_owner_discovery_is_read_only_and_requires_the_exact_immutable_marker():
    marker = uuid.uuid4()
    client = Mock()
    client.admin_get_user.return_value = _provider_user("owner@example.com", marker)

    owner = discover_marked_owner(
        client,
        user_pool_id="eu-west-2_example",
        email="owner@example.com",
        bootstrap_id=marker,
    )

    assert owner.subject == "cognito-owner-subject"
    client.admin_get_user.assert_called_once_with(
        UserPoolId="eu-west-2_example",
        Username="owner@example.com",
    )
    client.admin_create_user.assert_not_called()
    client.admin_delete_user.assert_not_called()

    client.admin_get_user.return_value = _provider_user(
        "owner@example.com",
        uuid.uuid4(),
    )
    with pytest.raises(OwnerIdentityUnavailable, match="could not be verified"):
        discover_marked_owner(
            client,
            user_pool_id="eu-west-2_example",
            email="owner@example.com",
            bootstrap_id=marker,
        )


@pytest.mark.parametrize(
    "change",
    [
        {"Enabled": False},
        {"UserStatus": "RESET_REQUIRED"},
        {"UserAttributes": []},
    ],
)
def test_owner_discovery_rejects_unsafe_provider_states_without_provider_details(change):
    marker = uuid.uuid4()
    response = _provider_user("owner@example.com", marker)
    response.update(change)
    client = Mock()
    client.admin_get_user.return_value = response

    with pytest.raises(OwnerIdentityUnavailable) as error:
        discover_marked_owner(
            client,
            user_pool_id="eu-west-2_example",
            email="owner@example.com",
            bootstrap_id=marker,
        )
    assert "owner@example.com" not in str(error.value)


class _UnitSessionFactory:
    def begin(self):
        return nullcontext(object())


def test_cli_writes_key_once_to_exclusive_0600_file_and_never_prints_it(
    tmp_path,
    monkeypatch,
    capsys,
):
    token = f"idsb_live_{'a' * 12}.{'B' * 43}"
    record = SimpleNamespace(
        id=uuid.uuid4(),
        key_prefix="a" * 12,
        expires_at=datetime.now(UTC) + timedelta(days=90),
    )
    calls = 0

    def issue(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return IssuedApiKey(record=record, token=token)

    monkeypatch.setattr(operator_script, "issue_beta_api_key", issue)
    output = tmp_path / "issued-key.txt"
    arguments = [
        "issue-api-key",
        "--organization-id",
        str(uuid.uuid4()),
        "--service-account-id",
        str(uuid.uuid4()),
        "--label",
        "canary",
        "--scope",
        "jobs:read",
        "--output-file",
        str(output),
    ]

    assert operator_script.main(arguments, session_factory=_UnitSessionFactory()) == 0
    first_output = capsys.readouterr()
    assert output.read_text() == token + "\n"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert token not in first_output.out
    assert token not in first_output.err
    assert json.loads(first_output.out)["plaintextWritten"] is True

    assert operator_script.main(arguments, session_factory=_UnitSessionFactory()) == 1
    repeated_output = capsys.readouterr()
    assert calls == 1
    assert output.read_text() == token + "\n"
    assert token not in repeated_output.out
    assert token not in repeated_output.err
    assert "already exists" in repeated_output.err


class _AmbiguousTransaction:
    def __enter__(self):
        return object()

    def __exit__(self, exception_type, exception, traceback):
        del exception, traceback
        assert exception_type is None
        raise ConnectionError("simulated lost commit acknowledgement")


class _AmbiguousSessionFactory:
    def begin(self):
        return _AmbiguousTransaction()


def test_ambiguous_commit_preserves_fsynced_token_and_reports_safe_reconciliation_ids(
    tmp_path,
    monkeypatch,
    capsys,
):
    token = f"idsb_live_{'c' * 12}.{'D' * 43}"
    record = SimpleNamespace(
        id=uuid.uuid4(),
        key_prefix="c" * 12,
        expires_at=datetime.now(UTC) + timedelta(days=90),
    )
    monkeypatch.setattr(
        operator_script,
        "issue_beta_api_key",
        lambda *_args, **_kwargs: IssuedApiKey(record=record, token=token),
    )
    output = tmp_path / "ambiguous-key.txt"

    result = operator_script.main(
        [
            "issue-api-key",
            "--organization-id",
            str(uuid.uuid4()),
            "--service-account-id",
            str(uuid.uuid4()),
            "--label",
            "ambiguous commit",
            "--scope",
            "jobs:read",
            "--output-file",
            str(output),
        ],
        session_factory=_AmbiguousSessionFactory(),
    )

    captured = capsys.readouterr()
    assert result == 1
    assert output.read_text() == token + "\n"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert str(record.id) in captured.err
    assert record.key_prefix in captured.err
    assert "ambiguous" in captured.err
    assert token not in captured.out
    assert token not in captured.err


@requires_db
def test_postgresql_operator_lifecycle_is_tenant_scoped_audited_and_suspendable(db_engine):
    owner_subject = f"cognito-{uuid.uuid4()}"
    endpoint_url = "https://hooks.example.test/instadescribe"
    secret_reference = (
        "arn:aws:secretsmanager:eu-west-2:123456789012:"
        "secret:instadescribe-beta/webhook-signing/canary-AbCdEf"
    )
    expiry = datetime.now(UTC) + timedelta(days=90)

    with Session(db_engine) as session:
        created = create_organization_with_owner(
            session,
            slug=f"operator-{uuid.uuid4().hex[:10]}",
            name="Operator canary",
            owner_subject=owner_subject,
            owner_display_name="Canary owner",
        )
        account = create_beta_service_account(
            session,
            organization_id=created.organization_id,
            name="canary integration",
        )
        first = issue_beta_api_key(
            session,
            organization_id=created.organization_id,
            service_account_id=account.id,
            label="initial",
            scopes={"jobs:read", "jobs:write"},
            expires_at=expiry,
        )
        replacement = rotate_beta_api_key(
            session,
            organization_id=created.organization_id,
            service_account_id=account.id,
            current_api_key_id=first.record.id,
            label="replacement",
            scopes={"jobs:read", "jobs:write"},
            expires_at=expiry,
        )
        configured = configure_beta_webhook(
            session,
            organization_id=created.organization_id,
            endpoint_url=endpoint_url,
            signing_secret_ref=secret_reference,
            allowed_hosts={"hooks.example.test"},
        )
        replay = configure_beta_webhook(
            session,
            organization_id=created.organization_id,
            endpoint_url=endpoint_url,
            signing_secret_ref=secret_reference,
            allowed_hosts={"hooks.example.test"},
        )
        session.commit()
        organization_id = created.organization_id
        owner_id = created.owner_principal_id
        account_id = account.id
        first_id = first.record.id
        replacement_id = replacement.record.id
        first_token = first.token
        replacement_token = replacement.token

    assert configured.changed is True
    assert replay.changed is False
    assert replay.endpoint_id == configured.endpoint_id
    with Session(db_engine) as session:
        organization = session.get(Organization, organization_id)
        owner = session.get(Principal, owner_id)
        membership = session.get(
            OrganizationMembership,
            {"organization_id": organization_id, "principal_id": owner_id},
        )
        assert organization is not None and organization.is_active is True
        assert owner is not None and owner.external_subject == owner_subject
        assert membership is not None and membership.role == "owner"
        assert session.get(OrganizationQuota, organization_id) is not None
        assert session.get(OrganizationJobCapacity, organization_id) is not None
        assert session.get(ServiceAccount, account_id) is not None
        keys = session.scalars(
            sa.select(ApiKey).where(ApiKey.service_account_id == account_id).order_by(ApiKey.label)
        ).all()
        assert {key.id for key in keys} == {first_id, replacement_id}
        assert all(first_token not in key.secret_digest for key in keys)
        assert all(replacement_token not in key.secret_digest for key in keys)
        endpoint = session.get(WebhookEndpoint, configured.endpoint_id)
        assert endpoint is not None and endpoint.is_active is True
        assert endpoint.secret_version == 1
        assert verify_api_key(session, first_token) is not None
        assert verify_api_key(session, replacement_token) is not None

        events = session.scalars(
            sa.select(AuditEvent).where(AuditEvent.organization_id == organization_id)
        ).all()
        assert {event.action for event in events} == {
            "operator.organization.created",
            "operator.owner.bound",
            "operator.service_account.created",
            "operator.api_key.issued",
            "operator.api_key.rotated",
            "operator.webhook.configured",
        }
        assert all(event.actor_principal_id is None for event in events)
        assert all(event.request_id is None for event in events)
        assert all(event.details == {"outcome": "succeeded"} for event in events)
        audit_blob = json.dumps(
            [
                {
                    "action": event.action,
                    "resourceType": event.resource_type,
                    "resourceId": event.resource_id,
                    "details": event.details,
                }
                for event in events
            ],
            sort_keys=True,
        )
        for forbidden in (
            owner_subject,
            endpoint_url,
            secret_reference,
            first_token,
            replacement_token,
        ):
            assert forbidden not in audit_blob

    suspended_at = datetime.now(UTC)
    with Session(db_engine) as session:
        revoked = revoke_beta_api_key(
            session,
            organization_id=organization_id,
            service_account_id=account_id,
            api_key_id=first_id,
            now=suspended_at,
        )
        suspended = suspend_beta_organization(
            session,
            organization_id=organization_id,
            confirmed_slug=session.get(Organization, organization_id).slug,
            now=suspended_at,
        )
        session.commit()
        assert revoked.revoked_at == suspended_at
        assert suspended.changed is True

    with Session(db_engine) as session:
        assert session.get(Organization, organization_id).is_active is False
        endpoint = session.get(WebhookEndpoint, configured.endpoint_id)
        assert endpoint is not None and endpoint.is_active is False
        assert endpoint.disabled_at == suspended_at
        assert verify_api_key(session, first_token) is None
        assert verify_api_key(session, replacement_token) is None
        terminal_actions = set(
            session.scalars(
                sa.select(AuditEvent.action).where(AuditEvent.organization_id == organization_id)
            )
        )
        assert "operator.api_key.revoked" in terminal_actions
        assert "operator.organization.suspended" in terminal_actions


@requires_db
@pytest.mark.parametrize("failure_point", ["write", "fsync"])
def test_key_file_failure_rolls_back_key_and_audit_and_removes_owned_file(
    db_engine,
    tmp_path,
    monkeypatch,
    failure_point,
):
    with Session(db_engine) as session:
        created = create_organization_with_owner(
            session,
            slug=f"write-failure-{uuid.uuid4().hex[:8]}",
            name="Write failure tenant",
            owner_subject=f"subject-{uuid.uuid4()}",
            owner_display_name="Write failure owner",
        )
        account = create_beta_service_account(
            session,
            organization_id=created.organization_id,
            name="write failure account",
        )
        session.commit()
        organization_id = created.organization_id
        account_id = account.id

    output = tmp_path / "must-not-survive.txt"
    sessions = sessionmaker(bind=db_engine, expire_on_commit=False)
    real_operation = getattr(operator_script.os, failure_point)

    def fail_file_operation(*arguments):
        del arguments
        raise OSError(f"synthetic secret file {failure_point} failure")

    monkeypatch.setattr(operator_script.os, failure_point, fail_file_operation)
    try:
        with pytest.raises(OSError, match="synthetic"):
            operator_script.execute_key_operation(
                sessions,
                output_file=output,
                operation=lambda session: issue_beta_api_key(
                    session,
                    organization_id=organization_id,
                    service_account_id=account_id,
                    label="must roll back",
                    scopes={"jobs:read"},
                    expires_at=datetime.now(UTC) + timedelta(days=30),
                ),
            )
    finally:
        monkeypatch.setattr(operator_script.os, failure_point, real_operation)

    assert not output.exists()
    with Session(db_engine) as session:
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(ApiKey)
                .where(ApiKey.service_account_id == account_id)
            )
            == 0
        )
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.organization_id == organization_id,
                    AuditEvent.action == "operator.api_key.issued",
                )
            )
            == 0
        )
