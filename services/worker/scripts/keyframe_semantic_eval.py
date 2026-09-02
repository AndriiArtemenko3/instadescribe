#!/usr/bin/env python3
"""Compare pHash-only and semantic keyframe selection on one local video.

The script embeds every candidate frame exactly once with the real CLIP vision
ONNX provider, then runs the open selector twice on the same descriptors: once
with embeddings stripped (baseline) and once with the configured semantic weight
and threshold. It reports what changed and a semantic-redundancy measure over each
selected set:

    R(K) = mean over x in K of max over y in K, y != x of cos(x, y)

Lower R(K) means the selected frames overlap less in embedding direction. It is an
inspection aid, not a quality claim. Run it locally (the model is not baked into
the worker image yet):

    python services/worker/scripts/keyframe_semantic_eval.py \\
        --media App/public/videos/sintel-blender-cc.mp4 \\
        --model /abs/path/to/clip-vit-base-patch32/onnx/vision_model.onnx
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import replace
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

from instadescribe_investigation_core import cosine_similarity, inspect_media  # noqa: E402


def semantic_redundancy(embeddings: Sequence[Sequence[float]]) -> float | None:
    """Mean nearest-neighbour cosine similarity inside one selected set.

    Returns None for fewer than two embeddings, where the measure is undefined.
    """

    if len(embeddings) < 2:
        return None
    nearest: list[float] = []
    for index, item in enumerate(embeddings):
        nearest.append(
            max(
                cosine_similarity(item, other)
                for other_index, other in enumerate(embeddings)
                if other_index != index
            )
        )
    return sum(nearest) / len(nearest)


def pairwise_similarity_summary(
    embeddings: Sequence[Sequence[float]],
) -> dict[str, float] | None:
    """Mean, median and maximum pairwise cosine similarity; None below two items."""

    if len(embeddings) < 2:
        return None
    values = [
        cosine_similarity(embeddings[left], embeddings[right])
        for left in range(len(embeddings))
        for right in range(left + 1, len(embeddings))
    ]
    return {
        "mean": sum(values) / len(values),
        "median": statistics.median(values),
        "max": max(values),
    }


def _settings(
    *,
    max_keyframes: int,
    semantic: bool,
    weight: float,
    threshold: float | None,
    model_path: Path,
) -> SimpleNamespace:
    return SimpleNamespace(
        investigation_max_keyframes=max_keyframes,
        investigation_image_long_edge=768,
        investigation_semantic_keyframes_enabled=semantic,
        investigation_semantic_novelty_weight=weight,
        investigation_semantic_similarity_threshold=threshold,
        investigation_frame_embedding_model_path=str(model_path),
    )


def _selection_report(ranked, embeddings_by_frame: dict[str, tuple[float, ...]]) -> dict:
    selected_ids = [item.descriptor.frame_id for item in ranked.selected]
    selected_embeddings = [embeddings_by_frame[frame_id] for frame_id in selected_ids]
    rejections: dict[str, int] = {}
    for item in ranked.rejected:
        rejections[item.reason.value] = rejections.get(item.reason.value, 0) + 1
    return {
        "selector": ranked.selector_version,
        "selectedFrames": selected_ids,
        "selectedCount": len(selected_ids),
        "rejections": rejections,
        "semanticRedundancy": semantic_redundancy(selected_embeddings),
        "pairwiseSimilarity": pairwise_similarity_summary(selected_embeddings),
        "keyframes": [
            {
                "frame": item.descriptor.frame_id,
                "timeMs": item.descriptor.time_ms,
                "informationScore": round(item.keyframe.information_score, 4),
                "embeddingSimilarityMax": (
                    None
                    if item.keyframe.embedding_similarity_max is None
                    else round(item.keyframe.embedding_similarity_max, 4)
                ),
                "semanticNovelty": (
                    None
                    if item.keyframe.semantic_novelty is None
                    else round(item.keyframe.semantic_novelty, 4)
                ),
            }
            for item in ranked.selected
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--media", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--max-keyframes", type=int, default=8)
    parser.add_argument("--weight", type=float, default=0.3)
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument("--duration-seconds", type=float, default=None)
    parser.add_argument("--json", action="store_true", help="emit the full report as JSON")
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
    semantic_settings = _settings(
        max_keyframes=args.max_keyframes,
        semantic=True,
        weight=args.weight,
        threshold=args.threshold,
        model_path=args.model,
    )
    baseline_settings = replace_settings(semantic_settings, semantic=False)

    with tempfile.TemporaryDirectory(prefix="keyframe-semantic-eval-") as workspace:
        started = time.perf_counter()
        candidates = extract_candidate_frames(
            media,
            Path(workspace),
            source_sha256=source_sha256,
            duration_seconds=duration,
            settings=semantic_settings,
            embedding_provider=provider,
        )
        extraction_seconds = time.perf_counter() - started
    embeddings_by_frame = {
        item.descriptor.frame_id: item.descriptor.embedding for item in candidates
    }
    baseline_candidates = tuple(
        replace(item, descriptor=replace(item.descriptor, embedding=None)) for item in candidates
    )
    baseline = rank_candidate_frames(baseline_candidates, settings=baseline_settings)
    semantic = rank_candidate_frames(
        candidates, settings=semantic_settings, embedding_provider=provider
    )
    report = {
        "media": media.name,
        "durationSeconds": duration,
        "candidateFrames": len(candidates),
        "embeddingInferenceCalls": len(candidates),
        "embeddingDimension": provider.dimension,
        "embeddingModel": {
            "name": provider.provenance.name,
            "runtime": provider.provenance.runtime,
            "version": provider.provenance.version,
            "digest": provider.provenance.digest,
        },
        "extractionSecondsIncludingEmbedding": round(extraction_seconds, 2),
        "config": {"noveltyWeight": args.weight, "similarityThreshold": args.threshold},
        "baseline": _selection_report(baseline, embeddings_by_frame),
        "semantic": _selection_report(semantic, embeddings_by_frame),
    }
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    print(f"media: {report['media']} ({duration:.1f}s)")
    print(f"candidate frames: {report['candidateFrames']}")
    print(f"embedding inference calls: {report['embeddingInferenceCalls']}")
    print(f"embedding dimension: {report['embeddingDimension']}")
    print(f"extraction incl. embedding: {report['extractionSecondsIncludingEmbedding']}s")
    for label in ("baseline", "semantic"):
        section = report[label]
        print(f"\n{label} ({section['selector']})")
        print(f"  selected: {section['selectedCount']} -> {section['selectedFrames']}")
        print(f"  rejections: {section['rejections']}")
        redundancy = section["semanticRedundancy"]
        print(
            "  semantic redundancy R(K): "
            + ("n/a (fewer than two frames)" if redundancy is None else f"{redundancy:.3f}")
        )
        pairwise = section["pairwiseSimilarity"]
        if pairwise is not None:
            print(
                "  pairwise cosine: "
                f"mean {pairwise['mean']:.3f} median {pairwise['median']:.3f} "
                f"max {pairwise['max']:.3f}"
            )
    return 0


def replace_settings(settings: SimpleNamespace, *, semantic: bool) -> SimpleNamespace:
    return SimpleNamespace(
        **{**vars(settings), "investigation_semantic_keyframes_enabled": semantic}
    )


if __name__ == "__main__":
    raise SystemExit(main())
