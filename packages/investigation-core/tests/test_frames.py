from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import (
    LANDMARK_A,
    LANDMARK_A_VARIANT,
    OPPOSITE_SCENE,
    UNRELATED_SCENE,
    FakeFrameEmbeddingProvider,
)

from instadescribe_investigation_core import (
    FrameDescriptor,
    FrameEmbeddingProvider,
    FrameRejectionReason,
    KeyframeSelectionConfig,
    SelectionWeights,
    cosine_similarity,
    information_score,
    perceptual_hash_distance,
    select_keyframes,
    semantic_novelty,
    vectors,
)


def frame(
    frame_id: str,
    *,
    digest_digit: str,
    shot: int = 0,
    time_ms: int = 0,
    phash: str | None = None,
    novelty: float = 0.5,
    ocr: float = 0,
) -> FrameDescriptor:
    return FrameDescriptor(
        frame_id=frame_id,
        artifact_id=f"artifact-{frame_id}",
        source_content_sha256="f" * 64,
        content_sha256=digest_digit * 64,
        shot_index=shot,
        time_ms=time_ms,
        size_bytes=100,
        width=640,
        height=360,
        perceptual_hash=phash,
        sharpness=0.8,
        exposure_quality=0.8,
        novelty=novelty,
        ocr_density=ocr,
        motion_stability=0.8,
    )


def test_selector_ranks_information_and_removes_exact_duplicate() -> None:
    strongest = frame("strongest", digest_digit="a", time_ms=1000, novelty=1, ocr=1)
    duplicate = frame("duplicate", digest_digit="a", time_ms=2000, novelty=0.2)
    other = frame("other", digest_digit="b", shot=1, time_ms=3000, novelty=0.7)

    result = select_keyframes((duplicate, other, strongest))

    assert [item.artifact.artifact_id for item in result.selected] == [
        "artifact-strongest",
        "artifact-other",
    ]
    assert result.rejected[0].reason is FrameRejectionReason.EXACT_DUPLICATE
    assert result.rejected[0].duplicate_of == "strongest"
    assert result.selected[0].selector_cache_key


def test_selector_applies_perceptual_temporal_and_shot_limits() -> None:
    selected = frame("selected", digest_digit="a", time_ms=1000, phash="0000000000000000")
    perceptual = frame(
        "perceptual",
        digest_digit="b",
        time_ms=2000,
        phash="0000000000000001",
    )
    temporal = frame("temporal", digest_digit="c", time_ms=1100)
    shot_limit = frame("shot-limit", digest_digit="d", time_ms=3000)
    result = select_keyframes(
        (selected, perceptual, temporal, shot_limit),
        config=KeyframeSelectionConfig(
            max_per_shot=1,
            minimum_time_distance_ms=500,
            perceptual_hash_distance=1,
        ),
    )

    reasons = {item.frame_id: item.reason for item in result.rejected}
    assert reasons["perceptual"] is FrameRejectionReason.PERCEPTUAL_DUPLICATE
    assert reasons["temporal"] is FrameRejectionReason.TEMPORAL_NEAR_DUPLICATE
    assert reasons["shot-limit"] is FrameRejectionReason.SHOT_LIMIT


def test_selection_digest_and_cache_keys_are_order_independent() -> None:
    first = frame("first", digest_digit="a", time_ms=1000)
    second = frame("second", digest_digit="b", shot=1, time_ms=2000)

    forward = select_keyframes((first, second))
    reverse = select_keyframes((second, first))

    assert forward.input_digest == reverse.input_digest
    assert [item.selector_cache_key for item in forward.selected] == [
        item.selector_cache_key for item in reverse.selected
    ]


def test_score_and_phash_distance_are_transparent() -> None:
    descriptor = frame("frame", digest_digit="a", novelty=1, ocr=1)
    weights = SelectionWeights(
        sharpness=0,
        exposure_quality=0,
        novelty=1,
        ocr_density=1,
        motion_stability=0,
    )

    assert information_score(descriptor, weights) == 1
    assert perceptual_hash_distance("00", "03") == 2
    assert perceptual_hash_distance("00", "0000") is None


