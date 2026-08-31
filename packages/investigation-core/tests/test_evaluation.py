from __future__ import annotations

import pytest

from instadescribe_investigation_core import (
    CalibrationPrediction,
    GeolocationPrediction,
    RankedPrediction,
    RetrievalPrediction,
    binary_precision,
    expected_calibration_error,
    haversine_km,
    median_geolocation_error_km,
    multiclass_brier_score,
    ndcg_at_k,
    retrieval_recall_at_k,
    top_k_accuracy,
)


def test_ranking_and_retrieval_metrics() -> None:
    ranking = [
        RankedPrediction(("pl", "sk"), "pl"),
        RankedPrediction(("cz", "sk"), "sk"),
    ]
    retrieval = [RetrievalPrediction(("a", "b", "c"), frozenset({"a", "c"}))]

    assert top_k_accuracy(ranking, k=1) == 0.5
    assert top_k_accuracy(ranking, k=2) == 1
    assert retrieval_recall_at_k(retrieval, k=2) == 0.5
    assert ndcg_at_k(retrieval, k=3) == pytest.approx((1 + 1 / 2) / (1 + 1 / 1.584962500721156))


def test_retrieval_metrics_reject_duplicate_ranked_ids() -> None:
    duplicated = [RetrievalPrediction(("a", "a"), frozenset({"a"}))]

    with pytest.raises(ValueError, match="must be unique"):
        retrieval_recall_at_k(duplicated, k=2)
    with pytest.raises(ValueError, match="must be unique"):
        ndcg_at_k(duplicated, k=2)


def test_geolocation_and_calibration_metrics() -> None:
    coordinates = [GeolocationPrediction(51.5, -0.1, 51.5, -0.1)]
    calibration = [CalibrationPrediction({"pl": 0.8, "sk": 0.2}, "pl")]

    assert haversine_km(0, 0, 0, 0) == 0
    assert median_geolocation_error_km(coordinates) == 0
    assert multiclass_brier_score(calibration) == pytest.approx(0.08)
    assert expected_calibration_error(calibration, bins=5) == pytest.approx(0.2)
    assert binary_precision([(True, True), (True, False), (False, True)]) == 0.5
