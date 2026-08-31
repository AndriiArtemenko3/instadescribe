from __future__ import annotations

import json
from dataclasses import replace

import pytest
from conftest import FIXED_TIME

from instadescribe_investigation_core import (
    TraceEventType,
    TraceRecorder,
    read_trace_jsonl,
    validate_trace,
    write_trace_jsonl,
)


def test_trace_round_trip_is_canonical_and_atomic(tmp_path) -> None:
    recorder = TraceRecorder("trace-1", clock=lambda: FIXED_TIME)
    recorder.record(TraceEventType.INVESTIGATION_STARTED, {"b": 2, "a": 1})
    recorder.record(TraceEventType.INVESTIGATION_NEEDS_REVIEW, {"abstained": True})
    destination = tmp_path / "trace.jsonl"

    write_trace_jsonl(recorder.events, destination)

    lines = destination.read_text(encoding="utf-8").splitlines()
    assert '"payload":{"a":1,"b":2}' in lines[0]
    assert read_trace_jsonl(destination) == recorder.events
    assert not list(tmp_path.glob("*.part"))


def test_trace_rejects_non_contiguous_sequences() -> None:
    recorder = TraceRecorder("trace-1", clock=lambda: FIXED_TIME)
    event = recorder.record(TraceEventType.INVESTIGATION_STARTED)

    with pytest.raises(ValueError, match="contiguous"):
        validate_trace((replace(event, sequence=1),))


def test_trace_recorder_snapshots_nested_payload() -> None:
    nested_values = ["original"]
    payload = {"extension": {"values": nested_values}}
    recorder = TraceRecorder("trace-1", clock=lambda: FIXED_TIME)

    event = recorder.record(TraceEventType.INVESTIGATION_STARTED, payload)
    nested_values.append("mutated")
    payload["extension"] = {"values": ["replaced"]}

    assert event.payload == {"extension": {"values": ["original"]}}
    assert recorder.events[0].payload == {"extension": {"values": ["original"]}}


def _raw_trace_event() -> dict[str, object]:
    return {
        "event_type": TraceEventType.INVESTIGATION_STARTED.value,
        "occurred_at": "2026-08-30T12:00:00Z",
        "payload": {},
        "sequence": 0,
        "trace_id": "trace-1",
    }


@pytest.mark.parametrize("field_change", ("missing", "extra"))
def test_trace_reader_requires_exact_event_fields(tmp_path, field_change) -> None:
    payload = _raw_trace_event()
    if field_change == "missing":
        payload.pop("occurred_at")
    else:
        payload["unexpected"] = True
    source = tmp_path / "trace.jsonl"
    source.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="fields must exactly match"):
        read_trace_jsonl(source)


def test_trace_reader_rejects_boolean_sequence(tmp_path) -> None:
    payload = _raw_trace_event()
    payload["sequence"] = True
    source = tmp_path / "trace.jsonl"
    source.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="sequence must be an integer"):
        read_trace_jsonl(source)


@pytest.mark.parametrize(
    "extension",
    (
        {"score": float("nan")},
        {"note": "x" * 131_073},
    ),
)
def test_trace_reader_rejects_unbounded_or_non_finite_extensions(tmp_path, extension) -> None:
    payload = _raw_trace_event()
    payload["payload"] = extension
    source = tmp_path / "trace.jsonl"
    source.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="finite|maximum string length"):
        read_trace_jsonl(source)
