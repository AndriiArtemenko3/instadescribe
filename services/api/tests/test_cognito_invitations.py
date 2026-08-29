"""Provider-bound invitation reconciliation and compensation tests."""

from __future__ import annotations

import uuid
from unittest.mock import Mock

import pytest
from app.services import cognito_invitations
from app.services.cognito_invitations import CognitoInvitationConflict
from botocore.exceptions import ClientError


def _attributes(email: str, invitation_id: uuid.UUID):
    return [
        {"Name": "sub", "Value": "provider-subject"},
        {"Name": "email", "Value": email},
        {"Name": "custom:invitation_id", "Value": str(invitation_id)},
    ]


def _exists() -> ClientError:
    return ClientError(
        {"Error": {"Code": "UsernameExistsException", "Message": "hidden"}},
        "AdminCreateUser",
    )


def test_username_exists_recovers_only_the_exact_durable_invitation_marker(monkeypatch):
    invitation_id = uuid.uuid4()
    client = Mock()
    client.admin_create_user.side_effect = _exists()
    client.admin_get_user.return_value = {
        "Username": "provider-user",
        "UserAttributes": _attributes("person@example.com", invitation_id),
    }
    monkeypatch.setattr(cognito_invitations, "_client", lambda: client)
    monkeypatch.setattr(cognito_invitations, "_user_pool_id", lambda: "eu-west-2_test")

    result = cognito_invitations.provision_invited_user("person@example.com", invitation_id)

    assert result.subject == "provider-subject"
    assert result.username == "provider-user"
    client.admin_get_user.assert_called_once_with(
        UserPoolId="eu-west-2_test", Username="person@example.com"
    )


def test_username_exists_with_foreign_marker_is_enumeration_safe_conflict(monkeypatch):
    invitation_id = uuid.uuid4()
    client = Mock()
    client.admin_create_user.side_effect = _exists()
    client.admin_get_user.return_value = {
        "Username": "provider-user",
        "UserAttributes": _attributes("person@example.com", uuid.uuid4()),
    }
    monkeypatch.setattr(cognito_invitations, "_client", lambda: client)
    monkeypatch.setattr(cognito_invitations, "_user_pool_id", lambda: "eu-west-2_test")

    with pytest.raises(CognitoInvitationConflict):
        cognito_invitations.provision_invited_user("person@example.com", invitation_id)


def test_malformed_create_response_is_compensated(monkeypatch):
    invitation_id = uuid.uuid4()
    client = Mock()
    client.admin_create_user.return_value = {
        "User": {
            "Username": "provider-user",
            "Attributes": _attributes("different@example.com", invitation_id),
        }
    }
    monkeypatch.setattr(cognito_invitations, "_client", lambda: client)
    monkeypatch.setattr(cognito_invitations, "_user_pool_id", lambda: "eu-west-2_test")

    with pytest.raises(cognito_invitations.CognitoInvitationUnavailable):
        cognito_invitations.provision_invited_user("person@example.com", invitation_id)
    client.admin_delete_user.assert_called_once_with(
        UserPoolId="eu-west-2_test", Username="provider-user"
    )