def embedded(
    frame_id: str,
    *,
    digest_digit: str,
    shot: int,
    time_ms: int,
    phash: str,
    embedding: tuple[float, ...] | None,
) -> FrameDescriptor:
    return replace(
        frame(frame_id, digest_digit=digest_digit, shot=shot, time_ms=time_ms, phash=phash),
        embedding=embedding,
    )


def scene_frames() -> tuple[FrameDescriptor, FrameDescriptor, FrameDescriptor]:
    """Three frames with equal scalar features and pHashes far apart.

    A: landmark; B: same landmark, different pixels; C: unrelated street.
    """

    a = embedded(
        "A", digest_digit="a", shot=0, time_ms=1000, phash="0000000000000000", embedding=LANDMARK_A
    )
    b = embedded(
        "B",
        digest_digit="b",
        shot=1,
        time_ms=2000,
        phash="ffffffffffffffff",
        embedding=LANDMARK_A_VARIANT,
    )
    c = embedded(
        "C",
        digest_digit="c",
        shot=2,
        time_ms=3000,
        phash="00000000ffffffff",
        embedding=UNRELATED_SCENE,
    )
    return a, b, c


def test_semantic_novelty_follows_cosine_similarity() -> None:
    similarity, novelty = semantic_novelty((0.99, 0.05), [(1.0, 0.0)])
    assert similarity == pytest.approx(0.9987, abs=1e-3)
    assert novelty == pytest.approx(1 - similarity)
    assert novelty < 0.01

    assert semantic_novelty((0.0, 1.0), [(1.0, 0.0)]) == (0.0, 1.0)


def test_semantic_novelty_of_first_keyframe_is_maximal() -> None:
    assert semantic_novelty((0.5, 0.5), []) == (None, 1.0)


def test_semantic_novelty_saturates_for_negative_similarity() -> None:
    similarity, novelty = semantic_novelty((-1.0, 0.0), [(1.0, 0.0), (0.0, 1.0)])
    assert similarity == pytest.approx(0)
    assert novelty == 1.0
    similarity, novelty = semantic_novelty((-1.0, 0.0), [(1.0, 0.0)])
    assert similarity == pytest.approx(-1)
    assert novelty == 1.0


def test_semantic_novelty_uses_highest_similarity_and_clamps_overshoot(monkeypatch) -> None:
    monkeypatch.setattr(vectors, "dot_product", lambda a, b: 1.0000000002)
    assert semantic_novelty((1.0, 0.0), [(1.0, 0.0)]) == (1.0, 0.0)


def test_semantic_weight_ranks_redundant_embedding_below_novel_scene() -> None:
    a, b, c = scene_frames()
    result = select_keyframes(
        (b, c, a),
        config=KeyframeSelectionConfig(weights=SelectionWeights(semantic_novelty=0.3)),
    )

    assert [item.artifact.artifact_id for item in result.selected] == [
        "artifact-A",
        "artifact-C",
        "artifact-B",
    ]
    assert result.rejected == ()
    first, novel, redundant = result.selected
    assert (first.embedding_similarity_max, first.semantic_novelty) == (None, 1.0)
    assert novel.embedding_similarity_max < 0.1
    assert novel.semantic_novelty > 0.9
    assert redundant.embedding_similarity_max > 0.99
    assert redundant.semantic_novelty < 0.01
    assert redundant.information_score < novel.information_score < first.information_score
    assert redundant.semantic_novelty == pytest.approx(1 - redundant.embedding_similarity_max)


def test_semantic_gate_and_perceptual_gate_reject_independently() -> None:
    a, b, c = scene_frames()
    d = embedded(
        "D",
        digest_digit="d",
        shot=3,
        time_ms=4000,
        phash="0000000000000001",
        embedding=OPPOSITE_SCENE,
    )
    result = select_keyframes(
        (a, b, c, d),
        config=KeyframeSelectionConfig(semantic_similarity_threshold=0.9),
    )

    assert [item.artifact.artifact_id for item in result.selected] == [
        "artifact-A",
        "artifact-C",
    ]
    rejections = {item.frame_id: item for item in result.rejected}
    assert rejections["B"].reason is FrameRejectionReason.SEMANTIC_DUPLICATE
    assert rejections["B"].duplicate_of == "A"
    assert rejections["D"].reason is FrameRejectionReason.PERCEPTUAL_DUPLICATE
    assert rejections["D"].duplicate_of == "A"
    assert result.selected[1].embedding_similarity_max == pytest.approx(
        cosine_similarity(UNRELATED_SCENE, LANDMARK_A)
    )


