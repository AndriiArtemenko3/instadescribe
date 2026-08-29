"""Cognito bearer authentication plus authoritative local membership lookup."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Literal

import sqlalchemy as sa
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.identity import Organization, OrganizationMembership, Principal
from app.services.browser_assertion import (
    BrowserAssertionConfigurationUnavailable,
    BrowserAssertionInvalid,
    verify_browser_assertion,
)
from app.services.cognito_jwt import (
    CognitoConfigurationUnavailable,
    CognitoJwksUnavailable,
    CognitoTokenInvalid,
    verify_cognito_access_token,
)

BrowserRole = Literal["owner", "editor", "reviewer", "viewer"]
_HUMAN_ROLES = frozenset({"owner", "editor", "reviewer", "viewer"})
_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class BrowserPrincipal:
    subject: str
    email: str
    display_name: str
    organization_id: uuid.UUID
    organization_slug: str
    principal_id: uuid.UUID
    role: BrowserRole
    mfa_verified: bool


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="A valid Cognito access token is required.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def authenticate_browser_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: Session = Depends(get_db),
) -> BrowserPrincipal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized()
    try:
        claims = verify_cognito_access_token(credentials.credentials)
    except CognitoTokenInvalid:
        raise _unauthorized() from None
    except (CognitoConfigurationUnavailable, CognitoJwksUnavailable):
        raise HTTPException(
            status_code=503,
            detail="Browser authentication is temporarily unavailable.",
        ) from None
    assertion_values = request.headers.getlist("X-InstaDescribe-Browser-Assertion")
    try:
        assertion = verify_browser_assertion(
            credentials.credentials,
            assertion_values[0] if len(assertion_values) == 1 else None,
        )
    except BrowserAssertionInvalid:
        raise _unauthorized() from None
    except BrowserAssertionConfigurationUnavailable:
        raise HTTPException(
            status_code=503,
            detail="Browser authentication is temporarily unavailable.",
        ) from None

    statement = (
        sa.select(Principal, OrganizationMembership, Organization)
        .join(
            OrganizationMembership,
            OrganizationMembership.principal_id == Principal.id,
        )
        .join(Organization, Organization.id == OrganizationMembership.organization_id)
        .where(
            Principal.external_subject == claims.subject,
            Principal.kind == "human",
            Principal.is_active.is_(True),
            OrganizationMembership.is_active.is_(True),
            Organization.is_active.is_(True),
        )
    )
    try:
        rows = db.execute(statement).all()
    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail="Browser authentication is temporarily unavailable.",
        ) from None

    # Browser sessions are single-tenant. Zero or ambiguous active human
    # memberships fail closed; service/legacy identities can never cross this
    # boundary even if their external_subject was populated accidentally.
    if len(rows) != 1:
        raise HTTPException(status_code=403, detail="No active browser membership is available.")
    principal, membership, organization = rows[0]
    if membership.role not in _HUMAN_ROLES:
        raise HTTPException(status_code=403, detail="No active browser membership is available.")
    return BrowserPrincipal(
        subject=claims.subject,
        email=assertion.email,
        display_name=principal.display_name,
        organization_id=organization.id,
        organization_slug=organization.slug,
        principal_id=principal.id,
        role=membership.role,
        mfa_verified=assertion.mfa_verified,
    )


def require_browser_roles(
    *roles: BrowserRole,
) -> Callable[[BrowserPrincipal], BrowserPrincipal]:
    """Build a reusable role dependency for future upload/review mutations."""

    allowed = frozenset(roles)
    if not allowed or not allowed.issubset(_HUMAN_ROLES):
        raise ValueError("at least one human browser role is required")

    def dependency(
        principal: Annotated[BrowserPrincipal, Depends(require_browser_access_principal)],
    ) -> BrowserPrincipal:
        if principal.role not in allowed:
            raise HTTPException(
                status_code=403, detail="This membership cannot perform that action."
            )
        return principal

    return dependency


def require_browser_access_principal(
    principal: Annotated[BrowserPrincipal, Depends(authenticate_browser_principal)],
) -> BrowserPrincipal:
    """Require a human session eligible to reach tenant resources.

    ``GET /session`` may truthfully return ``mfaVerified: false`` so the BFF
    can fail closed. Every resource route additionally enforces the owner MFA
    boundary, preventing a caller from bypassing that BFF decision by calling
    the Browser API origin directly.
    """
    if principal.role == "owner" and not principal.mfa_verified:
        raise HTTPException(status_code=403, detail="Verified MFA is required for this owner.")
    return principal


require_browser_upload_principal = require_browser_roles("owner", "editor")
require_browser_scene_principal = require_browser_roles("owner", "editor", "reviewer")
require_browser_review_principal = require_browser_roles("owner", "reviewer")
