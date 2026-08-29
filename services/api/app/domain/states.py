"""Job state machine: persisted states, legal edges, legacy status mapping.

Semantics fixed by ADR-0007 (as amended at the G2 reconciliation):
- "RETRYING" is NOT a persisted status — a retry is the single durable edge
  PROCESSING -> QUEUED with attempt/error metadata updated in the same
  operation.
- UPLOAD_COMPLETE -> PROCESSING is the queue-publication recovery edge (the
  SQS send succeeded, the final -> QUEUED update failed, and the worker
  claimed the job directly).
- Terminal states have no outgoing transitions. Same-state "transitions" are
  not legal edges; idempotent endpoints must recognize the current state
  before requesting one.
"""

from enum import StrEnum


class JobState(StrEnum):
    AWAITING_UPLOAD = "AWAITING_UPLOAD"
    UPLOAD_COMPLETE = "UPLOAD_COMPLETE"
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    EXPORT_QUEUED = "EXPORT_QUEUED"
    EXPORTING = "EXPORTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


_F = JobState.FAILED
_C = JobState.CANCELLED

LEGAL_EDGES: dict[JobState, frozenset[JobState]] = {
    JobState.AWAITING_UPLOAD: frozenset({JobState.UPLOAD_COMPLETE, _F, _C}),
    JobState.UPLOAD_COMPLETE: frozenset({JobState.QUEUED, JobState.PROCESSING, _F, _C}),
    JobState.QUEUED: frozenset({JobState.PROCESSING, _F, _C}),
    JobState.PROCESSING: frozenset({JobState.QUEUED, JobState.READY_FOR_REVIEW, _F, _C}),
    JobState.READY_FOR_REVIEW: frozenset({JobState.EXPORT_QUEUED, _F, _C}),
    JobState.EXPORT_QUEUED: frozenset({JobState.EXPORTING, _F, _C}),
    JobState.EXPORTING: frozenset({JobState.COMPLETED, _F, _C}),
    JobState.COMPLETED: frozenset(),
    JobState.FAILED: frozenset(),
    JobState.CANCELLED: frozenset(),
}

TERMINAL_STATES = frozenset({state for state, targets in LEGAL_EDGES.items() if not targets})

# Statuses that may hold the single compute-active portfolio slot (the partial
# unique index in migration 0001). AWAITING_UPLOAD is deliberately excluded so
# an abandoned upload reservation cannot block new uploads forever.
COMPUTE_ACTIVE_STATES = frozenset({JobState.UPLOAD_COMPLETE, JobState.QUEUED, JobState.PROCESSING})

_LEGACY: dict[JobState, str] = {
    JobState.AWAITING_UPLOAD: "queued",
    JobState.UPLOAD_COMPLETE: "queued",
    JobState.QUEUED: "queued",
    JobState.PROCESSING: "processing",
    JobState.READY_FOR_REVIEW: "ready",
    JobState.EXPORT_QUEUED: "processing",
    JobState.EXPORTING: "processing",
    JobState.COMPLETED: "ready",
    JobState.FAILED: "failed",
    JobState.CANCELLED: "failed",
}


class IllegalTransitionError(Exception):
    """A requested edge is not in the legal transition table."""

    def __init__(self, from_state: JobState, to_state: JobState) -> None:
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(f"illegal transition {from_state.value} -> {to_state.value}")


def validate_transition(from_state: JobState, to_state: JobState) -> None:
    if to_state not in LEGAL_EDGES[from_state]:
        raise IllegalTransitionError(from_state, to_state)


def to_legacy_status(state: JobState) -> str:
    """Exhaustive mapping onto the frontend's queued|processing|ready|failed."""
    return _LEGACY[state]
