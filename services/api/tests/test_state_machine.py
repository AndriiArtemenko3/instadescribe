"""Exhaustive state-machine tests: every legal edge, every illegal pair,
terminal behavior, and the complete legacy status mapping."""

import pytest
from app.domain.states import (
    COMPUTE_ACTIVE_STATES,
    LEGAL_EDGES,
    TERMINAL_STATES,
    IllegalTransitionError,
    JobState,
    to_legacy_status,
    validate_transition,
)

ALL_STATES = list(JobState)


def test_every_legal_edge_validates():
    for from_state, targets in LEGAL_EDGES.items():
        for to_state in targets:
            validate_transition(from_state, to_state)  # must not raise


def test_every_illegal_pair_raises_including_same_state():
    for from_state in ALL_STATES:
        for to_state in ALL_STATES:
            if to_state in LEGAL_EDGES[from_state]:
                continue
            with pytest.raises(IllegalTransitionError):
                validate_transition(from_state, to_state)


def test_same_state_is_never_a_legal_transition():
    for state in ALL_STATES:
        assert state not in LEGAL_EDGES[state]


def test_terminal_states_have_no_outgoing_edges():
    assert TERMINAL_STATES == {JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED}
    for state in TERMINAL_STATES:
        assert LEGAL_EDGES[state] == frozenset()


def test_retrying_is_not_a_persisted_state():
    assert "RETRYING" not in {s.value for s in JobState}


def test_retry_and_publication_recovery_edges_exist():
    validate_transition(JobState.PROCESSING, JobState.QUEUED)  # durable retry
    validate_transition(JobState.UPLOAD_COMPLETE, JobState.PROCESSING)  # recovery claim


def test_compute_active_states_exclude_awaiting_upload():
    assert COMPUTE_ACTIVE_STATES == {
        JobState.UPLOAD_COMPLETE,
        JobState.QUEUED,
        JobState.PROCESSING,
    }


def test_legacy_mapping_is_exhaustive_and_exact():
    expected = {
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
    assert set(expected) == set(ALL_STATES)
    for state, legacy in expected.items():
        assert to_legacy_status(state) == legacy
