"""Bounded database operations for the manual beta operator.

This module deliberately owns no command-line parsing, logging, commits or
provider-side identity mutation.  The caller supplies one transaction and, for
the initial owner only, a Cognito subject recovered from a pre-existing user
whose immutable bootstrap marker was verified by :func:`discover_marked_owner`.

Audit details are fixed rather than caller-controlled so credentials, endpoint
URLs, identity attributes and secret references cannot enter the audit stream.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import sqlalchemy as sa
from sqlalchemy.orm import Session

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
from app.services.api_keys import (
    API_KEY_SCOPES,
    IssuedApiKey,
    create_service_account,
    issue_api_key,
    revoke_api_key,
)
from app.services.webhooks import WebhookContractError, validate_endpoint_url

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SECRETS_MANAGER_ARN_RE = re.compile(
    r"^arn:(?:aws|aws-cn|aws-us-gov):secretsmanager:"
    r"[a-z0-9-]{3,32}:[0-9]{12}:secret:[A-Za-z0-9/_+=.@-]{1,380}$"
)
_ALLOWED_OWNER_STATES = frozenset({"FORCE_CHANGE_PASSWORD", "CONFIRMED"})
_AUDIT_DETAILS = {"outcome": "succeeded"}


class BetaOperatorError(ValueError):
    """Stable operator failure that never embeds supplied or provider data."""


class OperatorConflict(BetaOperatorError):
    """The requested mutation conflicts with durable state."""


class OperatorNotFound(BetaOperatorError):
    """The exact tenant-owned resource was not found."""


class OwnerIdentityUnavailable(BetaOperatorError):
    """A marked, enabled Cognito owner could not be proven read-only."""


@dataclass(frozen=True, slots=True)
class DiscoveredOwner:
    subject: str


@dataclass(frozen=True, slots=True)
class CreatedOrganization:
    organization_id: uuid.UUID
    owner_principal_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class WebhookConfiguration:
    endpoint_id: uuid.UUID
    secret_version: int
    changed: bool


@dataclass(frozen=True, slots=True)
class SuspensionResult:
    organization_id: uuid.UUID
    changed: bool


def _bounded_text(value: object, *, maximum: int, field: str) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or value != value.strip()
        or any(not character.isprintable() for character in value)
    ):
        raise BetaOperatorError(f"{field} is invalid")
    return value


def _canonical_email(value: object) -> str:
    email = _bounded_text(value, maximum=254, field="owner email").casefold()
    if email.count("@") != 1:
        raise BetaOperatorError("owner email is invalid")
    local, domain = email.split("@", 1)
    if not local or not domain or any(character.isspace() for character in email):
        raise BetaOperatorError("owner email is invalid")
    return email


def _attribute_values(response: dict[str, Any], name: str) -> list[str]:
    raw_attributes = response.get("UserAttributes")
    if not isinstance(raw_attributes, list) or len(raw_attributes) > 100:
        return []
    values: list[str] = []
    for item in raw_attributes:
        if not isinstance(item, dict) or item.get("Name") != name:
            continue
        value = item.get("Value")
        if isinstance(value, str):
            values.append(value)
    return values


def discover_marked_owner(
    client: Any,
    *,
    user_pool_id: str,
    email: str,
    bootstrap_id: uuid.UUID,
) -> DiscoveredOwner:
    """Resolve one pre-created Cognito user without mutating the provider.

    The immutable ``custom:invitation_id`` attribute is the adoption guard: an
    email match alone is never sufficient to bind an existing account as an
    organization owner.
    """

    pool_id = _bounded_text(user_pool_id, maximum=128, field="Cognito user pool ID")
    if any(
        not (character.isascii() and (character.isalnum() or character in "_-"))
        for character in pool_id
    ):
        raise BetaOperatorError("Cognito user pool ID is invalid")
    owner_email = _canonical_email(email)
    if not isinstance(bootstrap_id, uuid.UUID):
        raise BetaOperatorError("owner bootstrap ID is invalid")

    try:
        response = client.admin_get_user(UserPoolId=pool_id, Username=owner_email)
    except Exception:
        # Provider messages can contain identity attributes.  Preserve only the
        # stable failure class at the operator boundary.
        raise OwnerIdentityUnavailable("owner Cognito identity could not be verified") from None
    if (
        not isinstance(response, dict)
        or response.get("Enabled") is not True
        or response.get("UserStatus") not in _ALLOWED_OWNER_STATES
    ):
        raise OwnerIdentityUnavailable("owner Cognito identity could not be verified")

    subjects = _attribute_values(response, "sub")
    emails = _attribute_values(response, "email")
    markers = _attribute_values(response, "custom:invitation_id")
    if len(subjects) != 1 or len(emails) != 1 or len(markers) != 1:
        raise OwnerIdentityUnavailable("owner Cognito identity could not be verified")
    try:
        subject = _bounded_text(subjects[0], maximum=255, field="owner subject")
        returned_email = _canonical_email(emails[0])
    except BetaOperatorError:
        raise OwnerIdentityUnavailable("owner Cognito identity could not be verified") from None
    if returned_email != owner_email or markers[0] != str(bootstrap_id):
        raise OwnerIdentityUnavailable("owner Cognito identity could not be verified")
    return DiscoveredOwner(subject=subject)


def _append_operator_audit(
    session: Session,
    *,
    organization_id: uuid.UUID,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID,
) -> AuditEvent:
    event = AuditEvent(
        organization_id=organization_id,
        actor_principal_id=None,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id),
        request_id=None,
        details=dict(_AUDIT_DETAILS),
    )
    session.add(event)
    return event


def create_organization_with_owner(
    session: Session,
    *,
    slug: str,
    name: str,
    owner_subject: str,
    owner_display_name: str,
) -> CreatedOrganization:
    """Stage a complete local tenant bootstrap in the caller's transaction."""

    organization_slug = _bounded_text(slug, maximum=80, field="organization slug")
    if _SLUG_RE.fullmatch(organization_slug) is None:
        raise BetaOperatorError("organization slug is invalid")
    organization_name = _bounded_text(name, maximum=200, field="organization name")
    display_name = _bounded_text(owner_display_name, maximum=200, field="owner display name")
    subject = _bounded_text(owner_subject, maximum=255, field="owner subject")

    conflict = session.execute(
        sa.select(sa.literal(True)).where(
            sa.or_(
                sa.exists().where(Organization.slug == organization_slug),
                sa.exists().where(Principal.external_subject == subject),
            )
        )
    ).scalar_one_or_none()
    if conflict:
        raise OperatorConflict("organization or owner binding already exists")

    organization = Organization(slug=organization_slug, name=organization_name)
    session.add(organization)
    session.flush()
    owner = Principal(
        kind="human",
        display_name=display_name,
        external_subject=subject,
    )
    session.add(owner)
    session.flush()
    session.add(
        OrganizationMembership(
            organization_id=organization.id,
            principal_id=owner.id,
            role="owner",
        )
    )
    # New organizations are not covered by the migration's one-time backfill.
    # These rows are required before the first upload/job mutation.
    session.add(OrganizationQuota(organization_id=organization.id))
    session.add(OrganizationJobCapacity(organization_id=organization.id))
    session.flush()
    _append_operator_audit(
        session,
        organization_id=organization.id,
        action="operator.organization.created",
        resource_type="organization",
        resource_id=organization.id,
    )
    _append_operator_audit(
        session,
        organization_id=organization.id,
        action="operator.owner.bound",
        resource_type="principal",
        resource_id=owner.id,
    )
    session.flush()
    return CreatedOrganization(
        organization_id=organization.id,
        owner_principal_id=owner.id,
    )


