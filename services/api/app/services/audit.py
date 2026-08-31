"""Strict append-only audit records for successful tenant mutations.

The helper deliberately accepts no arbitrary details. This keeps request
bodies, credentials, media identity, prompts and network metadata outside the
audit table by construction while allowing the caller's transaction to own the
single commit or rollback.
"""

from __future__ import annotations

import re
import uuid
from typing import Literal

from sqlalchemy.orm import Session

from app.core.tenancy import PrincipalContext
from app.models import AuditEvent

AuditAction = Literal[
    "project.created",
    "project.updated",
    "job.created",
    "job.upload_completed",
    "job.cancelled",
    "scene.updated",
    "review.finished",
    "member.invited",
    "tts_preview.created",
    "investigation.created",
    "investigation.cancelled",
    "investigation.finalized",
]

_RESOURCE_BY_ACTION: dict[str, str] = {
    "project.created": "project",
    "project.updated": "project",
    "job.created": "job",
    "job.upload_completed": "job",
    "job.cancelled": "job",
    "scene.updated": "scene",
    "review.finished": "review",
    "member.invited": "invitation",
    "tts_preview.created": "tts_preview",
    "investigation.created": "investigation",
    "investigation.cancelled": "investigation",
    "investigation.finalized": "investigation",
}
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")


def bounded_request_id(value: object) -> str | None:
    """Keep only a server-established, bounded opaque request identifier."""

    return value if isinstance(value, str) and _REQUEST_ID_RE.fullmatch(value) else None


def append_succeeded(
    session: Session,
    principal: PrincipalContext,
    *,
    action: AuditAction,
    resource_id: uuid.UUID,
    request_id: object = None,
) -> AuditEvent:
    """Stage one sanitized success event in the caller's current transaction."""

    resource_type = _RESOURCE_BY_ACTION.get(action)
    if resource_type is None:
        raise ValueError("unsupported audit action")
    if not isinstance(resource_id, uuid.UUID):
        raise ValueError("audit resource_id must be a UUID")
    event = AuditEvent(
        organization_id=principal.organization_id,
        actor_principal_id=principal.principal_id,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id),
        request_id=bounded_request_id(request_id),
        details={"outcome": "succeeded"},
    )
    session.add(event)
    return event
