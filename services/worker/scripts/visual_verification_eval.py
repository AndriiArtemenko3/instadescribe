#!/usr/bin/env python3
"""Benchmark SIFT+RANSAC geometric verification of retrieved visual candidates.

The corpus extends the retrieval benchmark's assets (the rights-cleared Sintel
clip, CC BY 3.0, Blender Foundation) with transformations chosen to exercise
local-feature robustness rather than embedding similarity:

- positives per query: the frame 0.5 s later (same scene, different pixels) and
  five transformed copies of the query itself (crop, scale, rotation,
  brightness, perspective warp, partial occlusion);
- negatives: every other query's frames (semantically similar film scenes that
  share no geometry — the case retrieval ranks highly and RANSAC must reject)
  plus synthetic images.

Two modes:

  --model PATH   full pipeline: CLIP embedding -> exact retrieval -> Top-K ->
                 geometric verification. Retrieval and verification metrics are
                 reported separately.
  --no-embeddings
                 verification only, over the same pair set. No CLIP model is
                 needed and no network access is used; retrieval cosines are
                 reported as unavailable rather than invented.

    python services/worker/scripts/visual_verification_eval.py \\
        --media App/public/videos/sintel-blender-cc.mp4 \\
        --model /abs/path/to/clip-vit-base-patch32/onnx/vision_model.onnx
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
for root in (
    REPO / "services" / "worker",
    REPO / "packages" / "contracts",
    REPO / "packages" / "investigation-core" / "src",
):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from instadescribe_investigation_core import (  # noqa: E402
    InMemoryVisualCandidateRetriever,
    VerificationPrediction,
    VisualCandidate,
    verification_confusion,
    verification_f1,
    verification_precision,
    verification_recall,
)
from instadescribe_worker.visual_verification import (  # noqa: E402
    SiftRansacVisualMatcher,
    VerificationConfig,
)

QUERY_SECONDS = (8, 20, 32, 44, 56, 68, 80, 92)
NEIGHBOUR_OFFSET_SECONDS = 0.5
FRAME_LONG_EDGE = 768


@dataclass(frozen=True, slots=True)
class BenchmarkImage:
    image_id: str
    path: Path
    kind: str  # "query" | "positive" | "negative"


@dataclass(frozen=True, slots=True)
class BenchmarkQuery:
    query_id: str
    path: Path
    positives: frozenset[str]


def _transformed_positives(source: Path, directory: Path, stem: str) -> list[tuple[str, Path]]:
    """Crop, scale, rotation, brightness, perspective warp and occlusion copies."""

    from PIL import Image, ImageDraw, ImageEnhance

    outputs: list[tuple[str, Path]] = []
    with Image.open(source) as opened:
        image = opened.convert("RGB")
    width, height = image.size

    crop = image.crop(
        (int(width * 0.12), int(height * 0.12), int(width * 0.88), int(height * 0.88))
    )
    crop_path = directory / f"{stem}-crop.jpg"
    crop.save(crop_path, quality=92)
    outputs.append((f"{stem}:crop", crop_path))

    scaled = image.resize((int(width * 0.6), int(height * 0.6)), Image.Resampling.LANCZOS)
    scale_path = directory / f"{stem}-scale.jpg"
    scaled.save(scale_path, quality=92)
    outputs.append((f"{stem}:scale", scale_path))

    rotated = image.rotate(12, resample=Image.Resampling.BICUBIC, expand=False)
    rotate_path = directory / f"{stem}-rotate.jpg"
    rotated.save(rotate_path, quality=92)
    outputs.append((f"{stem}:rotate", rotate_path))

    brighter = ImageEnhance.Brightness(image).enhance(1.35)
    bright_path = directory / f"{stem}-bright.jpg"
    brighter.save(bright_path, quality=92)
    outputs.append((f"{stem}:brightness", bright_path))

    # Moderate perspective warp: a fixed quad-to-quad map, expressed as the
    # PIL PERSPECTIVE coefficients (the inverse homography's eight entries).
    shift = width * 0.06
    warped = image.transform(
        (width, height),
        Image.Transform.QUAD,
        (shift, 0, 0, height - shift, width - shift, height, width, shift),
        resample=Image.Resampling.BICUBIC,
    )
    warp_path = directory / f"{stem}-warp.jpg"
    warped.save(warp_path, quality=92)
    outputs.append((f"{stem}:perspective", warp_path))

    occluded = image.copy()
    draw = ImageDraw.Draw(occluded)
    draw.rectangle((0, 0, int(width * 0.34), height), fill=(0, 0, 0))
    occlusion_path = directory / f"{stem}-occluded.jpg"
    occluded.save(occlusion_path, quality=92)
    outputs.append((f"{stem}:occlusion", occlusion_path))
    return outputs


def _synthetic_negatives(directory: Path) -> list[tuple[str, Path]]:
    import numpy as np
    from PIL import Image, ImageDraw

    images: list[tuple[str, Path]] = []
    flat = directory / "synthetic-flat.png"
    Image.new("RGB", (480, 270), (128, 128, 128)).save(flat)
    images.append(("synthetic:flat", flat))
    noise = np.random.default_rng(7).integers(0, 256, size=(270, 480, 3), dtype=np.uint8)
    noise_path = directory / "synthetic-noise.png"
    Image.fromarray(noise, "RGB").save(noise_path)
    images.append(("synthetic:noise", noise_path))
    grid = Image.new("RGB", (480, 270), (255, 255, 255))
    draw = ImageDraw.Draw(grid)
    for x in range(0, 480, 24):
        draw.line((x, 0, x, 270), fill=(0, 0, 0))
    for y in range(0, 270, 24):
        draw.line((0, y, 480, y), fill=(0, 0, 0))
    grid_path = directory / "synthetic-grid.png"
    grid.save(grid_path)
    images.append(("synthetic:repeated-grid", grid_path))
    return images


def build_corpus(media: Path, workspace: Path) -> tuple[list[BenchmarkQuery], list[BenchmarkImage]]:
    """Extract query frames and build their positive/negative counterparts."""

    from instadescribe_worker.investigation_runtime import _run_ffmpeg_frame

    queries: list[BenchmarkQuery] = []
    images: list[BenchmarkImage] = []
    for seconds in QUERY_SECONDS:
        stem = f"sintel@{seconds}s"
        query_path = workspace / f"query-{seconds}.jpg"
        _run_ffmpeg_frame(media, query_path, seconds=seconds, long_edge=FRAME_LONG_EDGE)
        positives: set[str] = set()

        neighbour_id = f"{stem}:neighbour"
        neighbour_path = workspace / f"neighbour-{seconds}.jpg"
        _run_ffmpeg_frame(
            media,
            neighbour_path,
            seconds=seconds + NEIGHBOUR_OFFSET_SECONDS,
            long_edge=FRAME_LONG_EDGE,
        )
        images.append(BenchmarkImage(neighbour_id, neighbour_path, "positive"))
        positives.add(neighbour_id)

        for image_id, path in _transformed_positives(query_path, workspace, stem):
            images.append(BenchmarkImage(image_id, path, "positive"))
            positives.add(image_id)

        queries.append(BenchmarkQuery(stem, query_path, frozenset(positives)))

    for image_id, path in _synthetic_negatives(workspace):
        images.append(BenchmarkImage(image_id, path, "negative"))
    return queries, images


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def _distribution(values: Sequence[float]) -> dict:
    if not values:
        return {"count": 0, "median": None, "p10": None, "p90": None}
    return {
        "count": len(values),
        "median": statistics.median(values),
        "p10": _percentile(values, 0.10),
        "p90": _percentile(values, 0.90),
    }


def evaluate(
    queries: Sequence[BenchmarkQuery],
    images: Sequence[BenchmarkImage],
    matcher: SiftRansacVisualMatcher,
    *,
    similarities: dict[tuple[str, str], float] | None,
    ranked: dict[str, list[str]] | None,
    k: int,
) -> dict:
    """Verify each query against its candidate pairs and aggregate the outcome.

    With ``ranked`` supplied the pairs are the retrieval Top-K (the realistic
    pipeline); otherwise every query is paired with every non-own image so
    verification is measured on its own.
    """

    by_id = {image.image_id: image for image in images}
    predictions: list[VerificationPrediction] = []
    rows: list[dict] = []
    stats = {
        "positive": {"matches": [], "inliers": [], "ratio": [], "cosine": []},
        "negative": {"matches": [], "inliers": [], "ratio": [], "cosine": []},
    }
    timings = {"feature": [], "matching": [], "ransac": [], "total": []}

    for query in queries:
        candidate_ids = (
            ranked[query.query_id][:k]
            if ranked is not None
            else [image.image_id for image in images]
        )
        query_rows = []
        for candidate_id in candidate_ids:
            candidate = by_id[candidate_id]
            cosine = None if similarities is None else similarities[(query.query_id, candidate_id)]
            match, diagnostics = matcher.verify_detailed(
                query.path,
                candidate.path,
                # Verification never consumes this value; 0.0 is a neutral
                # placeholder when no embedding model was run, and the report
                # states the cosine is unavailable rather than inventing one.
                embedding_similarity=0.0 if cosine is None else cosine,
                query_artifact_id=query.query_id,
                candidate_artifact_id=candidate_id,
            )
            relevant = candidate_id in query.positives
            predictions.append(VerificationPrediction(verified=match.verified, relevant=relevant))
            bucket = stats["positive" if relevant else "negative"]
            bucket["matches"].append(float(match.feature_matches))
            bucket["inliers"].append(float(match.ransac_inliers))
            if match.ransac_inlier_ratio is not None:
                bucket["ratio"].append(match.ransac_inlier_ratio)
            if cosine is not None:
                bucket["cosine"].append(cosine)
            timings["feature"].append(diagnostics.feature_seconds)
            timings["matching"].append(diagnostics.matching_seconds)
            timings["ransac"].append(diagnostics.ransac_seconds)
            timings["total"].append(diagnostics.total_seconds)
            query_rows.append(
                {
                    "candidate": candidate_id,
                    "relevant": relevant,
                    "cosine": cosine,
                    "goodMatches": match.feature_matches,
                    "ransacInliers": match.ransac_inliers,
                    "inlierRatio": match.ransac_inlier_ratio,
                    "reprojectionError": match.reprojection_error,
                    "verified": match.verified,
                    "rejectionReason": match.rejection_reason,
                }
            )
        rows.append({"query": query.query_id, "candidates": query_rows})

    counts = verification_confusion(predictions)
    return {
        "pairs": len(predictions),
        "positivePairs": counts["tp"] + counts["fn"],
        "negativePairs": counts["fp"] + counts["tn"],
        "confusion": counts,
        "metrics": {
            "precision": verification_precision(predictions),
            "recall": verification_recall(predictions),
            "f1": verification_f1(predictions),
        },
        "separation": {
            side: {
                "goodMatches": _distribution(values["matches"]),
                "ransacInliers": _distribution(values["inliers"]),
                "inlierRatio": _distribution(values["ratio"]),
                "embeddingSimilarity": _distribution(values["cosine"]),
            }
            for side, values in stats.items()
        },
        "verificationTimingMilliseconds": {
            stage: (1000 * statistics.mean(values) if values else None)
            for stage, values in timings.items()
        },
        "queries": rows,
    }


def sensitivity(
    queries: Sequence[BenchmarkQuery],
    images: Sequence[BenchmarkImage],
    *,
    inlier_minimums: Sequence[int],
    ranked: dict[str, list[str]] | None,
    k: int,
) -> list[dict]:
    """Re-score the same pairs under a few minimum-inlier settings."""

    report = []
    for minimum in inlier_minimums:
        config = VerificationConfig(minimum_ransac_inliers=minimum)
        result = evaluate(
            queries,
            images,
            SiftRansacVisualMatcher(config),
            similarities=None,
            ranked=ranked,
            k=k,
        )
        report.append(
            {
                "minimumRansacInliers": minimum,
                "confusion": result["confusion"],
                "precision": result["metrics"]["precision"],
                "recall": result["metrics"]["recall"],
                "f1": result["metrics"]["f1"],
            }
        )
    return report


def _retrieval_stage(
    queries: Sequence[BenchmarkQuery], images: Sequence[BenchmarkImage], model: Path, k: int
) -> tuple[dict[str, list[str]], dict[tuple[str, str], float], dict]:
    """Embed every image once, then rank each query's candidates by cosine."""

    from instadescribe_worker.frame_embeddings import OnnxClipFrameEmbeddingProvider

    provider = OnnxClipFrameEmbeddingProvider(model.expanduser().absolute())
    started = time.perf_counter()
    embeddings = {image.image_id: provider.embed_frame(image.path) for image in images}
    query_embeddings = {query.query_id: provider.embed_frame(query.path) for query in queries}
    embedding_seconds = time.perf_counter() - started

    retriever = InMemoryVisualCandidateRetriever(
        [
            VisualCandidate(image.image_id, embeddings[image.image_id], image_ref=str(image.path))
            for image in images
        ]
    )
    ranked: dict[str, list[str]] = {}
    similarities: dict[tuple[str, str], float] = {}
    started = time.perf_counter()
    for query in queries:
        results = retriever.retrieve(query_embeddings[query.query_id], limit=len(retriever))
        ranked[query.query_id] = [item.candidate_id for item in results]
        for item in results:
            similarities[(query.query_id, item.candidate_id)] = item.embedding_similarity
    retrieval_seconds = time.perf_counter() - started

    provenance = provider.provenance
    timing = {
        "imagesEmbedded": len(images) + len(queries),
        "embeddingMillisecondsPerImage": 1000 * embedding_seconds / (len(images) + len(queries)),
        "retrievalMillisecondsPerQuery": 1000 * retrieval_seconds / len(queries),
        "topKRetrievalRecall": statistics.mean(
            len(set(ranked[query.query_id][:k]) & query.positives) / len(query.positives)
            for query in queries
        ),
        "embeddingModel": {
            "name": provenance.name,
            "runtime": provenance.runtime,
            "version": provenance.version,
            "digest": provenance.digest,
        },
    }
    return ranked, similarities, timing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--media", type=Path, required=True)
    parser.add_argument("--model", type=Path, help="CLIP vision ONNX export for retrieval")
    parser.add_argument(
        "--no-embeddings",
        action="store_true",
        help="verification only; no CLIP model, no retrieval ranking",
    )
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if not args.model and not args.no_embeddings:
        parser.error(
            "supply --model for the full pipeline or --no-embeddings for verification only"
        )

    with tempfile.TemporaryDirectory(prefix="visual-verification-eval-") as workspace:
        directory = Path(workspace)
        queries, images = build_corpus(args.media.resolve(), directory)
        ranked = similarities = None
        retrieval_timing = None
        if args.model:
            ranked, similarities, retrieval_timing = _retrieval_stage(
                queries, images, args.model, args.k
            )
        matcher = SiftRansacVisualMatcher()
        report = evaluate(
            queries, images, matcher, similarities=similarities, ranked=ranked, k=args.k
        )
        report["mode"] = "retrieval+verification" if args.model else "verification-only"
        report["retrieval"] = retrieval_timing
        report["sensitivity"] = sensitivity(
            queries, images, inlier_minimums=(6, 8, 12, 20), ranked=ranked, k=args.k
        )
        provenance = matcher.provenance
        report["matcher"] = {
            "name": provenance.name,
            "runtime": provenance.runtime,
            "version": provenance.version,
            "configDigest": provenance.digest,
            "geometricModel": "homography",
            "config": {
                "descriptorRatioThreshold": matcher.config.descriptor_ratio_threshold,
                "minimumFeatureMatches": matcher.config.minimum_feature_matches,
                "ransacReprojectionThreshold": matcher.config.ransac_reprojection_threshold,
                "minimumRansacInliers": matcher.config.minimum_ransac_inliers,
                "minimumRansacInlierRatio": matcher.config.minimum_ransac_inlier_ratio,
            },
        }

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(f"mode: {report['mode']}   pairs: {report['pairs']}")
    print(f"positive pairs: {report['positivePairs']}  negative pairs: {report['negativePairs']}")
    if retrieval_timing:
        print(
            f"embedding: {retrieval_timing['embeddingMillisecondsPerImage']:.1f} ms/image   "
            f"retrieval: {retrieval_timing['retrievalMillisecondsPerQuery']:.2f} ms/query   "
            f"top-{args.k} retrieval recall: {retrieval_timing['topKRetrievalRecall']:.3f}"
        )
    counts = report["confusion"]
    print(f"TP {counts['tp']}  FP {counts['fp']}  TN {counts['tn']}  FN {counts['fn']}")
    for name, value in report["metrics"].items():
        print(f"  {name}: {'n/a' if value is None else f'{value:.3f}'}")
    timing = report["verificationTimingMilliseconds"]
    print(
        "verification per pair: "
        + "  ".join(
            f"{stage} {value:.1f} ms" for stage, value in timing.items() if value is not None
        )
    )
    print("\nsignal separation (median [p10, p90]):")
    for side in ("positive", "negative"):
        block = report["separation"][side]
        print(f"  {side}:")
        for label, key in (
            ("good matches", "goodMatches"),
            ("RANSAC inliers", "ransacInliers"),
            ("inlier ratio", "inlierRatio"),
            ("embedding cosine", "embeddingSimilarity"),
        ):
            values = block[key]
            if values["count"] == 0:
                print(f"    {label}: n/a")
                continue
            print(f"    {label}: {values['median']:.3f} [{values['p10']:.3f}, {values['p90']:.3f}]")
    print("\nthreshold sensitivity (minimum RANSAC inliers):")
    for entry in report["sensitivity"]:
        precision = entry["precision"]
        recall = entry["recall"]
        f1 = entry["f1"]
        print(
            f"  >= {entry['minimumRansacInliers']:>3}: "
            f"P {'n/a' if precision is None else f'{precision:.3f}'}  "
            f"R {'n/a' if recall is None else f'{recall:.3f}'}  "
            f"F1 {'n/a' if f1 is None else f'{f1:.3f}'}  "
            f"{entry['confusion']}"
        )
    for entry in report["queries"][:2]:
        print(f"\nQUERY {entry['query']}")
        for row in entry["candidates"][: args.k]:
            cosine = "n/a" if row["cosine"] is None else f"{row['cosine']:.3f}"
            ratio = "n/a" if row["inlierRatio"] is None else f"{row['inlierRatio']:.3f}"
            print(
                f"  {row['candidate']}\n"
                f"    cosine: {cosine}   relevant: {'yes' if row['relevant'] else 'no'}\n"
                f"    good matches: {row['goodMatches']}   RANSAC inliers: {row['ransacInliers']}"
                f"   inlier ratio: {ratio}\n"
                f"    verified: {'yes' if row['verified'] else 'no'}"
                + ("" if row["verified"] else f"   reason: {row['rejectionReason']}")
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
