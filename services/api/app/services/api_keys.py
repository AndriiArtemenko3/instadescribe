"""Issue and verify opaque integration API keys without storing plaintext."""

import hmac
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.tenancy import PrincipalContext
from app.models import (
    ApiKey,
    Organization,
    OrganizationMembership,
    Principal,
    ServiceAccount,
)

API_KEY_SCOPES = frozenset(
    {
        "organization:read",
        "projects:read",
        "projects:write",
        "jobs:read",
        "jobs:write",
        "deliverables:read",
    }
)
_TOKEN_RE = re.compile(r"^idsb_live_([a-f0-9]{12})\.([A-Za-z0-9_-]{43,64})$")
_DIGEST_RE = re.compile(r"^[a-f0-9]{64}$")
_CURRENT_DIGEST_VERSION = 1


class ApiKeyConfigurationError(Exception):
    """Integration key authentication is not safely configured."""


class ApiKeyLimitError(Exception):
    """A beta service account already has both overlap-rotation keys."""


@dataclass(frozen=True, slots=True)
class IssuedApiKey:
    record: ApiKey
    token: str


def _configured_pepper(override: str | None = None) -> bytes:
    value = override if override is not None else get_settings().integration_api_key_pepper
    if value is None or len(value.encode("utf-8")) < 32:
        raise ApiKeyConfigurationError
    return value.encode("utf-8")


def _secret_digest(secret: str, pepper: bytes, version: int) -> str:
    if version != _CURRENT_DIGEST_VERSION:
        raise ApiKeyConfigurationError
    return hmac.new(pepper, secret.encode("utf-8"), "sha256").hexdigest()


def api_key_token_shape_valid(token: str) -> bool:
    return _TOKEN_RE.fullmatch(token) is not None


def create_service_account(
    session: Session,
    organization_id: uuid.UUID,
    *,
    name: str,
) -> ServiceAccount:
    """Create the principal, tenant membership and service account atomically.

    The caller owns the surrounding transaction and may add an initial key
    before committing.
    """
    principal = Principal(kind="service_account", display_name=name)
    session.add(principal)
    session.flush()
    session.add(
        OrganizationMembership(
            organization_id=organization_id,
            principal_id=principal.id,
            role="service",
        )
    )
    session.flush()
    account = ServiceAccount(
        organization_id=organization_id,
        principal_id=principal.id,
        name=name,
    )
    session.add(account)
    session.flush()
    return account


def issue_api_key(
    session: Session,
    service_account: ServiceAccount,
    *,
    label: str,
    scopes: set[str] | frozenset[str] = API_KEY_SCOPES,
    expires_at: datetime | None = None,
    pepper: str | None = None,
) -> IssuedApiKey:
    unknown = set(scopes) - API_KEY_SCOPES - {"*"}
    if unknown:
        raise ValueError("unknown API key scope")
    pepper_bytes = _configured_pepper(pepper)
    # Serialize issue/rotation for this service account. Beta intentionally
    # allows two live keys so a customer can overlap rotation without growing
    # an unbounded credential set.
    locked_account = session.execute(
        sa.select(ServiceAccount.id)
        .where(ServiceAccount.id == service_account.id)
        .with_for_update()
    ).scalar_one_or_none()
    if locked_account is None:
        raise ValueError("service account does not exist")
    now = datetime.now(UTC)
    active_keys = session.execute(
        sa.select(sa.func.count())
        .select_from(ApiKey)
        .where(
            ApiKey.service_account_id == service_account.id,
            ApiKey.revoked_at.is_(None),
            sa.or_(ApiKey.expires_at.is_(None), ApiKey.expires_at > now),
        )
    ).scalar_one()
    if active_keys >= 2:
        raise ApiKeyLimitError
    prefix = secrets.token_hex(6)
    secret = secrets.token_urlsafe(32)
    record = ApiKey(
        service_account_id=service_account.id,
        label=label,
        key_prefix=prefix,
        digest_version=_CURRENT_DIGEST_VERSION,
        secret_digest=_secret_digest(secret, pepper_bytes, _CURRENT_DIGEST_VERSION),
        scopes=sorted(scopes),
        expires_at=expires_at,
    )
    session.add(record)
    session.flush()
    return IssuedApiKey(record=record, token=f"idsb_live_{prefix}.{secret}")


def revoke_api_key(
    session: Session,
    service_account_id: uuid.UUID,
    api_key_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> bool:
    """Revoke one key without affecting an overlapping replacement."""

    result = session.execute(
        sa.update(ApiKey)
        .where(
            ApiKey.id == api_key_id,
            ApiKey.service_account_id == service_account_id,
            ApiKey.revoked_at.is_(None),
        )
        .values(revoked_at=now or datetime.now(UTC))
    )
    session.flush()
    return result.rowcount > 0


def verify_api_key(session: Session, token: str) -> PrincipalContext | None:
    pepper = _configured_pepper()
    match = _TOKEN_RE.fullmatch(token)
    if match is None:
        hmac.compare_digest(
            _secret_digest(token[:64], pepper, _CURRENT_DIGEST_VERSION),
            "0" * 64,
        )
        return None
    prefix, secret = match.groups()
    row = session.execute(
        sa.select(ApiKey, ServiceAccount, Organization, OrganizationMembership, Principal)
        .join(ServiceAccount, ApiKey.service_account_id == ServiceAccount.id)
        .join(Organization, ServiceAccount.organization_id == Organization.id)
        .join(
            OrganizationMembership,
            sa.and_(
                OrganizationMembership.organization_id == ServiceAccount.organization_id,
                OrganizationMembership.principal_id == ServiceAccount.principal_id,
            ),
        )
        .join(Principal, ServiceAccount.principal_id == Principal.id)
        .where(ApiKey.key_prefix == prefix)
    ).one_or_none()
    if row is None:
        hmac.compare_digest(
            _secret_digest(secret, pepper, _CURRENT_DIGEST_VERSION),
            "0" * 64,
        )
        return None
    key, account, organization, membership, principal = row
    now = datetime.now(UTC)
    if (
        key.revoked_at is not None
        or (key.expires_at is not None and key.expires_at <= now)
        or not account.is_active
        or not organization.is_active
        or not membership.is_active
        or not principal.is_active
        or principal.kind != "service_account"
        or key.digest_version != _CURRENT_DIGEST_VERSION
        or not _DIGEST_RE.fullmatch(key.secret_digest)
        or not isinstance(key.scopes, list)
        or any(not isinstance(scope, str) for scope in key.scopes)
    ):
        return None
    supplied = _secret_digest(secret, pepper, key.digest_version)
    if not hmac.compare_digest(supplied, key.secret_digest):
        return None
    return PrincipalContext(
        organization_id=organization.id,
        principal_id=principal.id,
        principal_type="service_account",
        scopes=frozenset(key.scopes),
    )
