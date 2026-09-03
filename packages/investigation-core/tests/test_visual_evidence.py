from __future__ import annotations

import pytest

from instadescribe_investigation_core import (
    BeliefConfig,
    CandidatePrior,
    EvidenceContribution,
    EvidenceItem,
    EvidenceKind,
    VerificationState,
    VisualCandidateBinding,
    VisualEvidenceConfig,
    VisualMatch,
    update_beliefs,
    visual_evidence_correlation_group,
    visual_evidence_id,
    visual_match_to_evidence,
)

MATCHER = "sift-ransac-homography"
ENABLED = VisualEvidenceConfig(enabled=True)

HYPOTHESES = (
    CandidatePrior("location:london", "London", 1 / 3),
    CandidatePrior("location:paris", "Paris", 1 / 3),
    CandidatePrior("location:amsterdam", "Amsterdam", 1 / 3),
)


def _match(
    *,
    candidate: str = "ref-london-01",
    verified: bool = True,
    query_artifact: str = "frame-0001",
    similarity: float = 0.8536,
    matches: int = 83,
    inliers: int = 71,
    reason: str | None = None,
) -> VisualMatch:
    return VisualMatch(
        match_id=f"visual-match:{query_artifact}:{candidate}",
        query_artifact_id=query_artifact,
        candidate_artifact_id=candidate,
        embedding_similarity=similarity,
        feature_matches=matches,
        ransac_inliers=inliers,
        reprojection_error=1.2 if verified else None,
        verified=verified,
        rejection_reason=reason,
    )


def _binding(
    candidate: str = "ref-london-01",
    hypothesis: str = "location:london",
    source: str = "capture:london:trafalgar-01",
) -> VisualCandidateBinding:
    return VisualCandidateBinding(
        candidate_id=candidate, hypothesis_id=hypothesis, source_observation_id=source
    )


def _evidence(match: VisualMatch, binding: VisualCandidateBinding, **kwargs) -> EvidenceItem | None:
    defaults = {
        "query_observation_id": "shot-01",
        "query_source_id": "source-video-01",
        "matcher": MATCHER,
        "config": ENABLED,
    }
    defaults.update(kwargs)
    return visual_match_to_evidence(match, binding, **defaults)


# --- conversion semantics -------------------------------------------------


def test_verified_match_becomes_one_positive_visual_evidence_item() -> None:
    item = _evidence(_match(), _binding(), retrieval_rank=4)

    assert item is not None
    assert item.kind is EvidenceKind.VISUAL_MATCH
    assert item.verification_state is VerificationState.VERIFIED
    # exactly one contribution: one observation must not be counted several times
    assert len(item.contributions) == 1
    assert item.contributions[0].candidate_id == "location:london"
    assert item.contributions[0].score == pytest.approx(1.0)
    assert item.reliability == pytest.approx(1.0)
    assert item.artifact_id == "frame-0001"
    # diagnostics travel as attributes, not as extra score contributions
    assert item.attributes["embeddingSimilarity"] == pytest.approx(0.8536)
    assert item.attributes["featureMatchCount"] == 83
    assert item.attributes["ransacInlierCount"] == 71
    assert item.attributes["ransacInlierRatio"] == pytest.approx(71 / 83)
    assert item.attributes["reprojectionError"] == pytest.approx(1.2)
    assert item.attributes["retrievalRank"] == 4
    assert item.attributes["candidateSourceObservationId"] == "capture:london:trafalgar-01"


def test_unverified_match_produces_no_evidence_and_never_negative_support() -> None:
    for reason in ("insufficientInliers", "lowInlierRatio", "homographyNotFound"):
        match = _match(verified=False, inliers=3, reason=reason)
        assert _evidence(match, _binding()) is None