def _organization(
    session: Session,
    organization_id: uuid.UUID,
    *,
    require_active: bool,
) -> Organization:
    statement = sa.select(Organization).where(Organization.id == organization_id).with_for_update()
    organization = session.execute(statement).scalar_one_or_none()
    if organization is None:
        raise OperatorNotFound("organization was not found")
    if require_active and not organization.is_active:
        raise OperatorConflict("organization is suspended")
    return organization


def create_beta_service_account(
    session: Session,
    *,
    organization_id: uuid.UUID,
    name: str,
) -> ServiceAccount:
    account_name = _bounded_text(name, maximum=200, field="service account name")
    _organization(session, organization_id, require_active=True)
    exists = session.execute(
        sa.select(ServiceAccount.id).where(
            ServiceAccount.organization_id == organization_id,
            ServiceAccount.name == account_name,
        )
    ).scalar_one_or_none()
    if exists is not None:
        raise OperatorConflict("service account already exists")
    account = create_service_account(session, organization_id, name=account_name)
    _append_operator_audit(
        session,
        organization_id=organization_id,
        action="operator.service_account.created",
        resource_type="service_account",
        resource_id=account.id,
    )
    session.flush()
    return account


def _service_account(
    session: Session,
    *,
    organization_id: uuid.UUID,
    service_account_id: uuid.UUID,
    require_active: bool,
) -> ServiceAccount:
    row = session.execute(
        sa.select(ServiceAccount, OrganizationMembership, Principal)
        .join(
            OrganizationMembership,
            sa.and_(
                OrganizationMembership.organization_id == ServiceAccount.organization_id,
                OrganizationMembership.principal_id == ServiceAccount.principal_id,
            ),
        )
        .join(Principal, Principal.id == ServiceAccount.principal_id)
        .where(
            ServiceAccount.id == service_account_id,
            ServiceAccount.organization_id == organization_id,
        )
        .with_for_update()
    ).one_or_none()
    if row is None:
        raise OperatorNotFound("service account was not found")
    account, membership, principal = row
    if membership.role != "service" or principal.kind != "service_account":
        raise OperatorConflict("service account binding is invalid")
    if require_active and not (account.is_active and membership.is_active and principal.is_active):
        raise OperatorConflict("service account is inactive")
    return account


