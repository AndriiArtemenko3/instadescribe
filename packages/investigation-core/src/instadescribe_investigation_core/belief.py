"""Transparent, correlation-aware belief updates and bounded action selection."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import datetime

from .models import (
    ActionCandidate,
    ActionDecision,
    ActionType,
    BeliefCandidate,
    BeliefSnapshot,
    CandidatePrior,
    ConnectivityPolicy,
    EvidenceItem,
    VerificationState,
    utc_now,
)
from .serialization import canonical_json


@dataclass(frozen=True, slots=True)
class BeliefConfig:
    """Public baseline thresholds; production calibration is intentionally external."""

    temperature: float = 1.0
    minimum_confidence: float = 0.55
    minimum_margin: float = 0.10
    maximum_normalized_entropy: float = 0.80
    minimum_independent_groups: int = 2
    minimum_group_support: float = 0.10
    conflict_threshold: float = 0.25
    unverified_weight: float = 0.25

    def __post_init__(self) -> None:
        if not math.isfinite(self.temperature) or self.temperature <= 0:
            raise ValueError("temperature must be finite and greater than zero")
        for name in (
            "minimum_confidence",
            "minimum_margin",
            "maximum_normalized_entropy",
            "minimum_group_support",
            "conflict_threshold",
            "unverified_weight",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be finite and between zero and one")
        if self.minimum_independent_groups < 0:
            raise ValueError("minimum_independent_groups must not be negative")


@dataclass(frozen=True, slots=True)
class ActionUtilityWeights:
    latency: float = 0.01
    cost: float = 1.0
    privacy: float = 1.0

    def __post_init__(self) -> None:
        for name in ("latency", "cost", "privacy"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} weight must be finite and non-negative")


_CONNECTED_ACTIONS = frozenset({ActionType.SEARCH_TEXT, ActionType.SEARCH_CROP})
_BELIEF_SNAPSHOT_VERSION = "correlation-softmax-v2"
_CORRELATION_TIE_TOLERANCE = 1e-12


def action_allowed(action: ActionType, policy: ConnectivityPolicy) -> bool:
    if action not in _CONNECTED_ACTIONS:
        return True
    if policy is ConnectivityPolicy.LOCAL:
        return False
    if action is ActionType.SEARCH_CROP and policy is ConnectivityPolicy.TEXT_ONLY:
        return False
    # ``approvedCrops`` authorizes creation of an approval request, not the
    # network action itself. Execution belongs to the product egress gateway
    # after it verifies a durable human decision token; the open planner has
    # no such token and therefore stays fail-closed.
    if action is ActionType.SEARCH_CROP and policy is ConnectivityPolicy.APPROVED_CROPS:
        return False
    return True


def compute_action_utility(
    action: ActionCandidate,
    weights: ActionUtilityWeights | None = None,
) -> float:
    """U(a) = E[delta entropy] - latency - cost - privacy penalties."""

    selected_weights = weights or ActionUtilityWeights()
    return (
        action.expected_entropy_reduction
        - selected_weights.latency * action.expected_latency_seconds
        - selected_weights.cost * action.expected_cost_units
        - selected_weights.privacy * action.privacy_risk
    )


def select_best_action(
    actions: tuple[ActionCandidate, ...],
    *,
    policy: ConnectivityPolicy,
    weights: ActionUtilityWeights | None = None,
) -> ActionDecision:
    # STOP is an implicit zero-utility baseline. Ignore caller-supplied STOP
    # candidates so they cannot attach work, privacy risk or a fabricated entropy
    # reduction to what must remain a no-op.
    allowed = [
        action
        for action in actions
        if action.action is not ActionType.STOP and action_allowed(action.action, policy)
    ]
    if not allowed:
        return ActionDecision(
            action=ActionType.STOP,
            utility=0,
            expected_entropy_reduction=0,
        )
    ranked = tuple((compute_action_utility(action, weights), action) for action in allowed)
    utility, selected = max(ranked, key=lambda item: (item[0], item[1].action.value))
    if utility <= 0:
        return ActionDecision(
            action=ActionType.STOP,
            utility=0,
            expected_entropy_reduction=0,
        )
    return ActionDecision(
        action=selected.action,
        utility=utility,
        expected_entropy_reduction=selected.expected_entropy_reduction,
        parameters=dict(selected.parameters),
    )


def shannon_entropy(probabilities: tuple[float, ...]) -> float:
    if any(not math.isfinite(value) or value < 0 for value in probabilities):
        raise ValueError("probabilities must be finite and non-negative")
    total = sum(probabilities)
    if not probabilities or total <= 0:
        raise ValueError("probabilities must contain positive mass")
    normalized = (value / total for value in probabilities)
    return -sum(value * math.log(value) for value in normalized if value > 0)


def _verification_weight(state: VerificationState, config: BeliefConfig) -> float:
    if state is VerificationState.REJECTED:
        return 0
    if state is VerificationState.UNVERIFIED:
        return config.unverified_weight
    return 1


def _group_scores(
    evidence: tuple[EvidenceItem, ...],
    candidate_ids: frozenset[str],
    config: BeliefConfig,
) -> tuple[dict[str, dict[str, float]], dict[str, float]]:
    """Keep one strongest signed contribution per correlation group/candidate.

    OCR and VLM outputs derived from the same sign/frame can therefore share a
    correlation group without appearing as independent evidence. Equal-magnitude
    opposing contributions are reduced to zero instead of being decided by opaque
    evidence IDs; their strength is returned so abstention can surface the ambiguity.
    """

    contributions: dict[str, dict[str, list[float]]] = {}
    for item in evidence:
        verification_weight = _verification_weight(item.verification_state, config)
        if verification_weight == 0:
            continue
        group = contributions.setdefault(item.correlation_group, {})
        for contribution in item.contributions:
            if contribution.candidate_id not in candidate_ids:
                raise ValueError(
                    f"evidence {item.evidence_id!r} references unknown candidate "
                    f"{contribution.candidate_id!r}"
                )
            weighted = item.reliability * verification_weight * contribution.score
            group.setdefault(contribution.candidate_id, []).append(weighted)

    scores: dict[str, dict[str, float]] = {}
    ambiguous_groups: dict[str, float] = {}
    for group_id, candidate_contributions in contributions.items():
        group_scores: dict[str, float] = {}
        for candidate_id, values in candidate_contributions.items():
            maximum_strength = max(abs(value) for value in values)
            strongest = tuple(
                value
                for value in values
                if math.isclose(
                    abs(value),
                    maximum_strength,
                    rel_tol=_CORRELATION_TIE_TOLERANCE,
                    abs_tol=_CORRELATION_TIE_TOLERANCE,
                )
            )
            has_positive = any(value > 0 for value in strongest)
            has_negative = any(value < 0 for value in strongest)
            if has_positive and has_negative:
                group_scores[candidate_id] = 0.0
                ambiguous_groups[group_id] = max(
                    ambiguous_groups.get(group_id, 0.0), maximum_strength
                )
            elif has_positive:
                group_scores[candidate_id] = maximum_strength
            elif has_negative:
                group_scores[candidate_id] = -maximum_strength
            else:
                group_scores[candidate_id] = 0.0
        scores[group_id] = group_scores
    return scores, ambiguous_groups


def _softmax(values: tuple[float, ...], temperature: float) -> tuple[float, ...]:
    scaled = tuple(value / temperature for value in values)
    maximum = max(scaled)
    exponentials = tuple(math.exp(value - maximum) for value in scaled)
    denominator = sum(exponentials)
    return tuple(value / denominator for value in exponentials)


def _snapshot_id(
    candidates: tuple[BeliefCandidate, ...],
    evidence_ids: tuple[str, ...],
    *,
    config: BeliefConfig,
    entropy: float,
    normalized_entropy: float,
    abstained: bool,
    abstention_reasons: tuple[str, ...],
) -> str:
    digest = hashlib.sha256(
        canonical_json(
            {
                "version": _BELIEF_SNAPSHOT_VERSION,
                "config": config,
                "candidates": candidates,
                "entropy": entropy,
                "normalizedEntropy": normalized_entropy,
                "abstained": abstained,
                "abstentionReasons": abstention_reasons,
                "evidenceIds": evidence_ids,
            }
        ).encode()
    ).hexdigest()
    return f"belief-{digest[:20]}"


def update_beliefs(
    candidates: tuple[CandidatePrior, ...],
    evidence: tuple[EvidenceItem, ...],
    *,
    config: BeliefConfig | None = None,
    created_at: datetime | None = None,
) -> BeliefSnapshot:
    """Apply z_h = log(prior_h) + sum_g w_g*s_g(h), then temperature-scaled softmax.

    The transparent baseline abstains on a substantial conflict when the top
    candidate has signed support and contradiction, a correlation group has an
    equal-strength opposing tie, or another mutually exclusive candidate has at
    least ``max(1, minimum_independent_groups)`` groups at ``conflict_threshold``.
    """

    selected_config = config or BeliefConfig()
    if not candidates:
        raise ValueError("at least one candidate is required")
    candidate_ids = [candidate.candidate_id for candidate in candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("candidate IDs must be unique")

    prior_total = sum(candidate.prior for candidate in candidates)
    grouped, ambiguous_groups = _group_scores(evidence, frozenset(candidate_ids), selected_config)
    raw: list[tuple[CandidatePrior, float, dict[str, float]]] = []
    for candidate in candidates:
        scores = {
            group_id: candidate_scores[candidate.candidate_id]
            for group_id, candidate_scores in grouped.items()
            if candidate.candidate_id in candidate_scores
        }
        log_score = math.log(candidate.prior / prior_total) + sum(scores.values())
        raw.append((candidate, log_score, scores))

    probabilities = _softmax(tuple(item[1] for item in raw), selected_config.temperature)
    beliefs = tuple(
        sorted(
            (
                BeliefCandidate(
                    candidate_id=candidate.candidate_id,
                    label=candidate.label,
                    log_score=log_score,
                    probability=probability,
                    group_scores=dict(sorted(scores.items())),
                )
                for (candidate, log_score, scores), probability in zip(
                    raw, probabilities, strict=True
                )
            ),
            key=lambda belief: (-belief.probability, belief.candidate_id),
        )
    )

    entropy = shannon_entropy(tuple(item.probability for item in beliefs))
    maximum_entropy = math.log(len(beliefs)) if len(beliefs) > 1 else 0
    normalized_entropy = min(1.0, max(0.0, entropy / maximum_entropy)) if maximum_entropy else 0
    top = beliefs[0]
    runner_up = beliefs[1].probability if len(beliefs) > 1 else 0
    positive_groups = sum(
        score >= selected_config.minimum_group_support for score in top.group_scores.values()
    )
    has_signed_conflict = any(
        score >= selected_config.conflict_threshold for score in top.group_scores.values()
    ) and any(score <= -selected_config.conflict_threshold for score in top.group_scores.values())
    minimum_alternative_groups = max(1, selected_config.minimum_independent_groups)
    has_alternative_conflict = any(
        sum(
            score >= selected_config.conflict_threshold for score in candidate.group_scores.values()
        )
        >= minimum_alternative_groups
        for candidate in beliefs[1:]
    )
    has_ambiguous_group = any(
        strength >= selected_config.conflict_threshold for strength in ambiguous_groups.values()
    )
    has_conflict = has_signed_conflict or has_alternative_conflict or has_ambiguous_group

    reasons: list[str] = []
    active_evidence = tuple(
        item for item in evidence if item.verification_state is not VerificationState.REJECTED
    )
    if not active_evidence:
        reasons.append("noEvidence")
    if positive_groups < selected_config.minimum_independent_groups:
        reasons.append("insufficientIndependentEvidence")
    if top.probability < selected_config.minimum_confidence:
        reasons.append("lowConfidence")
    if top.probability - runner_up < selected_config.minimum_margin:
        reasons.append("lowMargin")
    if normalized_entropy > selected_config.maximum_normalized_entropy:
        reasons.append("highEntropy")
    if has_conflict:
        reasons.append("conflictingEvidence")

    evidence_ids = tuple(sorted(item.evidence_id for item in active_evidence))
    abstention_reasons = tuple(reasons)
    abstained = bool(abstention_reasons)
    return BeliefSnapshot(
        snapshot_id=_snapshot_id(
            beliefs,
            evidence_ids,
            config=selected_config,
            entropy=entropy,
            normalized_entropy=normalized_entropy,
            abstained=abstained,
            abstention_reasons=abstention_reasons,
        ),
        created_at=created_at or utc_now(),
        candidates=beliefs,
        entropy=entropy,
        normalized_entropy=normalized_entropy,
        abstained=abstained,
        abstention_reasons=abstention_reasons,
        evidence_ids=evidence_ids,
    )
