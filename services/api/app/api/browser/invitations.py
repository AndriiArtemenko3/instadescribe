"""Owner-only, MFA-gated organization participant invitations."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.browser.auth import BrowserPrincipal, require_browser_roles
from app.api.integrations.problems import IntegrationProblem
from app.core.tenancy import PrincipalContext
from app.db.session import get_db
from app.models import OrganizationInvitation, OrganizationMembership, Principal
from app.schemas.browser import BrowserInvitationRequest, BrowserInvitationResponse
from app.services.audit import append_succeeded
from app.services.cognito_invitations import (
    CognitoInvitationConflict,
    CognitoInvitationUnavailable,
    compensate_invited_user,
    provision_invited_user,
)

router = APIRouter(prefix="/api/app/v1", tags=["browser-app"])
BrowserOwnerPrincipal = Annotated[
    BrowserPrincipal,
    Depends(require_browser_roles("owner")),
]
InvitationIdempotencyKey = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=255),
]


def _conflict() -> IntegrationProblem:
    return IntegrationProblem(
        409,
        "invitation_conflict",
        "Invitation conflict",
        "The invitation could not be completed.",
    )


def _unavailable() -> IntegrationProblem:
    return IntegrationProblem(
        503,
        "invitation_unavailable",
        "Invitation unavailable",
        "The invitation is temporarily unavailable.",
        retryable=True,
    )


def _request_hash(payload: BrowserInvitationRequest) -> str:
    body = json.dumps(
        {"email": payload.email, "role": payload.role},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(body).hexdigest()


def _response(invitation: OrganizationInvitation, *, replayed: bool, status: int) -> JSONResponse:
    body = BrowserInvitationResponse(
        invitationId=str(invitation.id),
        email=invitation.email,
        role=invitation.role,
        state="active",
    ).model_dump(mode="json", by_alias=True)
    headers = {"Cache-Control": "private, no-store"}
    if replayed:
        headers["Idempotent-Replayed"] = "true"
    return JSONResponse(status_code=status, content=body, headers=headers)


def _resolve_existing(
    db: Session,
    principal: BrowserPrincipal,
    payload: BrowserInvitationRequest,
    idempotency_key: str,
    request_hash: str,
    *,
    lock: bool = False,
) -> OrganizationInvitation | None:
    statement = sa.select(OrganizationInvitation).where(
        OrganizationInvitation.organization_id == principal.organization_id,
        sa.or_(
            OrganizationInvitation.idempotency_key == idempotency_key,
            OrganizationInvitation.email == payload.email,
        ),
    )
    if lock:
        statement = statement.with_for_update()
    invitations = db.execute(statement).scalars().all()
    if len(invitations) > 1:
        raise _conflict()
    if not invitations:
        return None
    invitation = invitations[0]
    if invitation.email != payload.email or invitation.role != payload.role:
        raise _conflict()
    if invitation.idempotency_key == idempotency_key and invitation.request_hash != request_hash:
        raise _conflict()
    return invitation


def _persist_pending(
    db: Session,
    principal: BrowserPrincipal,
    payload: BrowserInvitationRequest,
    idempotency_key: str,
    request_hash: str,
) -> tuple[OrganizationInvitation, bool]:
    invite_id = uuid.uuid4()
    invited_principal = Principal(
        kind="human",
        display_name=(payload.email.split("@", 1)[0] or "Invited member")[:200],
        external_subject=None,
        is_active=False,
    )
    try:
        db.add(invited_principal)
        db.flush()
        db.add(
            OrganizationMembership(
                organization_id=principal.organization_id,
                principal_id=invited_principal.id,
                role=payload.role,
                is_active=False,
            )
        )
        # The invitation's composite FK targets this exact tenant membership.
        # Flush it first because the models intentionally have no ORM relationship
        # that would otherwise establish unit-of-work object ordering.
        db.flush()
        invitation = OrganizationInvitation(
            id=invite_id,
            organization_id=principal.organization_id,
            principal_id=invited_principal.id,
            invited_by_principal_id=principal.principal_id,
            email=payload.email,
            role=payload.role,
            state="pending",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        db.add(invitation)
        db.commit()
    except IntegrityError:
        db.rollback()
        try:
            existing = _resolve_existing(
                db,
                principal,
                payload,
                idempotency_key,
                request_hash,
            )
        except SQLAlchemyError:
            raise _unavailable() from None
        if existing is None:
            # The beta browser assertion has no organization selector, so a
            # canonical email may belong to only one tenant. Do not expose
            # whether the collision is Cognito-, tenant-, or email-related.
            try:
                foreign_email_exists = db.execute(
                    sa.select(sa.literal(True)).where(
                        sa.exists().where(OrganizationInvitation.email == payload.email)
                    )
                ).scalar_one_or_none()
            except SQLAlchemyError:
                raise _unavailable() from None
            if foreign_email_exists:
                raise _conflict() from None
            raise _unavailable() from None
        return existing, False
    except SQLAlchemyError:
        db.rollback()
        raise _unavailable() from None
    return invitation, True


@router.post(
    "/organization/invitations",
    status_code=201,
    response_model=BrowserInvitationResponse,
    response_model_by_alias=True,
    responses={200: {"model": BrowserInvitationResponse, "description": "Idempotent replay"}},
    operation_id="inviteBrowserOrganizationMember",
)
def invite_organization_member(
    payload: BrowserInvitationRequest,
    request: Request,
    principal: BrowserOwnerPrincipal,
    idempotency_key: InvitationIdempotencyKey,
    db: Session = Depends(get_db),
):
    if any(ord(character) < 0x21 or ord(character) > 0x7E for character in idempotency_key):
        raise IntegrationProblem(
            422,
            "invalid_idempotency_key",
            "Invalid request",
            "The idempotency key is invalid.",
        )
    fingerprint = _request_hash(payload)
    try:
        invitation = _resolve_existing(
            db,
            principal,
            payload,
            idempotency_key,
            fingerprint,
        )
    except SQLAlchemyError:
        raise _unavailable() from None
    if invitation is None:
        invitation, created = _persist_pending(
            db,
            principal,
            payload,
            idempotency_key,
            fingerprint,
        )
    else:
        created = False
    if invitation.state == "active":
        return _response(invitation, replayed=True, status=200)
    if invitation.state != "pending":
        raise _conflict()

    # Serialize provisioning attempts for this tenant invitation. The pending
    # row was committed before this bounded provider call, so every failure
    # leaves an inactive principal/membership that a safe retry can reuse.
    try:
        invitation = _resolve_existing(
            db,
            principal,
            payload,
            idempotency_key,
            fingerprint,
            lock=True,
        )
    except SQLAlchemyError:
        db.rollback()
        raise _unavailable() from None
    if invitation is None:
        db.rollback()
        raise _unavailable()
    if invitation.state == "active":
        db.rollback()
        return _response(invitation, replayed=True, status=200)
    if invitation.state != "pending":
        db.rollback()
        raise _conflict()

    try:
        provisioned = provision_invited_user(invitation.email, invitation.id)
    except CognitoInvitationConflict:
        invitation.state = "provider_conflict"
        try:
            db.commit()
        except SQLAlchemyError:
            db.rollback()
            raise _unavailable() from None
        raise _conflict() from None
    except CognitoInvitationUnavailable:
        db.rollback()
        raise _unavailable() from None

    invited_principal = db.get(Principal, invitation.principal_id)
    membership = db.execute(
        sa.select(OrganizationMembership).where(
            OrganizationMembership.organization_id == principal.organization_id,
            OrganizationMembership.principal_id == invitation.principal_id,
        )
    ).scalar_one_or_none()
    if (
        invited_principal is None
        or membership is None
        or invited_principal.is_active
        or membership.is_active
        or invited_principal.external_subject is not None
    ):
        db.rollback()
        compensate_invited_user(provisioned.username)
        raise _unavailable()

    invited_principal.external_subject = provisioned.subject
    invited_principal.is_active = True
    membership.is_active = True
    invitation.state = "active"
    invitation.cognito_username = provisioned.username
    invitation.activated_at = datetime.now(UTC)
    append_succeeded(
        db,
        PrincipalContext(
            organization_id=principal.organization_id,
            principal_id=principal.principal_id,
            principal_type="human",
            scopes=frozenset(),
        ),
        action="member.invited",
        resource_id=invitation.id,
        request_id=getattr(request.state, "request_id", None),
    )
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        compensate_invited_user(provisioned.username)
        raise _unavailable() from None
    return _response(invitation, replayed=not created, status=201 if created else 200)