def _validated_key_inputs(
    *,
    label: str,
    scopes: Iterable[str],
    expires_at: datetime,
) -> tuple[str, frozenset[str], datetime]:
    key_label = _bounded_text(label, maximum=120, field="API key label")
    key_scopes = frozenset(scopes)
    if not key_scopes or not key_scopes.issubset(API_KEY_SCOPES):
        raise BetaOperatorError("API key scopes are invalid")
    if expires_at.tzinfo is None or expires_at.utcoffset() is None:
        raise BetaOperatorError("API key expiry must be timezone-aware")
    expiry = expires_at.astimezone(UTC)
    now = datetime.now(UTC)
    if expiry <= now + timedelta(hours=1) or expiry > now + timedelta(days=366):
        raise BetaOperatorError("API key expiry is outside the beta bound")
    return key_label, key_scopes, expiry


def issue_beta_api_key(
    session: Session,
    *,
    organization_id: uuid.UUID,
    service_account_id: uuid.UUID,
    label: str,
    scopes: Iterable[str],
    expires_at: datetime,
) -> IssuedApiKey:
    _organization(session, organization_id, require_active=True)
    account = _service_account(
        session,
        organization_id=organization_id,
        service_account_id=service_account_id,
        require_active=True,
    )
    key_label, key_scopes, expiry = _validated_key_inputs(
        label=label,
        scopes=scopes,
        expires_at=expires_at,
    )
    issued = issue_api_key(
        session,
        account,
        label=key_label,
        scopes=key_scopes,
        expires_at=expiry,
    )
    _append_operator_audit(
        session,
        organization_id=organization_id,
        action="operator.api_key.issued",
        resource_type="api_key",
        resource_id=issued.record.id,
    )
    session.flush()
    return issued


