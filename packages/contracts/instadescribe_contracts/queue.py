"""The single versioned InstaDescribe SQS queue-message contract (spec §10, ADR-0004).

Identifiers only: PostgreSQL remains the source of truth. The body must never
carry secrets, token material, signed URLs, S3 credentials, settings,
provider keys or prompts. Unknown fields are forbidden; timestamps are
JSON-safe UTC RFC3339.
"""

import json
import re
import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

SCHEMA_VERSION = 1
# Well below the 256 KiB SQS limit: canonical bodies are ~200 bytes; anything
# larger is hostile or corrupt and is rejected before JSON parsing.
MAX_BODY_BYTES = 8 * 1024

# The EXACT grammar this contract's serializer emits — nothing looser.
# Rejects ISO week/ordinal dates, space separators, comma fractions,
# minute-only timestamps and every non-Z offset BEFORE fromisoformat's
# permissive ISO-8601 superset can accept them.
_CANONICAL_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$")


class QueueMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal[1] = Field(alias="schemaVersion")
    message_id: uuid.UUID = Field(alias="messageId")
    task_type: Literal["ANALYZE"] = Field(alias="taskType")
    job_id: uuid.UUID = Field(alias="jobId")
    requested_at: datetime = Field(alias="requestedAt")

    @field_validator("requested_at")
    @classmethod
    def _must_be_utc_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("requestedAt must be timezone-aware UTC")
        return v.astimezone(UTC)

    @field_serializer("requested_at")
    def _rfc3339(self, v: datetime) -> str:
        return v.astimezone(UTC).isoformat().replace("+00:00", "Z")

    def to_body(self) -> str:
        return self.model_dump_json(by_alias=True)

    @classmethod
    def from_body(cls, body: str | bytes) -> "QueueMessage":
        """The SOLE untrusted-consumer parser (worker boundary): accepts one
        canonical JSON object and nothing else.

        - exact camelCase key set, no snake_case aliases, no unknown keys,
          duplicate keys rejected;
        - schemaVersion must be the JSON integer 1 (booleans/floats/strings
          rejected — note bool is an int subclass in Python);
        - IDs must be JSON strings holding canonical lowercase UUIDs;
        - taskType must be the JSON string "ANALYZE";
        - requestedAt must be a JSON string in timezone-aware RFC3339 form
          (numeric epochs, naive datetimes and coercions rejected);
        - bodies above MAX_BODY_BYTES are rejected before parsing.
        """
        raw = body.encode("utf-8") if isinstance(body, str) else body
        if len(raw) > MAX_BODY_BYTES:
            raise ValueError(f"queue body exceeds {MAX_BODY_BYTES} bytes")

        def _no_duplicates(pairs):
            keys = [k for k, _ in pairs]
            if len(keys) != len(set(keys)):
                raise ValueError("duplicate keys in queue body")
            return dict(pairs)

        data = json.loads(raw, object_pairs_hook=_no_duplicates)
        if not isinstance(data, dict):
            raise ValueError("queue body must be a JSON object")
        expected_keys = {"schemaVersion", "messageId", "taskType", "jobId", "requestedAt"}
        if set(data) != expected_keys:
            raise ValueError("queue body must contain exactly the canonical key set")
        sv = data["schemaVersion"]
        if type(sv) is not int or sv != SCHEMA_VERSION:  # bool is an int subclass
            raise ValueError("schemaVersion must be the JSON integer 1")
        if type(data["taskType"]) is not str or data["taskType"] != "ANALYZE":
            raise ValueError("taskType must be the JSON string 'ANALYZE'")
        ids = {}
        for key in ("messageId", "jobId"):
            value = data[key]
            if type(value) is not str:
                raise ValueError(f"{key} must be a JSON string")
            parsed = uuid.UUID(value)
            if str(parsed) != value:
                raise ValueError(f"{key} must be a canonical lowercase UUID")
            ids[key] = parsed
        ts = data["requestedAt"]
        if type(ts) is not str:
            raise ValueError("requestedAt must be a JSON string (numeric epochs rejected)")
        if not _CANONICAL_TS_RE.match(ts):
            raise ValueError(
                "requestedAt must be canonical UTC RFC3339: YYYY-MM-DDTHH:MM:SS[.ffffff]Z"
            )
        parsed_ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if parsed_ts.tzinfo is None:  # unreachable after the grammar check; belt only
            raise ValueError("requestedAt must be timezone-aware")
        return cls(
            schema_version=1,
            message_id=ids["messageId"],
            task_type="ANALYZE",
            job_id=ids["jobId"],
            requested_at=parsed_ts,
        )
