from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
from instadescribe_investigation_core import (
    InMemoryVisualCandidateRetriever,
    VisualCandidate,
    VisualMatcher,
    verify_retrieval_candidates,
)
from instadescribe_worker.failures import FailureCode, JobFailure
from instadescribe_worker.visual_verification import (
    HOMOGRAPHY_MINIMUM_CORRESPONDENCES,
    REASON_INSUFFICIENT_FEATURES,
    SiftRansacVisualMatcher,
    VerificationConfig,
)
from PIL import Image, ImageDraw, ImageEnhance


def _textured_image(path: Path, *, seed: int, shapes: bool = True) -> Path:
    """A deterministic, feature-rich image: seeded noise plus seed-placed shapes.

    The shapes are positioned FROM the seed so two different seeds never share
    aligned structure — identical drawn geometry across "unrelated" images
    would (correctly) verify, because the images really would share geometry.
    """

    rng = np.random.default_rng(seed)
    pixels = rng.integers(0, 256, size=(240, 320, 3), dtype=np.uint8)
    image = Image.fromarray(pixels, "RGB")
    if shapes:
        draw = ImageDraw.Draw(image)
        x0, y0 = int(rng.integers(10, 120)), int(rng.integers(10, 80))
        draw.ellipse(
            (x0, y0, x0 + 110, y0 + 100), fill=tuple(int(v) for v in rng.integers(30, 220, 3))
        )
        x1, y1 = int(rng.integers(140, 210)), int(rng.integers(90, 130))
        draw.rectangle(
            (x1, y1, x1 + 90, y1 + 90), fill=tuple(int(v) for v in rng.integers(30, 220, 3))
        )
    image.save(path)
    return path


def _transformed_copy(source: Path, destination: Path) -> Path:
    """Rotation + crop + brightness change: the positive-pair transform."""

    with Image.open(source) as image:
        rotated = image.convert("RGB").rotate(8, resample=Image.Resampling.BICUBIC)
        cropped = rotated.crop((15, 10, 305, 230))
        ImageEnhance.Brightness(cropped).enhance(0.85).save(destination)
    return destination


@pytest.fixture()
def matcher() -> SiftRansacVisualMatcher:
    return SiftRansacVisualMatcher()


@pytest.fixture()
def query_image(tmp_path: Path) -> Path:
    return _textured_image(tmp_path / "query.png", seed=3)


def _verify(
    matcher: SiftRansacVisualMatcher, query: Path, candidate: Path, similarity: float = 0.9
):
    return matcher.verify(
        query,
        candidate,
        embedding_similarity=similarity,
        query_artifact_id="query-artifact",
        candidate_artifact_id="candidate-artifact",
    )


def test_matcher_satisfies_core_protocol(matcher: SiftRansacVisualMatcher) -> None:
    assert isinstance(matcher, VisualMatcher)
    assert matcher.network_access is False


def test_transformed_positive_pair_verifies(
    matcher: SiftRansacVisualMatcher, query_image: Path, tmp_path: Path
) -> None:
    positive = _transformed_copy(query_image, tmp_path / "positive.png")

    match = _verify(matcher, query_image, positive, similarity=0.93)

    assert match.verified is True
    assert match.rejection_reason is None
    assert match.feature_matches >= matcher.config.minimum_feature_matches
    assert match.ransac_inliers >= matcher.config.minimum_ransac_inliers
    assert match.ransac_inlier_ratio is not None
    assert match.ransac_inlier_ratio >= matcher.config.minimum_ransac_inlier_ratio
    assert match.reprojection_error is not None
    assert match.reprojection_error < matcher.config.ransac_reprojection_threshold
    assert match.embedding_similarity == pytest.approx(0.93)


def test_unrelated_images_do_not_verify(
    matcher: SiftRansacVisualMatcher, query_image: Path, tmp_path: Path
) -> None:
    unrelated = _textured_image(tmp_path / "unrelated.png", seed=99)

    match = _verify(matcher, query_image, unrelated)

    assert match.verified is False
    assert match.rejection_reason is not None
    assert match.ransac_inliers < matcher.config.minimum_ransac_inliers


def test_high_cosine_alone_cannot_verify(
    matcher: SiftRansacVisualMatcher, query_image: Path, tmp_path: Path
) -> None:
    """The reason this stage exists: a perfect retrieval cosine with no geometry."""

    unrelated = _textured_image(tmp_path / "unrelated.png", seed=42)

    match = _verify(matcher, query_image, unrelated, similarity=1.0)

    assert match.embedding_similarity == pytest.approx(1.0)
    assert match.verified is False


def test_blank_image_fails_safely_with_named_reason(
    matcher: SiftRansacVisualMatcher, query_image: Path, tmp_path: Path
) -> None:
    blank = tmp_path / "blank.png"
    Image.new("RGB", (240, 320), (255, 255, 255)).save(blank)

    match = _verify(matcher, query_image, blank)

    assert match.verified is False
    assert match.rejection_reason == REASON_INSUFFICIENT_FEATURES
    assert match.feature_matches == 0 and match.ransac_inliers == 0


def test_missing_and_undecodable_images_raise_not_unverified(
    matcher: SiftRansacVisualMatcher, query_image: Path, tmp_path: Path
) -> None:
    with pytest.raises(JobFailure) as missing:
        _verify(matcher, query_image, tmp_path / "does-not-exist.png")
    assert missing.value.code is FailureCode.INVALID_MEDIA

    garbage = tmp_path / "garbage.png"
    garbage.write_bytes(b"this is not an image at all")
    with pytest.raises(JobFailure) as unreadable:
        _verify(matcher, query_image, garbage)
    assert unreadable.value.code is FailureCode.INVALID_MEDIA


