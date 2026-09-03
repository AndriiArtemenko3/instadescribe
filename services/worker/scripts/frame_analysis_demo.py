#!/usr/bin/env python3
"""Export a per-frame analysis manifest for one local video.

Every number in the manifest comes from the real pipeline: frames are extracted
and described by the worker's frame extractor, embedded once each by the CLIP
vision ONNX provider, and ranked by the open keyframe selector. Nothing is
sampled, simulated or re-implemented here.

The manifest exists so a frame-by-frame view can be inspected without a
database, a queue or a second analysis run. It carries scalars only: raw
embedding vectors are never written out, because the file is meant to be read
by a person and a small viewer, not to become an undeclared vector store.

The vector metrics are cosine similarities in [-1, 1]. A cosine measures how
closely two embeddings point the same way. It is not a confidence, a
probability, an accuracy or a quality judgement, and nothing downstream should
present it as one.

Run it locally (the model is not baked into the worker image):

    python services/worker/scripts/frame_analysis_demo.py \\
        --media App/public/videos/sintel-blender-cc.mp4 \\
        --model /abs/path/to/clip-vit-base-patch32/onnx/vision_model.onnx \\
        --output build/frame-analysis/manifest.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[3]
for root in (
    REPO / "services" / "worker",
    REPO / "packages" / "contracts",
    REPO / "packages" / "investigation-core" / "src",
):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from instadescribe_investigation_core import (  # noqa: E402
    cosine_similarity,
    inspect_media,
    l2_norm,
    semantic_novelty,
)

SCHEMA_VERSION = "frame-analysis-demo-1"

# Stated once, in the manifest, so a viewer cannot invent its own reading of
# these numbers. Wording is deliberate: similarity, never confidence.
METRIC_DEFINITIONS = {
    "clipCentroidSimilarity": (
        "Cosine similarity between this frame's embedding and the unit-length "
        "mean of all frame embeddings in this video. High means the frame looks "
        "like the video's average content; low means it stands apart. It is a "
        "direction comparison, not a confidence or quality score."
    ),
    "previousFrameSimilarity": (
        "Cosine similarity between this frame's embedding and the previous "
        "frame's, in time order. Null for the first frame, which has no "
        "predecessor."
    ),
    "nearestSelectedSimilarity": (
        "Highest cosine similarity between this frame and any keyframe in the "
        "final selected set, excluding the frame itself. Null when there is "
        "nothing to compare against."
    ),
    "semanticNovelty": (
        "The selector's novelty term derived from nearestSelectedSimilarity: 1 "
        "minus that similarity, clamped to [0, 1]. Higher means the frame adds "
        "content the selected set does not already cover."
    ),
    "mostSimilarFrameId": (
        "The keyframe that nearestSelectedSimilarity was measured against. Null "
        "when no comparison was possible."
    ),
    "informationScore": (
        "The selector's weighted combination of the frame quality features and "
        "semantic novelty, as computed at the moment the frame was selected. "
        "Null for frames the selector did not select."
    ),
    "selectionNearestSimilarity": (
        "The selector's own nearest-similarity reading, measured against the "
        "keyframes accepted BEFORE this one rather than the final set. It is "
        "what informationScore was actually built from, so it can differ from "
        "nearestSelectedSimilarity; the first keyframe accepted has none."
    ),
    "selectionSemanticNovelty": (
        "The novelty term the selector used for this frame at selection time, "
        "derived from selectionNearestSimilarity."
    ),
}


def unit_vector(embedding: Sequence[float]) -> tuple[float, ...]:
    """Scale an embedding to length one, keeping its direction."""

    norm = l2_norm(embedding)
    if norm == 0:
        raise ValueError("a zero-norm embedding has no direction to normalize")
    return tuple(value / norm for value in embedding)


def embedding_centroid(embeddings: Sequence[Sequence[float]]) -> tuple[float, ...]:
    """The unit-length mean direction of a set of embeddings.

    Each embedding is normalized before averaging so that a long vector cannot
    drag the centroid toward itself; the mean is then normalized again so the
    result is a direction that ``cosine_similarity`` can be measured against.

    The provider does not return unit-length embeddings, so this normalization
    is load-bearing rather than cosmetic. Dimension is taken from the data.
    """

    if not embeddings:
        raise ValueError("the centroid of an empty set is undefined")
    dimensions = {len(item) for item in embeddings}
    if len(dimensions) != 1:
        raise ValueError(f"embeddings must share one dimension, got {sorted(dimensions)}")
    normalized = [unit_vector(item) for item in embeddings]
    width = dimensions.pop()
    mean = tuple(sum(item[axis] for item in normalized) / len(normalized) for axis in range(width))
    return unit_vector(mean)


def _round(value: float | None, digits: int = 6) -> float | None:
    return None if value is None else round(value, digits)


def _settings(
    *,
    max_keyframes: int,
    weight: float,
    threshold: float | None,
    model_path: Path,
) -> SimpleNamespace:
    return SimpleNamespace(
        investigation_max_keyframes=max_keyframes,
        investigation_image_long_edge=768,
        investigation_semantic_keyframes_enabled=True,
        investigation_semantic_novelty_weight=weight,
        investigation_semantic_similarity_threshold=threshold,
        investigation_frame_embedding_model_path=str(model_path),
    )


def build_manifest(
    candidates,
    ranked,
    *,
    media_name: str,
    duration_seconds: float,
    source_sha256: str,
    provider_provenance,
    selector_config: dict,
    image_paths: dict[str, str] | None = None,
) -> dict:
    """Assemble the manifest from real extraction and selection results.

    Kept separate from ``main`` so it can be exercised with deterministic
    descriptors, without a video file or model weights.
    """

    ordered = sorted(
        candidates, key=lambda item: (item.descriptor.time_ms, item.descriptor.frame_id)
    )
    embeddings = [item.descriptor.embedding for item in ordered]
    if any(embedding is None for embedding in embeddings):
        raise ValueError("every candidate frame must carry an embedding")

    centroid = embedding_centroid(embeddings)
    selected_by_id = {item.descriptor.frame_id: item for item in ranked.selected}
    rejection_by_id = {item.frame_id: item for item in ranked.rejected}
    # The selector's chosen set is the reference for novelty; measuring a frame
    # against itself would report a similarity of one for every keyframe.
    selected_embeddings = {
        item.descriptor.frame_id: item.descriptor.embedding for item in ranked.selected
    }

    frames = []
    for index, item in enumerate(ordered):
        descriptor = item.descriptor
        embedding = descriptor.embedding
        others = [
            value
            for frame_id, value in selected_embeddings.items()
            if frame_id != descriptor.frame_id
        ]
        # The core selector's own novelty function, not a second implementation.
        nearest, novelty = semantic_novelty(embedding, others)
        most_similar = None
        if nearest is not None:
            most_similar = max(
                (frame_id for frame_id in selected_embeddings if frame_id != descriptor.frame_id),
                key=lambda frame_id: cosine_similarity(embedding, selected_embeddings[frame_id]),
            )
        previous = ordered[index - 1].descriptor.embedding if index else None
        keyframe = selected_by_id.get(descriptor.frame_id)
        rejection = rejection_by_id.get(descriptor.frame_id)
        frames.append(
            {
                "frameId": descriptor.frame_id,
                "index": index,
                "timeMs": descriptor.time_ms,
                "shotIndex": descriptor.shot_index,
                "image": (image_paths or {}).get(descriptor.frame_id),
                "width": descriptor.width,
                "height": descriptor.height,
                "sizeBytes": descriptor.size_bytes,
                "mediaType": descriptor.media_type,
                "perceptualHash": descriptor.perceptual_hash,
                "vectorMetrics": {
                    "clipCentroidSimilarity": _round(cosine_similarity(embedding, centroid)),
                    "previousFrameSimilarity": (
                        None if previous is None else _round(cosine_similarity(embedding, previous))
                    ),
                    "nearestSelectedSimilarity": _round(nearest),
                    "semanticNovelty": _round(novelty),
                    "mostSimilarFrameId": most_similar,
                },
                "qualityMetrics": {
                    "sharpness": _round(descriptor.sharpness),
                    "exposureQuality": _round(descriptor.exposure_quality),
                    "novelty": _round(descriptor.novelty),
                    "ocrDensity": _round(descriptor.ocr_density),
                    "motionStability": _round(descriptor.motion_stability),
                },
                "keyframe": {
                    "selected": keyframe is not None,
                    "rank": None if keyframe is None else keyframe.keyframe.rank,
                    "informationScore": (
                        None if keyframe is None else _round(keyframe.keyframe.information_score)
                    ),
                    "qualityScore": (
                        None if keyframe is None else _round(keyframe.keyframe.quality_score)
                    ),
                    # As-of-selection readings, kept beside informationScore
                    # because that is the score they actually produced.
                    "selectionNearestSimilarity": (
                        None
                        if keyframe is None
                        else _round(keyframe.keyframe.embedding_similarity_max)
                    ),
                    "selectionSemanticNovelty": (
                        None if keyframe is None else _round(keyframe.keyframe.semantic_novelty)
                    ),
                    "rejectionReason": None if rejection is None else rejection.reason.value,
                    "duplicateOf": None if rejection is None else rejection.duplicate_of,
                },
            }
        )

    return {
        "schemaVersion": SCHEMA_VERSION,
        "media": {
            "name": media_name,
            "durationSeconds": round(duration_seconds, 3),
            "sourceSha256": source_sha256,
        },
        "embeddingModel": {
            "name": provider_provenance.name,
            "runtime": provider_provenance.runtime,
            "version": provider_provenance.version,
            "digest": provider_provenance.digest,
            # Read off the embeddings themselves; the width is never assumed.
            "dimension": len(embeddings[0]),
        },
        "selector": {
            **selector_config,
            "version": ranked.selector_version,
            "semanticEnabled": ranked.semantic_enabled,
            "candidateCount": len(ordered),
            "selectedCount": len(ranked.selected),
        },
        "metricDefinitions": METRIC_DEFINITIONS,
        "frames": frames,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--media", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="manifest JSON path")
    parser.add_argument(
        "--frames-dir",
        type=Path,
        default=None,
        help="copy the extracted frame images here and reference them from the manifest",
    )
    parser.add_argument("--max-keyframes", type=int, default=8)
    parser.add_argument("--weight", type=float, default=0.3)
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument("--duration-seconds", type=float, default=None)
    args = parser.parse_args()

    from instadescribe_worker.frame_embeddings import OnnxClipFrameEmbeddingProvider
    from instadescribe_worker.investigation_runtime import (
        extract_candidate_frames,
        rank_candidate_frames,
    )

    media = args.media.resolve()
    duration = args.duration_seconds
    if duration is None:
        duration_ms = inspect_media(media).duration_ms
        if not duration_ms:
            parser.error("could not probe the media duration; pass --duration-seconds")
        duration = duration_ms / 1000
    source_sha256 = hashlib.sha256(media.read_bytes()).hexdigest()
    provider = OnnxClipFrameEmbeddingProvider(args.model.expanduser().absolute())
    settings = _settings(
        max_keyframes=args.max_keyframes,
        weight=args.weight,
        threshold=args.threshold,
        model_path=args.model,
    )

    frames_dir = args.frames_dir
    image_paths: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="frame-analysis-demo-") as workspace:
        started = time.perf_counter()
        candidates = extract_candidate_frames(
            media,
            Path(workspace),
            source_sha256=source_sha256,
            duration_seconds=duration,
            settings=settings,
            embedding_provider=provider,
        )
        elapsed = time.perf_counter() - started
        # Copy before the workspace is removed; the manifest is useless to a
        # viewer if the images it names have already been deleted.
        if frames_dir is not None:
            frames_dir.mkdir(parents=True, exist_ok=True)
            for item in candidates:
                target = frames_dir / f"{item.descriptor.frame_id}{item.path.suffix}"
                shutil.copyfile(item.path, target)
                image_paths[item.descriptor.frame_id] = target.name

    ranked = rank_candidate_frames(candidates, settings=settings, embedding_provider=provider)
    manifest = build_manifest(
        candidates,
        ranked,
        media_name=media.name,
        duration_seconds=duration,
        source_sha256=source_sha256,
        provider_provenance=provider.provenance,
        selector_config={
            "maxKeyframes": args.max_keyframes,
            "noveltyWeight": args.weight,
            "similarityThreshold": args.threshold,
        },
        image_paths=image_paths or None,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"media: {manifest['media']['name']} ({duration:.1f}s)")
    print(f"candidate frames: {manifest['selector']['candidateCount']}")
    print(f"embedding inference calls: {len(candidates)}")
    print(f"embedding dimension: {manifest['embeddingModel']['dimension']}")
    print(f"selected keyframes: {manifest['selector']['selectedCount']}")
    print(f"extraction incl. embedding: {elapsed:.2f}s")
    print(f"manifest: {args.output}")
    if frames_dir is not None:
        print(f"frame images: {frames_dir} ({len(image_paths)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
