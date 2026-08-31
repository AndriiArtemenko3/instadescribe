"""Deterministic, dependency-light shot/keyframe selection primitives."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

from .models import ArtifactRef, Keyframe
from .serialization import canonical_json


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

    def __post_init__(self) -> None:
        for field_name in (
            "sharpness",
            "exposure_quality",
            "novelty",
            "ocr_density",
            "motion_stability",
        ):
            value = getattr(self, field_name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{field_name} weight must be finite and non-negative")
        if self.total <= 0:
            raise ValueError("at least one selection weight must be positive")

    @property
    def total(self) -> float:
        return (
            self.sharpness
            + self.exposure_quality
            + self.novelty
            + self.ocr_density
            + self.motion_stability
        )


@dataclass(frozen=True, slots=True)
class KeyframeSelectionConfig:
    max_keyframes: int = 24
    max_per_shot: int = 3
    minimum_time_distance_ms: int = 500
    perceptual_hash_distance: int = 6
    selector_version: str = "heuristic-v1"
    weights: SelectionWeights = SelectionWeights()

    def __post_init__(self) -> None:
        if self.max_keyframes <= 0 or self.max_per_shot <= 0:
            raise ValueError("keyframe limits must be positive")
        if self.minimum_time_distance_ms < 0 or self.perceptual_hash_distance < 0:
            raise ValueError("dedupe thresholds must not be negative")
        if not self.selector_version.strip():
            raise ValueError("selector_version must not be empty")


class FrameRejectionReason(StrEnum):
    EXACT_DUPLICATE = "exactDuplicate"
    PERCEPTUAL_DUPLICATE = "perceptualDuplicate"
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
) -> float:
    selected_weights = weights or SelectionWeights()
    weighted = (
        selected_weights.sharpness * frame.sharpness
        + selected_weights.exposure_quality * frame.exposure_quality
        + selected_weights.novelty * frame.novelty
        + selected_weights.ocr_density * frame.ocr_density
        + selected_weights.motion_stability * frame.motion_stability
    )
    return weighted / selected_weights.total


def _quality_score(frame: FrameDescriptor) -> float:
    return (frame.sharpness + frame.exposure_quality + frame.motion_stability) / 3


def perceptual_hash_distance(left: str, right: str) -> int | None:
    if len(left) != len(right):
        return None
    return (int(left, 16) ^ int(right, 16)).bit_count()


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


def select_keyframes(
    frames: tuple[FrameDescriptor, ...],
    *,
    config: KeyframeSelectionConfig | None = None,
) -> KeyframeSelection:
    """Rank and deduplicate descriptors without loading pixels or model weights."""

    selected_config = config or KeyframeSelectionConfig()
    frame_ids = [frame.frame_id for frame in frames]
    if len(frame_ids) != len(set(frame_ids)):
        raise ValueError("frame IDs must be unique")
    input_digest = _selection_input_digest(frames, selected_config)
    ranked = sorted(
        frames,
        key=lambda frame: (
            -information_score(frame, selected_config.weights),
            frame.time_ms,
            frame.frame_id,
        ),
    )
    accepted: list[FrameDescriptor] = []
    rejected: list[FrameRejection] = []
    shot_counts: dict[int, int] = {}

    for frame in ranked:
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
        accepted.append(frame)
        shot_counts[frame.shot_index] = shot_counts.get(frame.shot_index, 0) + 1

    selected = tuple(
        Keyframe(
            keyframe_id=f"keyframe-{frame.frame_id}",
            artifact=frame.artifact(),
            shot_index=frame.shot_index,
            rank=rank,
            information_score=information_score(frame, selected_config.weights),
            quality_score=_quality_score(frame),
            selector_cache_key=_keyframe_cache_key(frame, selected_config, input_digest),
        )
        for rank, frame in enumerate(accepted)
    )
    return KeyframeSelection(
        selected=selected,
        rejected=tuple(rejected),
        selector_version=selected_config.selector_version,
        input_digest=input_digest,
    )
