"""Job transition primitive — the only sanctioned way to change `jobs.status`.

Validates the edge against the legal-transition table, then performs one
conditional UPDATE guarded by the expected current status, so a lost race
returns None instead of clobbering another process's transition (the G4/G5
publication-race and claim semantics build directly on this).
"""

import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.domain.states import JobState, validate_transition
from app.models.job import Job


def transition_job(
    session: Session,
    job_id: uuid.UUID,
    expected: JobState | tuple[JobState, ...],
    to_state: JobState,
    *,
    values: dict | None = None,
) -> Job | None:
    """Conditionally move a job along a legal edge.

    `expected` may be a tuple (e.g. a worker claim from QUEUED or
    UPLOAD_COMPLETE); every expected->to edge must be legal. Returns the
    updated Job, or None when the row is not in an expected state (lost race,
    already-transitioned duplicate, or missing job). Raises
    IllegalTransitionError before touching the database for illegal edges.
    """
    expected_states = expected if isinstance(expected, tuple) else (expected,)
    for from_state in expected_states:
        validate_transition(from_state, to_state)

    stmt = (
        sa.update(Job)
        .where(Job.id == job_id, Job.status.in_([s.value for s in expected_states]))
        .values(status=to_state.value, updated_at=sa.func.now(), **(values or {}))
        .returning(Job)
    )
    result = session.execute(stmt)
    session.flush()
    return result.scalar_one_or_none()
