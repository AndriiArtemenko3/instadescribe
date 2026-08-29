"""Bounded Cognito provisioning used only by the owner invitation route."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import get_settings


class CognitoInvitationUnavailable(Exception):
    pass


class CognitoInvitationConflict(Exception):
    pass


@dataclass(frozen=True, slots=True)
class CognitoInvitationResult:
    subject: str
    username: str


def _user_pool_id() -> str:
    value = (get_settings().cognito_user_pool_id or "").strip()
    if not 1 <= len(value) <= 128 or any(
        not (character.isascii() and (character.isalnum() or character in "_-"))
        for character in value
    ):
        raise CognitoInvitationUnavailable
    return value


@lru_cache
def _client():
    settings = get_settings()
    return boto3.client(
        "cognito-idp",
        region_name=settings.aws_region,
        config=Config(
            connect_timeout=2,
            read_timeout=5,
            retries={"max_attempts": 1, "mode": "standard"},
        ),
    )


def _provider_error_code(error: ClientError) -> str:
    response = error.response if isinstance(error.response, dict) else {}
    payload = response.get("Error") if isinstance(response, dict) else None
    return str(payload.get("Code", "")) if isinstance(payload, dict) else ""


def _bounded_text(value: Any, *, maximum: int) -> str | None:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        return None
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        return None
    return value


def _attributes(user: dict[str, Any]) -> dict[str, str]:
    raw = user.get("Attributes", user.get("UserAttributes"))
    if not isinstance(raw, list):
        return {}
    result: dict[str, str] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = _bounded_text(item.get("Name"), maximum=128)
        value = _bounded_text(item.get("Value"), maximum=2048)
        if name is not None and value is not None:
            result[name] = value
    return result


def _validated_user(
    user: dict[str, Any],
    *,
    email: str,
    invitation_id: uuid.UUID,
) -> CognitoInvitationResult | None:
    username = _bounded_text(user.get("Username"), maximum=255)
    attributes = _attributes(user)
    subject = _bounded_text(attributes.get("sub"), maximum=255)
    returned_email = _bounded_text(attributes.get("email"), maximum=254)
    marker = _bounded_text(attributes.get("custom:invitation_id"), maximum=36)
    if (
        username is None
        or subject is None
        or returned_email is None
        or returned_email.strip().casefold() != email
        or marker != str(invitation_id)
    ):
        return None
    return CognitoInvitationResult(subject=subject, username=username)


def _recover_created_user(email: str, invitation_id: uuid.UUID) -> CognitoInvitationResult:
    try:
        user = _client().admin_get_user(UserPoolId=_user_pool_id(), Username=email)
    except (BotoCoreError, ClientError, TimeoutError, CognitoInvitationUnavailable):
        raise CognitoInvitationUnavailable from None
    if not isinstance(user, dict):
        raise CognitoInvitationUnavailable
    recovered = _validated_user(user, email=email, invitation_id=invitation_id)
    if recovered is None:
        # Exact marker comparison distinguishes a crash-retry from an account
        # that predated (or belongs outside) this invitation operation.
        raise CognitoInvitationConflict
    return recovered


def provision_invited_user(email: str, invitation_id: uuid.UUID) -> CognitoInvitationResult:
    """Create or safely reconcile one exactly marked Cognito invitation."""

    pool_id = _user_pool_id()
    try:
        response = _client().admin_create_user(
            UserPoolId=pool_id,
            Username=email,
            UserAttributes=[
                {"Name": "email", "Value": email},
                {"Name": "custom:invitation_id", "Value": str(invitation_id)},
            ],
            DesiredDeliveryMediums=["EMAIL"],
        )
    except ClientError as exc:
        if _provider_error_code(exc) in {
            "AliasExistsException",
            "UsernameExistsException",
        }:
            return _recover_created_user(email, invitation_id)
        raise CognitoInvitationUnavailable from None
    except (BotoCoreError, TimeoutError):
        raise CognitoInvitationUnavailable from None

    user = response.get("User") if isinstance(response, dict) else None
    if not isinstance(user, dict):
        raise CognitoInvitationUnavailable
    provisioned = _validated_user(user, email=email, invitation_id=invitation_id)
    if provisioned is None:
        username = _bounded_text(user.get("Username"), maximum=255)
        if username is not None:
            compensate_invited_user(username)
        raise CognitoInvitationUnavailable
    return provisioned


def compensate_invited_user(username: str) -> bool:
    """Best-effort removal after local activation fails; never raises."""

    try:
        _client().admin_delete_user(UserPoolId=_user_pool_id(), Username=username)
    except (BotoCoreError, ClientError, TimeoutError, CognitoInvitationUnavailable):
        return False
    return True


def reset_cognito_invitation_cache() -> None:
    _client.cache_clear()