def test_identical_images_verify(matcher: SiftRansacVisualMatcher, query_image: Path) -> None:
    match = _verify(matcher, query_image, query_image)

    assert match.verified is True
    assert match.ransac_inlier_ratio is not None
    assert match.ransac_inlier_ratio > 0.9


def test_config_rejects_impossible_thresholds() -> None:
    with pytest.raises(ValueError):
        VerificationConfig(descriptor_ratio_threshold=1.5)
    with pytest.raises(ValueError):
        VerificationConfig(minimum_feature_matches=HOMOGRAPHY_MINIMUM_CORRESPONDENCES - 1)
    with pytest.raises(ValueError):
        VerificationConfig(minimum_ransac_inliers=2)
    with pytest.raises(ValueError):
        VerificationConfig(ransac_reprojection_threshold=0)
    with pytest.raises(ValueError):
        VerificationConfig(minimum_ransac_inlier_ratio=0)
    with pytest.raises(ValueError):
        VerificationConfig(feature_cache_size=0)


def test_verification_is_deterministic_for_a_pair(
    matcher: SiftRansacVisualMatcher, query_image: Path, tmp_path: Path
) -> None:
    positive = _transformed_copy(query_image, tmp_path / "positive.png")

    first = _verify(matcher, query_image, positive)
    second = _verify(matcher, query_image, positive)

    assert first.verified is second.verified is True
    assert first.feature_matches == second.feature_matches
    assert first.ransac_inliers == second.ransac_inliers


def test_query_features_are_extracted_once_across_top_k(
    query_image: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    matcher = SiftRansacVisualMatcher()
    candidates = [
        _transformed_copy(query_image, tmp_path / "candidate-a.png"),
        _textured_image(tmp_path / "candidate-b.png", seed=11),
        _textured_image(tmp_path / "candidate-c.png", seed=12),
    ]
    decoded: list[Path] = []
    original = SiftRansacVisualMatcher._decode_grayscale

    def counting(self: SiftRansacVisualMatcher, path: Path, label: str):
        decoded.append(path)
        return original(self, path, label)

    monkeypatch.setattr(SiftRansacVisualMatcher, "_decode_grayscale", counting)

    for candidate in candidates:
        _verify(matcher, query_image, candidate)

    query_decodes = [path for path in decoded if path.name == "query.png"]
    assert len(query_decodes) == 1  # cached after the first pair
    assert len(decoded) == 1 + len(candidates)


def test_bridge_runs_top_k_and_preserves_retrieval_cosine(
    query_image: Path, tmp_path: Path
) -> None:
    matcher = SiftRansacVisualMatcher()
    positive = _transformed_copy(query_image, tmp_path / "positive.png")
    distractor = _textured_image(tmp_path / "distractor.png", seed=21)
    retriever = InMemoryVisualCandidateRetriever(
        [
            VisualCandidate("positive", (0.9, 0.1, 0.05), image_ref=str(positive)),
            VisualCandidate("distractor", (0.7, 0.6, 0.1), image_ref=str(distractor)),
        ]
    )
    candidates = retriever.retrieve((1.0, 0.0, 0.0), limit=2)

    matches = verify_retrieval_candidates(query_image, "query-artifact", candidates, matcher)

    assert len(matches) == 2  # every candidate verified — no early stop
    by_id = {match.candidate_artifact_id: match for match in matches}
    assert by_id["positive"].verified is True
    assert by_id["distractor"].verified is False
    for match, candidate in zip(matches, candidates, strict=True):
        assert match.embedding_similarity == pytest.approx(candidate.embedding_similarity)


REAL_MODEL = os.environ.get("INSTADESCRIBE_TEST_FRAME_EMBEDDING_MODEL")


@pytest.mark.skipif(
    not REAL_MODEL,
    reason="INSTADESCRIBE_TEST_FRAME_EMBEDDING_MODEL not set (path to a CLIP vision ONNX export)",
)
def test_real_clip_retrieval_then_geometric_verification(tmp_path: Path) -> None:
    """CLIP embedding -> exact retrieval -> Top-K -> SIFT/RANSAC -> VisualMatch."""

    from instadescribe_worker.frame_embeddings import OnnxClipFrameEmbeddingProvider

    provider = OnnxClipFrameEmbeddingProvider(Path(REAL_MODEL))
    query = _textured_image(tmp_path / "query.png", seed=3)
    positive = _transformed_copy(query, tmp_path / "positive.png")
    distractor = _textured_image(tmp_path / "distractor.png", seed=55)
    retriever = InMemoryVisualCandidateRetriever(
        [
            VisualCandidate("positive", provider.embed_frame(positive), image_ref=str(positive)),
            VisualCandidate(
                "distractor", provider.embed_frame(distractor), image_ref=str(distractor)
            ),
        ]
    )
    candidates = retriever.retrieve(provider.embed_frame(query), limit=2)
    assert candidates[0].candidate_id == "positive"  # relevant candidate retrieved first

    matcher = SiftRansacVisualMatcher()
    matches = verify_retrieval_candidates(query, "query-artifact", candidates, matcher)

    by_id = {match.candidate_artifact_id: match for match in matches}
    assert by_id["positive"].verified is True
    assert by_id["distractor"].verified is False
