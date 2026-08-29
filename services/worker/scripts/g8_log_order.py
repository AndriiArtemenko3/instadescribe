"""G8.1 D3 — structured delete-order proof.

Parses worker log output into structured records (each worker line embeds
one JSON object) and requires, for the given job: exactly one `job_ready`
and exactly one LATER `message_success` with the same job id and attempt,
and zero `success_ack_pending`. Substring presence alone is never trusted.
"""

import json


def parse_worker_events(logs: str) -> list[dict]:
    """Extract every embedded JSON record ({"event": ...}) from compose log
    output (lines carry a `service | ` prefix)."""
    events = []
    for line in logs.splitlines():
        start = line.find("{")
        if start == -1:
            continue
        try:
            record = json.loads(line[start:])
        except ValueError:
            continue
        if isinstance(record, dict) and "event" in record:
            events.append(record)
    return events


def assert_ready_before_ack(logs: str, job_id: str) -> str:
    """Raises ValueError unless exactly one job_ready precedes exactly one
    message_success for the SAME job and attempt. Returns a short evidence
    string on success."""
    events = parse_worker_events(logs)
    if not events:
        raise ValueError("no structured worker log records found")
    if any(e["event"] == "success_ack_pending" for e in events):
        raise ValueError("success_ack_pending present — deletion ordering not clean")

    ready = [
        (i, e)
        for i, e in enumerate(events)
        if e["event"] == "job_ready" and e.get("job_id") == job_id
    ]
    acks = [
        (i, e)
        for i, e in enumerate(events)
        if e["event"] == "message_success" and e.get("job_id") == job_id
    ]
    if len(ready) != 1:
        raise ValueError(f"expected exactly one job_ready for {job_id}, saw {len(ready)}")
    if len(acks) != 1:
        raise ValueError(f"expected exactly one message_success for {job_id}, saw {len(acks)}")
    ready_idx, ready_event = ready[0]
    ack_idx, ack_event = acks[0]
    if ack_idx <= ready_idx:
        raise ValueError("message_success does not occur after job_ready")
    if ready_event.get("attempt") != ack_event.get("attempt"):
        raise ValueError(
            f"attempt mismatch: job_ready attempt {ready_event.get('attempt')} != "
            f"message_success attempt {ack_event.get('attempt')}"
        )
    return (
        f"job_ready[{ready_idx}] -> message_success[{ack_idx}] "
        f"(attempt {ready_event.get('attempt')}); no success_ack_pending"
    )
