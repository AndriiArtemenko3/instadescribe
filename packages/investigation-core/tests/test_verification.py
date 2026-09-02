from __future__ import annotations

from pathlib import Path

import pytest

from instadescribe_investigation_core import (
    ModelProvenance,
    VerificationPrediction,
    VisualMatch,
    VisualMatcher,
    VisualRetrievalCandidate,
    verification_confusion,
    verification_f1,
    verification_precision,
    verification_recall,
    verify_retrieval_candidates,
)


class RecordingMatcher:
    """A stub VisualMatcher that records calls and echoes deterministic results."""

    def __init__(self) -> None:
        self.calls: list[tuple[Path, Path, float, str, str]] = []

    @property
    def provenance(self) -> ModelProvenance | None:
        return None

    @property
    def network_access(self) -> bool:
        return False

    def verify(
        self,
        query_path: Path,
        candidate_path: Path,
        *,
        embedding_similarity: float,
        query_artifact_id: str,
        candidate_artifact_id: str,
    ) -> VisualMatch:
        self.calls.append(
            (
                query_path,
                candidate_path,
                embedding_similarity,
                query_artifact_id,
                candidate_artifact_id,
            )
        )
        return VisualMatch(
            match_id=f"{query_artifact_id}:{candidate_artifact_id}",
            query_artifact_id=query_artifact_id,
            candidate_artifact_id=candidate_artifact_id,
            embedding_similarity=embedding_similarity,
            feature_matches=0,
            ransac_inliers=0,
            reprojection_error=None,
            verified=False,
            rejection_reason="insufficientFeatures",
        )


def _candidate(
    candidate_id: str, similarity: float, rank: int, ref: str | None
) -> VisualRetrievalCandidate:
    return VisualRetrievalCandidate(
        candidate_id=candidate_id,
        embedding_similarity=similarity,
        rank=rank,
        image_ref=ref,
    )


def test_visual_match_rejects_reason_on_verified_and_blank_reasons() -> None:
    with pytest.raises(ValueError):
        VisualMatch("m", "q", "c", 0.5, 10, 8, 1.0, True, rejection_reason="anything")
    with pytest.raises(ValueError):
        VisualMatch("m", "q", "c", 0.5, 10, 2, 1.0, False, rejection_reason="  ")


def test_visual_match_inlier_ratio_handles_zero_matches() -> None:
    assert VisualMatch("m", "q", "c", 0.5, 0, 0, None, False).ransac_inlier_ratio is None
    assert VisualMatch("m", "q", "c", 0.5, 20, 15, 1.0, True).ransac_inlier_ratio == pytest.approx(
        0.75
    )


def test_bridge_preserves_similarity_and_respects_limit() -> None:
    matcher = RecordingMatcher()
    assert isinstance(matcher, VisualMatcher)
    candidates = [
        _candidate("cand-a", 0.91, 1, "/tmp/a.png"),
        _candidate("cand-b", 0.82, 2, "/tmp/b.png"),
        _candidate("cand-c", 0.60, 3, "/tmp/c.png"),
    ]

    matches = verify_retrieval_candidates(
        Path("/tmp/query.png"), "query-artifact", candidates, matcher, limit=2
    )

    assert len(matches) == 2 and len(matcher.calls) == 2
    assert matches[0].embedding_similarity == pytest.approx(0.91)
    assert matches[1].embedding_similarity == pytest.approx(0.82)
    assert matches[0].candidate_artifact_id == "cand-a"
    assert matcher.calls[0][1] == Path("/tmp/a.png")
    # every requested candidate is verified — no early stop on the first result
    assert [call[4] for call in matcher.calls] == ["cand-a", "cand-b"]


def test_bridge_requires_image_ref_and_positive_limit() -> None:
    matcher = RecordingMatcher()
    with pytest.raises(ValueError, match="image_ref"):
        verify_retrieval_candidates(
            Path("/tmp/q.png"), "query", [_candidate("cand-x", 0.5, 1, None)], matcher
        )
    with pytest.raises(ValueError, match="limit"):
        verify_retrieval_candidates(Path("/tmp/q.png"), "query", [], matcher, limit=0)


def test_verification_metrics_report_confusion_and_scores() -> None:
    examples = [
        VerificationPrediction(verified=True, relevant=True),
        VerificationPrediction(verified=True, relevant=True),
        VerificationPrediction(verified=True, relevant=False),
        VerificationPrediction(verified=False, relevant=True),
        VerificationPrediction(verified=False, relevant=False),
    ]

    counts = verification_confusion(examples)

    assert counts == {"tp": 2, "fp": 1, "tn": 1, "fn": 1}
    assert verification_precision(examples) == pytest.approx(2 / 3)
    assert verification_recall(examples) == pytest.approx(2 / 3)
    assert verification_f1(examples) == pytest.approx(2 / 3)


def test_verification_metrics_are_undefined_not_zero_when_empty_sided() -> None:
    nothing_verified = [VerificationPrediction(verified=False, relevant=True)]
    no_positives = [VerificationPrediction(verified=True, relevant=False)]

    assert verification_precision(nothing_verified) is None
    assert verification_recall(no_positives) is None
    assert verification_f1(nothing_verified) is None
    with pytest.raises(ValueError):
        verification_confusion([])
