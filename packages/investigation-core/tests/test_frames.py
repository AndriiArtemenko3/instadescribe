from __future__ import annotations

from instadescribe_investigation_core import (
    FrameDescriptor,
    FrameRejectionReason,
    KeyframeSelectionConfig,
    SelectionWeights,
    information_score,
    perceptual_hash_distance,
    select_keyframes,
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
