"""Transaction-coupled idempotency claims and exact response replay."""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import IdempotencyRecord

IDEMPOTENCY_RETENTION = timedelta(hours=24)


class IdempotencyError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    record: IdempotencyRecord
    replay_status: int | None = None
    replay_body: dict[str, Any] | None = None

    @property
    def is_replay(self) -> bool:
        return self.replay_status is not None


def request_fingerprint(method: str, path: str, body: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"method": method.upper(), "path": path, "body": body},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _resolve_existing(
    record: IdempotencyRecord,
    *,
    method: str,
    path: str,
    fingerprint: str,
) -> IdempotencyClaim:
    if record.method != method.upper() or record.path != path or record.request_hash != fingerprint:
        raise IdempotencyError("idempotency_key_reused")
    if record.state != "completed":
        raise IdempotencyError("idempotency_in_progress")
    return IdempotencyClaim(
        record=record,
        replay_status=record.response_status,
        replay_body=record.response_body,
    )


def claim(
    session: Session,
    organization_id: uuid.UUID,
    *,
    key: str,
    method: str,
    path: str,
    body: dict[str, Any],
) -> IdempotencyClaim:
    if not 1 <= len(key) <= 255 or any(ord(char) < 0x21 or ord(char) > 0x7E for char in key):
        raise IdempotencyError("invalid_idempotency_key")
    normalized_method = method.upper()
    fingerprint = request_fingerprint(normalized_method, path, body)
    existing = session.execute(
        sa.select(IdempotencyRecord)
        .where(
            IdempotencyRecord.organization_id == organization_id,
            IdempotencyRecord.method == normalized_method,
            IdempotencyRecord.path == path,
            IdempotencyRecord.key == key,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if existing is not None:
        # The 24-hour window bounds deduplication, not the key namespace.
        # Replace an expired row while holding its unique-key row lock so a
        # concurrent retry either wins this replacement or resolves the new
        # record through the existing IntegrityError path below.  Different
        # payloads are therefore safe after expiry, while an unexpired key
        # retains the exact-response/reuse contract.
        if existing.expires_at <= datetime.now(UTC):
            session.delete(existing)
            session.flush()
        else:
            return _resolve_existing(existing, method=method, path=path, fingerprint=fingerprint)

    record = IdempotencyRecord(
        organization_id=organization_id,
        key=key,
        method=normalized_method,
        path=path,
        request_hash=fingerprint,
        expires_at=datetime.now(UTC) + IDEMPOTENCY_RETENTION,
    )
    try:
        with session.begin_nested():
            session.add(record)
            session.flush()
    except IntegrityError:
        existing = session.execute(
            sa.select(IdempotencyRecord)
            .where(
                IdempotencyRecord.organization_id == organization_id,
                IdempotencyRecord.method == normalized_method,
                IdempotencyRecord.path == path,
                IdempotencyRecord.key == key,
            )
            .with_for_update()
        ).scalar_one()
        return _resolve_existing(existing, method=method, path=path, fingerprint=fingerprint)
    return IdempotencyClaim(record=record)


def complete(
    session: Session,
    claim_result: IdempotencyClaim,
    *,
    status: int,
    body: dict[str, Any],
) -> None:
    if claim_result.is_replay:
        raise ValueError("cannot complete a replayed idempotency claim")
    claim_result.record.state = "completed"
    claim_result.record.response_status = status
    claim_result.record.response_body = body
    claim_result.record.updated_at = datetime.now(UTC)
    session.commit()