def test_high_cosine_unverified_match_cannot_update_beliefs() -> None:
    """A perfect retrieval cosine with failed geometry must not move beliefs."""

    baseline = update_beliefs(HYPOTHESES, ())
    match = _match(verified=False, similarity=1.0, matches=17, inliers=2, reason="lowInlierRatio")

    item = _evidence(match, _binding())
    assert item is None

    after = update_beliefs(HYPOTHESES, ())
    assert [(b.candidate_id, b.probability) for b in after.candidates] == [
        (b.candidate_id, b.probability) for b in baseline.candidates
    ]


def test_disabled_by_default_and_when_disabled_no_evidence_is_produced() -> None:
    assert VisualEvidenceConfig().enabled is False
    assert (
        visual_match_to_evidence(
            _match(),
            _binding(),
            query_observation_id="shot-01",
            query_source_id="source-video-01",
            matcher=MATCHER,
        )
        is None
    )


def test_binding_mismatch_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="binding is for candidate"):
        _evidence(_match(candidate="ref-london-01"), _binding(candidate="ref-paris-09"))


def test_binding_requires_all_identifiers() -> None:
    for kwargs in (
        {"candidate_id": " "},
        {"hypothesis_id": ""},
        {"source_observation_id": "  "},
    ):
        base = {
            "candidate_id": "ref",
            "hypothesis_id": "location:london",
            "source_observation_id": "capture",
        }
        base.update(kwargs)
        with pytest.raises(ValueError):
            VisualCandidateBinding(**base)


def test_config_rejects_out_of_range_magnitudes() -> None:
    for kwargs in ({"support_score": 0}, {"support_score": 1.5}, {"reliability": 0}):
        with pytest.raises(ValueError):
            VisualEvidenceConfig(enabled=True, **kwargs)


def test_evidence_identity_is_stable_and_ignores_geometry() -> None:
    strong = _evidence(_match(matches=900, inliers=880), _binding())
    weak = _evidence(_match(matches=20, inliers=12), _binding())

    assert strong is not None and weak is not None
    # same observation, same binding -> same identity, whatever the geometry
    assert strong.evidence_id == weak.evidence_id
    assert strong.evidence_id == visual_evidence_id("shot-01", _binding(), matcher=MATCHER)
    assert strong.evidence_id.startswith("visual-match-")
    # a different hypothesis is a different claim
    other = visual_evidence_id("shot-01", _binding(hypothesis="location:paris"), matcher=MATCHER)
    assert other != strong.evidence_id


def test_unknown_hypothesis_fails_in_the_belief_layer() -> None:
    item = _evidence(_match(), _binding(hypothesis="location:atlantis"))
    assert item is not None
    with pytest.raises(ValueError, match="unknown candidate"):
        update_beliefs(HYPOTHESES, (item,))


# --- correlation ----------------------------------------------------------


def test_correlation_group_identifies_the_observation_not_the_claim() -> None:
    london = visual_evidence_correlation_group("shot-01", "capture:london:trafalgar-01")
    paris = visual_evidence_correlation_group("shot-01", "capture:paris:eiffel-04")

    assert london == "visual:shot-01:capture:london:trafalgar-01"
    assert london != paris
    # the hypothesis is deliberately absent from the key
    assert "location:" not in london


def _score_for(evidence: tuple[EvidenceItem, ...], hypothesis: str) -> float:
    snapshot = update_beliefs(HYPOTHESES, evidence)
    return next(b.log_score for b in snapshot.candidates if b.candidate_id == hypothesis)


def test_correlated_variants_do_not_multiply_support() -> None:
    """Three transformed variants of one reference capture are one observation."""

    single = _evidence(_match(candidate="ref-london-01"), _binding("ref-london-01"))
    variants = tuple(
        _evidence(_match(candidate=name), _binding(name))  # same source_observation_id
        for name in ("ref-london-01", "ref-london-01-crop", "ref-london-01-bright")
    )
    assert all(item is not None for item in variants)
    assert len({item.correlation_group for item in variants}) == 1

    one = _score_for((single,), "location:london")
    three = _score_for(variants, "location:london")

    baseline = _score_for((), "location:london")
    delta_one = one - baseline
    delta_three = three - baseline
    assert delta_three == pytest.approx(delta_one)  # max-per-group, not a sum
    assert delta_three < 3 * delta_one