def rotate_beta_api_key(
    session: Session,
    *,
    organization_id: uuid.UUID,
    service_account_id: uuid.UUID,
    current_api_key_id: uuid.UUID,
    label: str,
    scopes: Iterable[str],
    expires_at: datetime,
) -> IssuedApiKey:
    """Issue one overlap key; the named current key remains live until revoke."""

    _organization(session, organization_id, require_active=True)
    account = _service_account(
        session,
        organization_id=organization_id,
        service_account_id=service_account_id,
        require_active=True,
    )
    current = session.execute(
        sa.select(ApiKey)
        .where(
            ApiKey.id == current_api_key_id,
            ApiKey.service_account_id == account.id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if current is None or current.revoked_at is not None:
        raise OperatorNotFound("current API key was not found")
    if current.expires_at is not None and current.expires_at <= now:
        raise OperatorConflict("current API key is expired")
    key_label, key_scopes, expiry = _validated_key_inputs(
        label=label,
        scopes=scopes,
        expires_at=expires_at,
    )
    issued = issue_api_key(
        session,
        account,
        label=key_label,
        scopes=key_scopes,
        expires_at=expiry,
    )
    _append_operator_audit(
        session,
        organization_id=organization_id,
        action="operator.api_key.rotated",
        resource_type="api_key",
        resource_id=issued.record.id,
    )
    session.flush()
    return issued


def revoke_beta_api_key(
    session: Session,
    *,
    organization_id: uuid.UUID,
    service_account_id: uuid.UUID,
    api_key_id: uuid.UUID,
    now: datetime | None = None,
) -> ApiKey:
    _organization(session, organization_id, require_active=False)
    account = _service_account(
        session,
        organization_id=organization_id,
        service_account_id=service_account_id,
        require_active=False,
    )
    key = session.execute(
        sa.select(ApiKey)
        .where(
            ApiKey.id == api_key_id,
            ApiKey.service_account_id == account.id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if key is None:
        raise OperatorNotFound("API key was not found")
    if key.revoked_at is not None:
        raise OperatorConflict("API key is already revoked")
    if not revoke_api_key(session, account.id, key.id, now=now):
        raise OperatorConflict("API key could not be revoked")
    session.refresh(key)
    _append_operator_audit(
        session,
        organization_id=organization_id,
        action="operator.api_key.revoked",
        resource_type="api_key",
        resource_id=key.id,
    )
    session.flush()
    return key


def _secret_reference(value: str) -> str:
    reference = _bounded_text(value, maximum=500, field="webhook signing secret reference")
    if "*" in reference or _SECRETS_MANAGER_ARN_RE.fullmatch(reference) is None:
        raise BetaOperatorError("webhook signing secret reference must be one exact ARN")
    return reference


def configure_beta_webhook(
    session: Session,
    *,
    organization_id: uuid.UUID,
    endpoint_url: str,
    signing_secret_ref: str,
    allowed_hosts: Iterable[str],
) -> WebhookConfiguration:
    _organization(session, organization_id, require_active=True)
    try:
        endpoint_value = validate_endpoint_url(endpoint_url, allowed_hosts=allowed_hosts)
    except WebhookContractError:
        raise BetaOperatorError("webhook endpoint is not an exact allowlisted HTTPS URL") from None
    # Query strings are a common accidental secret transport.  The beta accepts
    # one fixed path only; authentication belongs in the signing secret.
    if urlsplit(endpoint_value).query:
        raise BetaOperatorError("webhook endpoint query strings are forbidden")
    secret_reference = _secret_reference(signing_secret_ref)
    endpoint = session.execute(
        sa.select(WebhookEndpoint)
        .where(WebhookEndpoint.organization_id == organization_id)
        .with_for_update()
    ).scalar_one_or_none()
    if endpoint is None:
        endpoint = WebhookEndpoint(
            organization_id=organization_id,
            endpoint_url=endpoint_value,
            signing_secret_ref=secret_reference,
        )
        session.add(endpoint)
        session.flush()
        changed = True
    else:
        changed = (
            endpoint.endpoint_url != endpoint_value
            or endpoint.signing_secret_ref != secret_reference
            or not endpoint.is_active
        )
        if changed:
            endpoint.endpoint_url = endpoint_value
            endpoint.signing_secret_ref = secret_reference
            endpoint.secret_version += 1
            endpoint.is_active = True
            endpoint.disabled_at = None
            session.flush()
    if changed:
        _append_operator_audit(
            session,
            organization_id=organization_id,
            action="operator.webhook.configured",
            resource_type="webhook_endpoint",
            resource_id=endpoint.id,
        )
        session.flush()
    return WebhookConfiguration(
        endpoint_id=endpoint.id,
        secret_version=endpoint.secret_version,
        changed=changed,
    )


def suspend_beta_organization(
    session: Session,
    *,
    organization_id: uuid.UUID,
    confirmed_slug: str,
    now: datetime | None = None,
) -> SuspensionResult:
    """Disable tenant authentication and stop new webhook claims atomically."""

    organization = _organization(session, organization_id, require_active=False)
    if confirmed_slug != organization.slug:
        raise BetaOperatorError("organization slug confirmation did not match")
    endpoint = session.execute(
        sa.select(WebhookEndpoint)
        .where(WebhookEndpoint.organization_id == organization_id)
        .with_for_update()
    ).scalar_one_or_none()
    changed = organization.is_active or (endpoint is not None and endpoint.is_active)
    if changed:
        instant = now or datetime.now(UTC)
        organization.is_active = False
        if endpoint is not None and endpoint.is_active:
            endpoint.is_active = False
            endpoint.disabled_at = instant
        _append_operator_audit(
            session,
            organization_id=organization_id,
            action="operator.organization.suspended",
            resource_type="organization",
            resource_id=organization_id,
        )
        session.flush()
    return SuspensionResult(organization_id=organization_id, changed=changed)
