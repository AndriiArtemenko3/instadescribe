"""Opaque, stable keyset cursors for integration collections."""

import base64
import json
import uuid
from dataclasses import dataclass
from datetime import datetime

from app.api.integrations.problems import IntegrationProblem


@dataclass(frozen=True, slots=True)
class Cursor:
    created_at: datetime
    resource_id: uuid.UUID


def encode_cursor(created_at: datetime, resource_id: uuid.UUID) -> str:
    raw = json.dumps(
        {"createdAt": created_at.isoformat(), "id": str(resource_id)},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(value: str | None) -> Cursor | None:
    if value is None:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.b64decode(padded, altchars=b"-_", validate=True))
        if set(payload) != {"createdAt", "id"}:
            raise ValueError
        created_at = datetime.fromisoformat(payload["createdAt"])
        if created_at.tzinfo is None:
            raise ValueError
        return Cursor(created_at=created_at, resource_id=uuid.UUID(payload["id"]))
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        raise IntegrationProblem(
            400,
            "invalid_cursor",
            "Invalid cursor",
            "The pagination cursor is invalid or malformed.",
        ) from None
