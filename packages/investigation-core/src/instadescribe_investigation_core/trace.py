"""In-memory trace recording and deterministic, atomic JSONL interchange."""

from __future__ import annotations

import json
import math
import os
import tempfile
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path

from .models import JsonValue, TraceEvent, TraceEventType, utc_now
from .serialization import canonical_json

_TRACE_EVENT_FIELDS = frozenset({"event_type", "occurred_at", "payload", "sequence", "trace_id"})
_MAX_TRACE_LINE_CHARACTERS = 1_000_000
_MAX_STRING_CHARACTERS = 131_072
_MAX_COLLECTION_ITEMS = 4_096
_MAX_OBJECT_FIELDS = 256
_MAX_JSON_DEPTH = 32
_MAX_JSON_NODES = 100_000


class TraceRecorder:
    def __init__(
        self,
        trace_id: str,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if not trace_id.strip():
            raise ValueError("trace_id must not be empty")
        self._trace_id = trace_id
        self._clock = clock
        self._events: list[TraceEvent] = []

    @property
    def events(self) -> tuple[TraceEvent, ...]:
        return tuple(self._events)

    def record(
        self,
        event_type: TraceEventType,
        payload: dict[str, JsonValue] | None = None,
    ) -> TraceEvent:
        event = TraceEvent(
            trace_id=self._trace_id,
            sequence=len(self._events),
            event_type=event_type,
            occurred_at=self._clock(),
            payload=payload or {},
        )
        self._events.append(event)
        return event


def validate_trace(events: Iterable[TraceEvent]) -> tuple[TraceEvent, ...]:
    materialized = tuple(events)
    if not materialized:
        return materialized
    trace_id = materialized[0].trace_id
    for expected_sequence, event in enumerate(materialized):
        if event.trace_id != trace_id:
            raise ValueError("all trace events must have the same trace_id")
        if event.sequence != expected_sequence:
            raise ValueError("trace event sequences must be contiguous and start at zero")
    return materialized


def write_trace_jsonl(events: Iterable[TraceEvent], destination: Path) -> None:
    """Atomically replace a trace file with canonical, one-event-per-line JSON."""

    validated = validate_trace(events)
    target = destination.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".part",
        dir=target.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            for event in validated:
                handle.write(canonical_json(event))
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("trace occurred_at must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("trace occurred_at must be timezone-aware")
    return parsed.astimezone(UTC)


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _validate_json_bounds(value: object) -> None:
    stack = [(value, 0)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > _MAX_JSON_NODES:
            raise ValueError("trace event exceeds the maximum JSON node count")
        if depth > _MAX_JSON_DEPTH:
            raise ValueError("trace event exceeds the maximum JSON nesting depth")
        if current is None or isinstance(current, bool | int):
            continue
        if isinstance(current, float):
            if not math.isfinite(current):
                raise ValueError("trace event numbers must be finite")
            continue
        if isinstance(current, str):
            if len(current) > _MAX_STRING_CHARACTERS:
                raise ValueError("trace event exceeds the maximum string length")
            continue
        if isinstance(current, list):
            if len(current) > _MAX_COLLECTION_ITEMS:
                raise ValueError("trace event exceeds the maximum collection size")
            stack.extend((item, depth + 1) for item in current)
            continue
        if isinstance(current, dict):
            if len(current) > _MAX_OBJECT_FIELDS:
                raise ValueError("trace event exceeds the maximum field count")
            for key, item in current.items():
                if not isinstance(key, str):  # pragma: no cover - guaranteed by json.loads
                    raise ValueError("trace event object keys must be strings")
                if len(key) > _MAX_STRING_CHARACTERS:
                    raise ValueError("trace event contains an oversized field name")
                stack.append((item, depth + 1))
            continue
        raise ValueError(f"trace event contains unsupported JSON value: {type(current).__name__}")


def read_trace_jsonl(source: Path) -> tuple[TraceEvent, ...]:
    events: list[TraceEvent] = []
    with source.expanduser().resolve(strict=True).open(encoding="utf-8") as handle:
        line_number = 0
        while line := handle.readline(_MAX_TRACE_LINE_CHARACTERS + 1):
            line_number += 1
            if len(line) > _MAX_TRACE_LINE_CHARACTERS:
                raise ValueError(f"invalid trace event on line {line_number}: line is too large")
            if not line.strip():
                continue
            try:
                payload = json.loads(line, object_pairs_hook=_strict_json_object)
                _validate_json_bounds(payload)
                if not isinstance(payload, dict):
                    raise ValueError("event must be an object")
                if set(payload) != _TRACE_EVENT_FIELDS:
                    raise ValueError("event fields must exactly match the trace event schema")
                event_payload = payload["payload"]
                if not isinstance(event_payload, dict):
                    raise ValueError("payload must be an object")
                trace_id = payload["trace_id"]
                sequence = payload["sequence"]
                if not isinstance(trace_id, str) or not (
                    isinstance(sequence, int) and not isinstance(sequence, bool)
                ):
                    raise ValueError("trace_id must be a string and sequence must be an integer")
                events.append(
                    TraceEvent(
                        trace_id=trace_id,
                        sequence=sequence,
                        event_type=TraceEventType(payload["event_type"]),
                        occurred_at=_parse_timestamp(payload["occurred_at"]),
                        payload=event_payload,
                    )
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise ValueError(f"invalid trace event on line {line_number}: {error}") from error
    return validate_trace(events)
