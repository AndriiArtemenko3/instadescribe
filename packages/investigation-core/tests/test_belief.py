from __future__ import annotations

import math

import pytest
from conftest import FIXED_TIME, evidence_item

from instadescribe_investigation_core import (
    ActionCandidate,
    ActionType,
    BeliefConfig,
    CandidatePrior,
    ConnectivityPolicy,
    VerificationState,
    compute_action_utility,
    select_best_action,
    shannon_entropy,
    update_beliefs,
)

CANDIDATES = (
    CandidatePrior("pl", "Poland", 0.5),
    CandidatePrior("sk", "Slovakia", 0.5),
)


def test_correlation_group_keeps_only_strongest_contribution() -> None:
    belief = update_beliefs(
        CANDIDATES,
        (
            evidence_item("ocr", group="same-sign", score=0.5),
            evidence_item("vlm", group="same-sign", score=0.8),
        ),
        config=BeliefConfig(minimum_independent_groups=0),
        created_at=FIXED_TIME,
    )

    assert belief.candidates[0].candidate_id == "pl"
    assert belief.candidates[0].group_scores == {"same-sign": 0.8}
    assert belief.candidates[0].probability == pytest.approx(1 / (1 + math.exp(-0.8)))


def test_two_independent_groups_can_produce_non_abstained_result() -> None:
    belief = update_beliefs(
        CANDIDATES,
        (
            evidence_item("road-sign", group="road-sign", score=0.9),
            evidence_item("domain", group="web-domain", score=0.8),
        ),
        created_at=FIXED_TIME,
    )

    assert belief.abstained is False
    assert belief.candidates[0].candidate_id == "pl"
    assert sum(item.probability for item in belief.candidates) == pytest.approx(1)


def test_conflict_and_unverified_weight_are_visible() -> None:
    belief = update_beliefs(
        CANDIDATES,
        (
            evidence_item("support", group="support", score=0.8),
            evidence_item("contradict", group="contradict", score=-0.7),
            evidence_item(
                "unverified",
                group="unverified",
                score=0.8,
                state=VerificationState.UNVERIFIED,
            ),
        ),
        created_at=FIXED_TIME,
    )

    assert belief.candidates[0].group_scores["unverified"] == pytest.approx(0.2)
    assert "conflictingEvidence" in belief.abstention_reasons


def test_equal_opposing_correlated_evidence_is_conservative_and_id_invariant() -> None:
    first = update_beliefs(
        CANDIDATES,
        (
            evidence_item("a-support", group="same-sign", score=0.8),
            evidence_item("z-contradict", group="same-sign", score=-0.8),
        ),
        config=BeliefConfig(minimum_independent_groups=0),
        created_at=FIXED_TIME,
    )
    renamed_and_reordered = update_beliefs(
        CANDIDATES,
        (
            evidence_item("a-contradict", group="same-sign", score=-0.8),
            evidence_item("z-support", group="same-sign", score=0.8),
        ),
        config=BeliefConfig(minimum_independent_groups=0),
        created_at=FIXED_TIME,
    )

    assert [item.probability for item in first.candidates] == pytest.approx(
        [item.probability for item in renamed_and_reordered.candidates]
    )
    assert first.abstained is True
    assert renamed_and_reordered.abstained is True
    assert "conflictingEvidence" in first.abstention_reasons
    assert "conflictingEvidence" in renamed_and_reordered.abstention_reasons


def test_substantial_support_for_mutually_exclusive_alternative_abstains() -> None:
    evidence = tuple(
        evidence_item(f"pl-{index}", group=f"pl-{index}", score=0.9) for index in range(4)
    ) + tuple(
        evidence_item(
            f"sk-{index}",
            group=f"sk-{index}",
            candidate_id="sk",
            score=0.9,
        )
        for index in range(2)
    )

    belief = update_beliefs(CANDIDATES, evidence, created_at=FIXED_TIME)

    assert belief.candidates[0].candidate_id == "pl"
    assert belief.candidates[0].probability > 0.8
    assert belief.abstained is True
    assert "conflictingEvidence" in belief.abstention_reasons


def test_snapshot_identity_includes_config_and_abstention_outcome() -> None:
    evidence = (
        evidence_item("road-sign", group="road-sign", score=0.9),
        evidence_item("domain", group="web-domain", score=0.8),
    )
    accepted = update_beliefs(
        CANDIDATES,
        evidence,
        config=BeliefConfig(minimum_confidence=0.55),
        created_at=FIXED_TIME,
    )
    abstained = update_beliefs(
        CANDIDATES,
        evidence,
        config=BeliefConfig(minimum_confidence=0.99),
        created_at=FIXED_TIME,
    )

    assert accepted.abstained is False
    assert abstained.abstained is True
    assert accepted.snapshot_id != abstained.snapshot_id


def test_empty_evidence_abstains_and_entropy_is_maximal() -> None:
    belief = update_beliefs(CANDIDATES, (), created_at=FIXED_TIME)

    assert belief.abstained is True
    assert "noEvidence" in belief.abstention_reasons
    assert belief.normalized_entropy == pytest.approx(1)
    assert shannon_entropy((0.5, 0.5)) == pytest.approx(math.log(2))


def test_action_utility_and_local_policy_block_connected_actions() -> None:
    connected = ActionCandidate(
        action=ActionType.SEARCH_CROP,
        expected_entropy_reduction=1,
        privacy_risk=0.1,
    )
    local = ActionCandidate(
        action=ActionType.OCR,
        expected_entropy_reduction=0.4,
        expected_latency_seconds=1,
    )

    decision = select_best_action((connected, local), policy=ConnectivityPolicy.LOCAL)

    assert decision.action is ActionType.OCR
    assert compute_action_utility(local) == pytest.approx(0.39)

    awaiting_approval = select_best_action(
        (connected, local),
        policy=ConnectivityPolicy.APPROVED_CROPS,
    )
    assert awaiting_approval.action is ActionType.OCR


def test_action_selection_stops_when_every_action_has_non_positive_utility() -> None:
    costly = ActionCandidate(
        action=ActionType.OCR,
        expected_entropy_reduction=0,
        expected_latency_seconds=10,
        privacy_risk=1,
    )
    neutral = ActionCandidate(
        action=ActionType.TRANSCRIBE,
        expected_entropy_reduction=0,
    )
    fabricated_stop = ActionCandidate(
        action=ActionType.STOP,
        expected_entropy_reduction=1,
        parameters={"work": "must-not-run"},
    )

    decision = select_best_action(
        (costly, neutral, fabricated_stop),
        policy=ConnectivityPolicy.LOCAL,
    )

    assert decision.action is ActionType.STOP
    assert decision.utility == 0
    assert decision.expected_entropy_reduction == 0
    assert decision.parameters == {}
