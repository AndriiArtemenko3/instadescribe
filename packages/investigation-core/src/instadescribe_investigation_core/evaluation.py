"""Small, dependency-free metrics for investigation evaluation fixtures."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import median


@dataclass(frozen=True, slots=True)
class RankedPrediction:
    ranked_candidate_ids: tuple[str, ...]
    truth_id: str


@dataclass(frozen=True, slots=True)
class GeolocationPrediction:
    predicted_latitude: float
    predicted_longitude: float
    truth_latitude: float
    truth_longitude: float


@dataclass(frozen=True, slots=True)
class CalibrationPrediction:
    probabilities: Mapping[str, float]
    truth_id: str


@dataclass(frozen=True, slots=True)
class RetrievalPrediction:
    ranked_ids: tuple[str, ...]
    relevant_ids: frozenset[str]


def _mean(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("at least one example is required")
    return sum(values) / len(values)


def top_k_accuracy(examples: Sequence[RankedPrediction], *, k: int) -> float:
    if k <= 0:
        raise ValueError("k must be positive")
    return _mean(
        [float(example.truth_id in example.ranked_candidate_ids[:k]) for example in examples]
    )


def haversine_km(
    latitude_a: float,
    longitude_a: float,
    latitude_b: float,
    longitude_b: float,
) -> float:
    for latitude in (latitude_a, latitude_b):
        if not -90 <= latitude <= 90:
            raise ValueError("latitude must be between -90 and 90")
    for longitude in (longitude_a, longitude_b):
        if not -180 <= longitude <= 180:
            raise ValueError("longitude must be between -180 and 180")
    radius_km = 6371.0088
    phi_a, phi_b = math.radians(latitude_a), math.radians(latitude_b)
    delta_phi = math.radians(latitude_b - latitude_a)
    delta_lambda = math.radians(longitude_b - longitude_a)
    value = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi_a) * math.cos(phi_b) * math.sin(delta_lambda / 2) ** 2
    )
    return radius_km * 2 * math.atan2(math.sqrt(value), math.sqrt(max(0, 1 - value)))


def median_geolocation_error_km(examples: Sequence[GeolocationPrediction]) -> float:
    if not examples:
        raise ValueError("at least one example is required")
    return median(
        haversine_km(
            example.predicted_latitude,
            example.predicted_longitude,
            example.truth_latitude,
            example.truth_longitude,
        )
        for example in examples
    )


def _validated_probabilities(example: CalibrationPrediction) -> dict[str, float]:
    probabilities = dict(example.probabilities)
    if example.truth_id not in probabilities:
        raise ValueError("truth_id must be represented in probabilities")
    if not probabilities:
        raise ValueError("probabilities must not be empty")
    if any(not math.isfinite(value) or not 0 <= value <= 1 for value in probabilities.values()):
        raise ValueError("probabilities must be finite and between zero and one")
    if abs(sum(probabilities.values()) - 1) > 1e-9:
        raise ValueError("probabilities must sum to one")
    return probabilities


def multiclass_brier_score(examples: Sequence[CalibrationPrediction]) -> float:
    scores: list[float] = []
    for example in examples:
        probabilities = _validated_probabilities(example)
        scores.append(
            sum(
                (probability - float(candidate_id == example.truth_id)) ** 2
                for candidate_id, probability in probabilities.items()
            )
        )
    return _mean(scores)


def expected_calibration_error(
    examples: Sequence[CalibrationPrediction],
    *,
    bins: int = 10,
) -> float:
    if bins <= 0:
        raise ValueError("bins must be positive")
    if not examples:
        raise ValueError("at least one example is required")
    buckets: list[list[tuple[float, float]]] = [[] for _ in range(bins)]
    for example in examples:
        probabilities = _validated_probabilities(example)
        predicted_id, confidence = max(probabilities.items(), key=lambda item: (item[1], item[0]))
        bucket = min(int(confidence * bins), bins - 1)
        buckets[bucket].append((confidence, float(predicted_id == example.truth_id)))
    total = len(examples)
    return sum(
        len(bucket)
        / total
        * abs(
            _mean([confidence for confidence, _ in bucket])
            - _mean([correct for _, correct in bucket])
        )
        for bucket in buckets
        if bucket
    )


def retrieval_recall_at_k(examples: Sequence[RetrievalPrediction], *, k: int) -> float:
    if k <= 0:
        raise ValueError("k must be positive")
    recalls: list[float] = []
    for example in examples:
        if not example.relevant_ids:
            raise ValueError("each retrieval example needs at least one relevant ID")
        if len(example.ranked_ids) != len(set(example.ranked_ids)):
            raise ValueError("ranked retrieval IDs must be unique")
        retrieved = set(example.ranked_ids[:k])
        recalls.append(len(retrieved & example.relevant_ids) / len(example.relevant_ids))
    return _mean(recalls)


def _validated_retrieval(example: RetrievalPrediction) -> None:
    if not example.relevant_ids:
        raise ValueError("each retrieval example needs at least one relevant ID")
    if len(example.ranked_ids) != len(set(example.ranked_ids)):
        raise ValueError("ranked retrieval IDs must be unique")


def retrieval_hit_rate_at_k(examples: Sequence[RetrievalPrediction], *, k: int) -> float:
    """Fraction of queries with at least one relevant ID in the first ``k`` results.

    ``k=1`` is top-1 accuracy for queries that may have several relevant items;
    ``retrieval_recall_at_k`` instead measures how much of the relevant set was
    retrieved.
    """

    if k <= 0:
        raise ValueError("k must be positive")
    hits: list[float] = []
    for example in examples:
        _validated_retrieval(example)
        hits.append(float(any(item in example.relevant_ids for item in example.ranked_ids[:k])))
    return _mean(hits)


def mean_reciprocal_rank(examples: Sequence[RetrievalPrediction]) -> float:
    """Mean of ``1 / rank`` of the first relevant result (0 when none is ranked)."""

    reciprocals: list[float] = []
    for example in examples:
        _validated_retrieval(example)
        reciprocal = 0.0
        for index, candidate_id in enumerate(example.ranked_ids, start=1):
            if candidate_id in example.relevant_ids:
                reciprocal = 1 / index
                break
        reciprocals.append(reciprocal)
    return _mean(reciprocals)


def ndcg_at_k(examples: Sequence[RetrievalPrediction], *, k: int) -> float:
    if k <= 0:
        raise ValueError("k must be positive")
    scores: list[float] = []
    for example in examples:
        if not example.relevant_ids:
            raise ValueError("each retrieval example needs at least one relevant ID")
        if len(example.ranked_ids) != len(set(example.ranked_ids)):
            raise ValueError("ranked retrieval IDs must be unique")
        dcg = sum(
            float(candidate_id in example.relevant_ids) / math.log2(index + 2)
            for index, candidate_id in enumerate(example.ranked_ids[:k])
        )
        ideal_count = min(k, len(example.relevant_ids))
        ideal = sum(1 / math.log2(index + 2) for index in range(ideal_count))
        scores.append(dcg / ideal)
    return _mean(scores)


def binary_precision(labels: Sequence[tuple[bool, bool]]) -> float:
    """Return precision from (predicted_positive, actually_positive) pairs."""

    predicted = [actual for selected, actual in labels if selected]
    if not predicted:
        return 0
    return sum(predicted) / len(predicted)


@dataclass(frozen=True, slots=True)
class VerificationPrediction:
    """One verified/relevant pair judgement for geometric verification.

    ``verified`` is the matcher's decision; ``relevant`` is the benchmark
    ground truth for the same query/candidate pair.
    """

    verified: bool
    relevant: bool


def verification_confusion(
    examples: Sequence[VerificationPrediction],
) -> dict[str, int]:
    """Return TP/FP/TN/FN counts for verification decisions."""

    if not examples:
        raise ValueError("at least one example is required")
    counts = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    for example in examples:
        if example.verified and example.relevant:
            counts["tp"] += 1
        elif example.verified and not example.relevant:
            counts["fp"] += 1
        elif not example.verified and not example.relevant:
            counts["tn"] += 1
        else:
            counts["fn"] += 1
    return counts


def verification_precision(examples: Sequence[VerificationPrediction]) -> float | None:
    """TP / (TP + FP); None when nothing was verified (undefined, not zero)."""

    counts = verification_confusion(examples)
    decided = counts["tp"] + counts["fp"]
    if decided == 0:
        return None
    return counts["tp"] / decided


def verification_recall(examples: Sequence[VerificationPrediction]) -> float | None:
    """TP / (TP + FN); None when no positive pairs exist (undefined, not zero)."""

    counts = verification_confusion(examples)
    positives = counts["tp"] + counts["fn"]
    if positives == 0:
        return None
    return counts["tp"] / positives


def verification_f1(examples: Sequence[VerificationPrediction]) -> float | None:
    """Harmonic mean of precision and recall; None when either is undefined."""

    precision = verification_precision(examples)
    recall = verification_recall(examples)
    if precision is None or recall is None:
        return None
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)