def test_independent_observations_compound() -> None:
    """Different query shots against different reference captures are independent."""

    first = visual_match_to_evidence(
        _match(candidate="ref-london-01", query_artifact="frame-0001"),
        _binding("ref-london-01", source="capture:london:trafalgar-01"),
        query_observation_id="shot-01",
        query_source_id="source-video-01",
        matcher=MATCHER,
        config=ENABLED,
    )
    second = visual_match_to_evidence(
        _match(candidate="ref-london-77", query_artifact="frame-0900"),
        _binding("ref-london-77", source="capture:london:southbank-12"),
        query_observation_id="shot-07",
        query_source_id="source-video-01",
        matcher=MATCHER,
        config=ENABLED,
    )
    assert first is not None and second is not None
    assert first.correlation_group != second.correlation_group

    baseline = _score_for((), "location:london")
    delta_one = _score_for((first,), "location:london") - baseline
    delta_two = _score_for((first, second), "location:london") - baseline

    assert delta_two == pytest.approx(2 * delta_one)
    assert delta_two > delta_one


def test_replaying_the_same_match_does_not_double_count() -> None:
    item = _evidence(_match(), _binding())
    assert item is not None

    once = _score_for((item,), "location:london")
    twice = _score_for((item, item), "location:london")

    assert twice == pytest.approx(once)


# --- belief integration ---------------------------------------------------


def test_verified_visual_match_moves_only_its_hypothesis() -> None:
    baseline = update_beliefs(HYPOTHESES, ())
    item = _evidence(_match(), _binding())
    assert item is not None

    after = update_beliefs(HYPOTHESES, (item,))

    before_by_id = {b.candidate_id: b for b in baseline.candidates}
    after_by_id = {b.candidate_id: b for b in after.candidates}
    assert after_by_id["location:london"].log_score > before_by_id["location:london"].log_score
    assert after_by_id["location:london"].probability > before_by_id["location:london"].probability
    # competitors keep their log-scores; only the shared softmax denominator moves
    for other in ("location:paris", "location:amsterdam"):
        assert after_by_id[other].log_score == pytest.approx(before_by_id[other].log_score)
        assert after_by_id[other].probability < before_by_id[other].probability


def test_visual_evidence_shares_the_fusion_path_with_other_kinds() -> None:
    ocr = EvidenceItem(
        evidence_id="ocr-0001",
        observation="A street sign reads Rue de Rivoli.",
        source_id="source-video-01",
        artifact_id="frame-0500",
        correlation_group="frame-abc123",
        reliability=1.0,
        contributions=(EvidenceContribution(candidate_id="location:paris", score=1.0),),
        kind=EvidenceKind.OCR,
    )
    visual = _evidence(_match(), _binding())
    assert visual is not None

    snapshot = update_beliefs(HYPOTHESES, (ocr, visual))
    by_id = {b.candidate_id: b for b in snapshot.candidates}

    # both kinds contribute through the same grouped-score aggregation
    assert by_id["location:london"].group_scores[visual.correlation_group] == pytest.approx(1.0)
    assert by_id["location:paris"].group_scores["frame-abc123"] == pytest.approx(1.0)
    assert by_id["location:london"].probability == pytest.approx(
        by_id["location:paris"].probability
    )
    assert by_id["location:amsterdam"].probability < by_id["location:london"].probability


def test_beliefs_unchanged_when_no_verified_visual_matches_exist() -> None:
    unverified = [
        _evidence(_match(verified=False, reason="insufficientInliers"), _binding()),
        _evidence(_match(verified=False, reason="lowInlierRatio"), _binding()),
    ]
    assert unverified == [None, None]

    baseline = update_beliefs(HYPOTHESES, (), config=BeliefConfig())
    after = update_beliefs(HYPOTHESES, tuple(i for i in unverified if i), config=BeliefConfig())

    assert after.snapshot_id == baseline.snapshot_id
