from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from keyframe_semantic_eval import (  # noqa: E402
    pairwise_similarity_summary,
    semantic_redundancy,
)

LANDMARK_A = (0.82, 0.11, 0.43, 0.05)
LANDMARK_A_VARIANT = (0.80, 0.13, 0.45, 0.07)
UNRELATED_SCENE = (-0.10, 0.78, 0.04, 0.60)


def test_semantic_redundancy_separates_redundant_and_diverse_selections():
    redundant = semantic_redundancy([LANDMARK_A, LANDMARK_A_VARIANT, UNRELATED_SCENE])
    diverse = semantic_redundancy([LANDMARK_A, UNRELATED_SCENE])

    assert redundant is not None and diverse is not None
    assert redundant > 0.6
    assert diverse < 0.1
    assert redundant > diverse


def test_semantic_redundancy_is_undefined_below_two_frames():
    assert semantic_redundancy([]) is None
    assert semantic_redundancy([LANDMARK_A]) is None
    assert pairwise_similarity_summary([LANDMARK_A]) is None


def test_pairwise_summary_reports_mean_median_and_maximum():
    summary = pairwise_similarity_summary([LANDMARK_A, LANDMARK_A_VARIANT, UNRELATED_SCENE])

    assert summary is not None
    assert set(summary) == {"mean", "median", "max"}
    assert summary["max"] == pytest.approx(0.9991, abs=1e-3)
    assert summary["mean"] < summary["max"]
