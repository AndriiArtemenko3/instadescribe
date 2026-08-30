"""The one versioned queue-message contract: exact shape, strictness, UTC."""

import json
import uuid
from datetime import UTC, datetime, timezone
from datetime import timedelta as td

import pytest
from instadescribe_contracts.queue import QueueMessage
from pydantic import ValidationError


def _msg(**overrides):
    payload = {
        "schemaVersion": 1,
        "messageId": str(uuid.uuid4()),
        "taskType": "ANALYZE",
        "jobId": str(uuid.uuid4()),
        "requestedAt": "2026-08-06T12:00:00Z",
    }
    payload.update(overrides)
    return payload


def test_round_trip_and_exact_wire_shape():
    m = QueueMessage.model_validate(_msg())
    body = m.to_body()
    parsed = json.loads(body)
    assert set(parsed) == {"schemaVersion", "messageId", "taskType", "jobId", "requestedAt"}
    assert parsed["schemaVersion"] == 1
    assert parsed["taskType"] == "ANALYZE"
    assert parsed["requestedAt"].endswith("Z")
    assert QueueMessage.from_body(body) == m


def test_extra_fields_are_forbidden():
    with pytest.raises(ValidationError):
        QueueMessage.model_validate(_msg(signedUrl="https://leak.example"))


@pytest.mark.parametrize(
    "mutation",
    [
        {"schemaVersion": 2},
        {"taskType": "EXPORT"},
        {"messageId": "not-a-uuid"},
        {"jobId": "also-not-a-uuid"},
        {"requestedAt": "yesterday"},
    ],
)
def test_invalid_version_type_uuid_and_time_are_rejected(mutation):
    with pytest.raises(ValidationError):
        QueueMessage.model_validate(_msg(**mutation))


def test_naive_timestamps_are_rejected_and_offsets_normalize_to_utc():
    with pytest.raises(ValidationError):
        QueueMessage(
            schema_version=1,
            message_id=uuid.uuid4(),
            task_type="ANALYZE",
            job_id=uuid.uuid4(),
            requested_at=datetime(2026, 8, 6, 12, 0, 0),  # naive
        )
    offset = QueueMessage(
        schema_version=1,
        message_id=uuid.uuid4(),
        task_type="ANALYZE",
        job_id=uuid.uuid4(),
        requested_at=datetime(2026, 8, 6, 14, 0, 0, tzinfo=timezone(td(hours=2))),
    )
    assert offset.requested_at == datetime(2026, 8, 6, 12, 0, 0, tzinfo=UTC)
    assert json.loads(offset.to_body())["requestedAt"] == "2026-08-06T12:00:00Z"


def test_body_carries_identifiers_only():
    body = json.loads(QueueMessage.model_validate(_msg()).to_body())
    for forbidden in ("settings", "token", "url", "key", "prompt", "provider"):
        assert not any(forbidden in k.lower() for k in body)


# --- strict wire parser (the sole untrusted-consumer boundary) ---


def test_wire_round_trip_is_canonical():
    m = QueueMessage.model_validate(_msg())
    assert QueueMessage.from_body(m.to_body()) == m


@pytest.mark.parametrize(
    "mutation",
    [
        {"schemaVersion": True},  # bool is an int subclass — must be rejected
        {"schemaVersion": 1.0},
        {"schemaVersion": "1"},
        {"schemaVersion": 2},
        {"taskType": "analyze"},
        {"taskType": 1},
        {"messageId": 123},
        {"messageId": "B2BFA32C-9D5E-4B08-9AB6-6F0AFEA8B3E6"},  # not canonical lowercase
        {"messageId": "{b2bfa32c-9d5e-4b08-9ab6-6f0afea8b3e6}"},
        {"jobId": 42},
        {"requestedAt": 1754500000},  # numeric epoch
        {"requestedAt": 1754500000.5},
        {"requestedAt": "2026-08-06T12:00:00"},  # naive
        {"requestedAt": "yesterday"},
    ],
)
def test_wire_parser_rejects_every_coercion(mutation):
    with pytest.raises((ValueError, TypeError)):
        QueueMessage.from_body(json.dumps(_msg(**mutation)))


def test_wire_parser_rejects_snake_case_missing_and_unknown_keys():
    m = _msg()
    snake = {
        "schema_version": 1,
        "message_id": m["messageId"],
        "task_type": "ANALYZE",
        "job_id": m["jobId"],
        "requested_at": m["requestedAt"],
    }
    with pytest.raises(ValueError):
        QueueMessage.from_body(json.dumps(snake))
    missing = {k: v for k, v in m.items() if k != "jobId"}
    with pytest.raises(ValueError):
        QueueMessage.from_body(json.dumps(missing))
    with pytest.raises(ValueError):
        QueueMessage.from_body(json.dumps({**m, "extra": "x"}))


def test_wire_parser_rejects_non_objects_invalid_json_and_duplicates():
    for bad in ("[]", '"scalar"', "42", "not json at all", "{"):
        with pytest.raises(ValueError):
            QueueMessage.from_body(bad)
    m = _msg()
    dup = (
        '{"schemaVersion": 1, "schemaVersion": 1, '
        f'"messageId": "{m["messageId"]}", "taskType": "ANALYZE", '
        f'"jobId": "{m["jobId"]}", "requestedAt": "{m["requestedAt"]}"}}'
    )
    with pytest.raises(ValueError, match="duplicate"):
        QueueMessage.from_body(dup)


def test_wire_parser_rejects_oversized_bodies_before_parsing():
    from instadescribe_contracts.queue import MAX_BODY_BYTES

    huge = '{"schemaVersion": 1, "pad": "' + "x" * MAX_BODY_BYTES + '"}'
    with pytest.raises(ValueError, match="exceeds"):
        QueueMessage.from_body(huge)


@pytest.mark.parametrize(
    "ts",
    [
        "2026-08-06 12:00:00Z",  # space separator
        "2026-08-06T12:00Z",  # minute-only
        "2026-08-06T12:00:00,123456Z",  # comma fraction
        "2026-08-06T12:00:00+05:00",  # non-UTC offset
        "2026-08-06T12:00:00+00:00",  # offset form instead of Z
        "2026-08-06T12:00:00-00:00",
        "2026-08-06T12:00:00",  # no zone at all
        "2026-W32-4T12:00:00Z",  # ISO week date
        "2026-219T12:00:00Z",  # ISO ordinal date
        "2026-08-06T12:00:00.1234567Z",  # over-precision fraction
        "2026-08-06t12:00:00Z",  # lowercase separator
        "2026-08-06T12:00:00.Z",  # empty fraction
    ],
)
def test_wire_parser_rejects_non_canonical_timestamps(ts):
    """G5.1 A2: requestedAt must be the EXACT serializer grammar
    (YYYY-MM-DDTHH:MM:SS[.ffffff]Z) — fromisoformat's permissive ISO-8601
    superset must never be reachable."""
    with pytest.raises(ValueError):
        QueueMessage.from_body(json.dumps(_msg(requestedAt=ts)))


def test_wire_parser_accepts_only_the_canonical_grammar():
    for ts in ("2026-08-06T12:00:00Z", "2026-08-06T12:00:00.000123Z"):
        parsed = QueueMessage.from_body(json.dumps(_msg(requestedAt=ts)))
        assert parsed.requested_at.tzinfo is not None