def test_embeddings_do_not_change_selection_when_semantic_feature_is_off() -> None:
    a, b, c = scene_frames()
    extra = embedded(
        "E",
        digest_digit="e",
        shot=0,
        time_ms=1200,
        phash="0000000000000001",
        embedding=OPPOSITE_SCENE,
    )
    late = embedded(
        "F", digest_digit="f", shot=4, time_ms=9000, phash="f0f0f0f0f0f0f0f0", embedding=LANDMARK_A
    )
    with_embeddings = (a, b, c, extra, late)
    without_embeddings = tuple(replace(item, embedding=None) for item in with_embeddings)
    config = KeyframeSelectionConfig(max_keyframes=3)

    embedded_result = select_keyframes(with_embeddings, config=config)
    plain_result = select_keyframes(without_embeddings, config=config)

    assert [item.artifact for item in embedded_result.selected] == [
        item.artifact for item in plain_result.selected
    ]
    assert [item.information_score for item in embedded_result.selected] == [
        item.information_score for item in plain_result.selected
    ]
    assert embedded_result.rejected == plain_result.rejected
    assert [item.reason for item in plain_result.rejected] == [
        FrameRejectionReason.PERCEPTUAL_DUPLICATE,
        FrameRejectionReason.GLOBAL_LIMIT,
    ]
    assert [item.semantic_novelty for item in plain_result.selected] == [None, None, None]
    assert embedded_result.selected[0].semantic_novelty == 1.0
    assert embedded_result.selected[1].embedding_similarity_max > 0.99
    assert embedded_result.selected[2].embedding_similarity_max < 0.1


def test_selector_validates_embedding_dimensions_and_coverage() -> None:
    a, b, c = scene_frames()
    with pytest.raises(ValueError, match="share one dimension"):
        select_keyframes((a, replace(b, embedding=(1.0, 0.0))))
    with pytest.raises(ValueError, match="every frame needs an embedding"):
        select_keyframes(
            (a, replace(b, embedding=None)),
            config=KeyframeSelectionConfig(semantic_similarity_threshold=0.9),
        )
    with pytest.raises(ValueError, match="every frame needs an embedding"):
        select_keyframes(
            (a, replace(c, embedding=None)),
            config=KeyframeSelectionConfig(weights=SelectionWeights(semantic_novelty=0.1)),
        )


def test_frame_descriptor_validates_embeddings() -> None:
    a, _, _ = scene_frames()
    for bad in ([1.0, 0.0], (), (0.0, 0.0), (math.nan, 1.0), tuple([1.0] * 4097)):
        with pytest.raises(ValueError):
            replace(a, embedding=bad)
    with pytest.raises(ValueError, match="semantic_similarity_threshold"):
        KeyframeSelectionConfig(semantic_similarity_threshold=1.5)
    with pytest.raises(ValueError, match="semantic_novelty weight"):
        SelectionWeights(semantic_novelty=-0.1)


def test_all_ones_frame_scores_exactly_one_with_semantic_weight() -> None:
    descriptor = replace(
        frame("frame", digest_digit="a", novelty=1, ocr=1),
        sharpness=1,
        exposure_quality=1,
        motion_stability=1,
        embedding=LANDMARK_A,
    )
    weights = SelectionWeights(semantic_novelty=0.3)

    assert information_score(descriptor, weights) == 1.0
    assert (
        select_keyframes((descriptor,), config=KeyframeSelectionConfig(weights=weights))
        .selected[0]
        .information_score
        == 1.0
    )


def test_fake_embedding_provider_satisfies_the_adapter_seam() -> None:
    provider = FakeFrameEmbeddingProvider({"a.jpg": LANDMARK_A})
    a, _, _ = scene_frames()

    assert isinstance(provider, FrameEmbeddingProvider)
    assert provider.network_access is False
    assert replace(a, embedding=None).embedding is None
    assert replace(a, embedding=provider.embed_frame(Path("frames/a.jpg"))).embedding == LANDMARK_A
