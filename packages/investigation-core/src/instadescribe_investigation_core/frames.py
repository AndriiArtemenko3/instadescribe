"""Deterministic, dependency-light shot/keyframe selection primitives.

Redundant frames are detected in two independent layers:

- perceptual redundancy: DCT pHash + Hamming distance rejects frames whose pixels are
  near-identical (``perceptual_hash_distance``);
- semantic redundancy: optional embedding vectors + cosine similarity score how much
  of a candidate's meaning is already covered by selected keyframes
  (``semantic_novelty``).

Embeddings are optional. Without them, or with the semantic weight and threshold left
at their defaults, selection is identical to the pHash-only behaviour.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

from .models import ArtifactRef, Keyframe
from .serialization import canonical_json
from .vectors import cosine_similarity, validate_embedding


def _validate_sha256(value: str, field_name: str) -> None:
    if len(value) != 64:
        raise ValueError(f"{field_name} must be a 64-character hexadecimal digest")
    try:
        int(value, 16)
    except ValueError as error:
        raise ValueError(f"{field_name} must be hexadecimal") from error


def _bounded_score(value: float, field_name: str) -> None:
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"{field_name} must be finite and between zero and one")


@dataclass(frozen=True, slots=True)
class ShotBoundary:
    shot_index: int
    start_ms: int
    end_ms: int
    detector_score: float | None = None

    def __post_init__(self) -> None:
        if self.shot_index < 0:
            raise ValueError("shot_index must not be negative")
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise ValueError("a shot must have a non-negative start before its end")
        if self.detector_score is not None:
            _bounded_score(self.detector_score, "detector_score")


@dataclass(frozen=True, slots=True)
class FrameDescriptor:
    """Small persisted descriptor produced before any heavyweight VLM inference."""

    frame_id: str
    artifact_id: str
    source_content_sha256: str
    content_sha256: str
    shot_index: int
    time_ms: int
    size_bytes: int
    width: int
    height: int
    media_type: str = "image/jpeg"
    perceptual_hash: str | None = None
    sharpness: float = 0
    exposure_quality: float = 0
    novelty: float = 0
    ocr_density: float = 0
    motion_stability: float = 0
    # Optional semantic embedding produced by a FrameEmbeddingProvider. Only its
    # direction matters: the selector compares embeddings with cosine similarity.
    embedding: tuple[float, ...] | None = None

    def __post_init__(self) -> None:
        for value, field_name in (
            (self.frame_id, "frame_id"),
            (self.artifact_id, "artifact_id"),
            (self.media_type, "media_type"),
        ):
            if not value.strip():
                raise ValueError(f"{field_name} must not be empty")
        _validate_sha256(self.source_content_sha256, "source_content_sha256")
        _validate_sha256(self.content_sha256, "content_sha256")
        if self.shot_index < 0 or self.time_ms < 0 or self.size_bytes < 0:
            raise ValueError("shot_index, time_ms and size_bytes must not be negative")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("frame dimensions must be positive")
        if self.perceptual_hash is not None:
            if not self.perceptual_hash or len(self.perceptual_hash) % 2:
                raise ValueError("perceptual_hash must contain an even number of hex digits")
            try:
                int(self.perceptual_hash, 16)
            except ValueError as error:
                raise ValueError("perceptual_hash must be hexadecimal") from error
        for field_name in (
            "sharpness",
            "exposure_quality",
            "novelty",
            "ocr_density",
            "motion_stability",
        ):
            _bounded_score(getattr(self, field_name), field_name)
        if self.embedding is not None:
            validate_embedding(self.embedding, "embedding")

    def artifact(self) -> ArtifactRef:
        return ArtifactRef(
            artifact_id=self.artifact_id,
            content_sha256=self.content_sha256,
            media_type=self.media_type,
            size_bytes=self.size_bytes,
            frame_time_ms=self.time_ms,
            perceptual_hash=self.perceptual_hash,
        )


@dataclass(frozen=True, slots=True)
class SelectionWeights:
    sharpness: float = 0.25
    exposure_quality: float = 0.15
    novelty: float = 0.30
    ocr_density: float = 0.20
    motion_stability: float = 0.10
    # Weight of the embedding-based semantic novelty term. Zero keeps the score
    # identical to the pHash-only selector even when embeddings are present.
    semantic_novelty: float = 0.0

    def __post_init__(self) -> None:
        for field_name in (
            "sharpness",
            "exposure_quality",
            "novelty",
            "ocr_density",
            "motion_stability",
            "semantic_novelty",
        ):
            value = getattr(self, field_name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{field_name} weight must be finite and non-negative")
        if self.total <= 0:
            raise ValueError("at least one selection weight must be positive")

    @property
    def total(self) -> float:
        # Keep this summation order identical to information_score so that a frame
        # scoring one on every feature divides to exactly 1.0.
        return (
            self.sharpness
            + self.exposure_quality
            + self.novelty
            + self.ocr_density
            + self.motion_stability
            + self.semantic_novelty
        )


@dataclass(frozen=True, slots=True)
class KeyframeSelectionConfig:
    max_keyframes: int = 24
    max_per_shot: int = 3
    minimum_time_distance_ms: int = 500
    perceptual_hash_distance: int = 6
    # Optional layer-2 gate: reject a candidate whose highest cosine similarity to an
    # already-selected keyframe reaches this value. None disables the gate.
    semantic_similarity_threshold: float | None = None
    selector_version: str = "heuristic-v1"
    weights: SelectionWeights = SelectionWeights()

    def __post_init__(self) -> None:
        if self.max_keyframes <= 0 or self.max_per_shot <= 0:
            raise ValueError("keyframe limits must be positive")
        if self.minimum_time_distance_ms < 0 or self.perceptual_hash_distance < 0:
            raise ValueError("dedupe thresholds must not be negative")
        if self.semantic_similarity_threshold is not None and (
            not math.isfinite(self.semantic_similarity_threshold)
            or not -1 <= self.semantic_similarity_threshold <= 1
        ):
            raise ValueError("semantic_similarity_threshold must be finite and between -1 and 1")
        if not self.selector_version.strip():
            raise ValueError("selector_version must not be empty")

    @property
    def semantic_enabled(self) -> bool:
        """True when embeddings influence ranking or rejection."""

        return self.weights.semantic_novelty > 0 or self.semantic_similarity_threshold is not None


class FrameRejectionReason(StrEnum):
    EXACT_DUPLICATE = "exactDuplicate"
    PERCEPTUAL_DUPLICATE = "perceptualDuplicate"
    SEMANTIC_DUPLICATE = "semanticDuplicate"
    TEMPORAL_NEAR_DUPLICATE = "temporalNearDuplicate"
    SHOT_LIMIT = "shotLimit"
    GLOBAL_LIMIT = "globalLimit"


@dataclass(frozen=True, slots=True)
class FrameRejection:
    frame_id: str
    reason: FrameRejectionReason
    duplicate_of: str | None = None


@dataclass(frozen=True, slots=True)
class KeyframeSelection:
    selected: tuple[Keyframe, ...]
    rejected: tuple[FrameRejection, ...]
    selector_version: str
    input_digest: str


@runtime_checkable
class ShotDetector(Protocol):
    """Seam for PySceneDetect, FFmpeg or a deterministic fixture detector."""

    @property
    def version(self) -> str: ...

    def detect(self, media_path: Path) -> tuple[ShotBoundary, ...]: ...


@runtime_checkable
class FrameDescriptorProvider(Protocol):
    """Extract representative frames and their cheap scalar features locally."""

    @property
    def version(self) -> str: ...

    def describe(
        self,
        media_path: Path,
        shots: tuple[ShotBoundary, ...],
    ) -> tuple[FrameDescriptor, ...]: ...


def information_score(
    frame: FrameDescriptor,
    weights: SelectionWeights | None = None,
    *,
    semantic_novelty: float = 1.0,
) -> float:
    """Explicit weighted mean of the scalar features.

    ``semantic_novelty`` is supplied by the selector because it depends on which
    keyframes were already chosen; it defaults to fully novel so callers scoring a
    frame in isolation get the pre-embedding behaviour.
    """

    selected_weights = weights or SelectionWeights()
    _bounded_score(semantic_novelty, "semantic_novelty")
    weighted = (
        selected_weights.sharpness * frame.sharpness
        + selected_weights.exposure_quality * frame.exposure_quality
        + selected_weights.novelty * frame.novelty
        + selected_weights.ocr_density * frame.ocr_density
        + selected_weights.motion_stability * frame.motion_stability
        + selected_weights.semantic_novelty * semantic_novelty
    )
    return weighted / selected_weights.total


def _quality_score(frame: FrameDescriptor) -> float:
    return (frame.sharpness + frame.exposure_quality + frame.motion_stability) / 3


def perceptual_hash_distance(left: str, right: str) -> int | None:
    if len(left) != len(right):
        return None
    return (int(left, 16) ^ int(right, 16)).bit_count()


def _novelty_from_similarity(embedding_similarity_max: float | None) -> float:
    """Map the highest cosine similarity onto a novelty score in ``[0, 1]``.

    - No comparison possible (``None``): the candidate is maximally novel (1.0).
    - Similarity in ``[0, 1]``: novelty is ``1 - similarity``.
    - Negative similarity: the embeddings point away from each other, which is at
      least as novel as orthogonal, so novelty saturates at 1.0 rather than
      exceeding it. Callers wanting the signed value should read the raw maximum.
    """

    if embedding_similarity_max is None:
        return 1.0
    return 1.0 - max(0.0, embedding_similarity_max)


def semantic_novelty(
    candidate_embedding: Sequence[float],
    selected_embeddings: Sequence[Sequence[float]],
) -> tuple[float | None, float]:
    """Return ``(embedding_similarity_max, novelty)`` for a candidate embedding.

    ``embedding_similarity_max`` is the highest cosine similarity between the
    candidate and any selected embedding (``None`` when nothing was selected yet) and
    ``novelty`` follows ``_novelty_from_similarity``.
    """

    if not selected_embeddings:
        return None, _novelty_from_similarity(None)
    highest = max(cosine_similarity(candidate_embedding, item) for item in selected_embeddings)
    return highest, _novelty_from_similarity(highest)


def _validate_frame_embeddings(
    frames: tuple[FrameDescriptor, ...],
    config: KeyframeSelectionConfig,
) -> None:
    dimensions = {len(frame.embedding) for frame in frames if frame.embedding is not None}
    if len(dimensions) > 1:
        raise ValueError("frame embeddings must share one dimension")
    if config.semantic_enabled and any(frame.embedding is None for frame in frames):
        # A frame without an embedding would otherwise receive full novelty credit,
        # which is a bonus rather than a neutral value. Fail closed instead.
        raise ValueError("every frame needs an embedding when semantic novelty is enabled")


def _selection_input_digest(
    frames: tuple[FrameDescriptor, ...],
    config: KeyframeSelectionConfig,
) -> str:
    payload = {
        "config": config,
        "frames": sorted(frames, key=lambda frame: frame.frame_id),
    }
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def _keyframe_cache_key(
    frame: FrameDescriptor,
    config: KeyframeSelectionConfig,
    input_digest: str,
) -> str:
    payload = {
        "frameContentSha256": frame.content_sha256,
        "inputDigest": input_digest,
        "selectorVersion": config.selector_version,
        "sourceContentSha256": frame.source_content_sha256,
        "timeMs": frame.time_ms,
    }
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class _SemanticState:
    """Highest cosine similarity seen so far against accepted keyframes."""

    similarity_max: float | None = None
    most_similar_frame_id: str | None = None


def select_keyframes(
    frames: tuple[FrameDescriptor, ...],
    *,
    config: KeyframeSelectionConfig | None = None,
) -> KeyframeSelection:
    """Rank and deduplicate descriptors without loading pixels or model weights.

    Frames are picked greedily. Each step re-scores the remaining candidates against
    the keyframes accepted so far, which is what lets the semantic novelty term see
    the current selection; with the semantic weight at zero the ranking key equals
    the static information score, so the pick order matches a one-off sort.
    """

    selected_config = config or KeyframeSelectionConfig()
    frame_ids = [frame.frame_id for frame in frames]
    if len(frame_ids) != len(set(frame_ids)):
        raise ValueError("frame IDs must be unique")
    _validate_frame_embeddings(frames, selected_config)
    input_digest = _selection_input_digest(frames, selected_config)
    weights = selected_config.weights
    threshold = selected_config.semantic_similarity_threshold

    semantic: dict[str, _SemanticState] = {frame.frame_id: _SemanticState() for frame in frames}

    def step_score(frame: FrameDescriptor) -> float:
        novelty = _novelty_from_similarity(semantic[frame.frame_id].similarity_max)
        return information_score(frame, weights, semantic_novelty=novelty)

    remaining = list(frames)
    accepted: list[FrameDescriptor] = []
    selected: list[Keyframe] = []
    rejected: list[FrameRejection] = []
    shot_counts: dict[int, int] = {}

    while remaining:
        frame = min(
            remaining,
            key=lambda item: (-step_score(item), item.time_ms, item.frame_id),
        )
        remaining.remove(frame)
        if len(accepted) >= selected_config.max_keyframes:
            rejected.append(FrameRejection(frame.frame_id, FrameRejectionReason.GLOBAL_LIMIT))
            continue
        exact = next(
            (item for item in accepted if item.content_sha256 == frame.content_sha256),
            None,
        )
        if exact is not None:
            rejected.append(
                FrameRejection(
                    frame.frame_id,
                    FrameRejectionReason.EXACT_DUPLICATE,
                    exact.frame_id,
                )
            )
            continue
        perceptual = next(
            (
                item
                for item in accepted
                if item.perceptual_hash is not None
                and frame.perceptual_hash is not None
                and (
                    distance := perceptual_hash_distance(
                        item.perceptual_hash,
                        frame.perceptual_hash,
                    )
                )
                is not None
                and distance <= selected_config.perceptual_hash_distance
            ),
            None,
        )
        if perceptual is not None:
            rejected.append(
                FrameRejection(
                    frame.frame_id,
                    FrameRejectionReason.PERCEPTUAL_DUPLICATE,
                    perceptual.frame_id,
                )
            )
            continue
        state = semantic[frame.frame_id]
        if (
            threshold is not None
            and state.similarity_max is not None
            and state.similarity_max >= threshold
        ):
            rejected.append(
                FrameRejection(
                    frame.frame_id,
                    FrameRejectionReason.SEMANTIC_DUPLICATE,
                    state.most_similar_frame_id,
                )
            )
            continue
        temporal = next(
            (
                item
                for item in accepted
                if item.shot_index == frame.shot_index
                and abs(item.time_ms - frame.time_ms) < selected_config.minimum_time_distance_ms
            ),
            None,
        )
        if temporal is not None:
            rejected.append(
                FrameRejection(
                    frame.frame_id,
                    FrameRejectionReason.TEMPORAL_NEAR_DUPLICATE,
                    temporal.frame_id,
                )
            )
            continue
        if shot_counts.get(frame.shot_index, 0) >= selected_config.max_per_shot:
            rejected.append(FrameRejection(frame.frame_id, FrameRejectionReason.SHOT_LIMIT))
            continue

        selected.append(
            Keyframe(
                keyframe_id=f"keyframe-{frame.frame_id}",
                artifact=frame.artifact(),
                shot_index=frame.shot_index,
                rank=len(accepted),
                information_score=step_score(frame),
                quality_score=_quality_score(frame),
                selector_cache_key=_keyframe_cache_key(frame, selected_config, input_digest),
                embedding_similarity_max=state.similarity_max,
                semantic_novelty=(
                    None
                    if frame.embedding is None
                    else _novelty_from_similarity(state.similarity_max)
                ),
            )
        )
        accepted.append(frame)
        shot_counts[frame.shot_index] = shot_counts.get(frame.shot_index, 0) + 1
        if frame.embedding is None:
            continue
        # The maximum over a growing set only ever rises, so each remaining
        # candidate needs one cosine against the newly accepted keyframe.
        for other in remaining:
            if other.embedding is None:
                continue
            similarity = cosine_similarity(other.embedding, frame.embedding)
            current = semantic[other.frame_id]
            if current.similarity_max is None or similarity > current.similarity_max:
                semantic[other.frame_id] = _SemanticState(similarity, frame.frame_id)

    return KeyframeSelection(
        selected=tuple(selected),
        rejected=tuple(rejected),
        selector_version=selected_config.selector_version,
        input_digest=input_digest,
    )
