#!/usr/bin/env python3
"""Benchmark exact visual candidate retrieval driven by real CLIP embeddings.

The corpus is built at run time from the rights-cleared Sintel clip already in
the repository (CC BY 3.0, Blender Foundation), so nothing new needs licensing:

- queries: frames at fixed timestamps spread across the film's scenes;
- relevant candidates per query: the frame 0.5 s later (same scene, different
  pixels) and a flipped, cropped, slightly darkened copy of the query;
- distractors: every other query's relevant frames plus synthetic images (flat
  colours, a gradient, seeded noise, drawn shapes).

Every image is embedded exactly once. Retrieval is the Apache core's exact
cosine search, so the per-query cost is O(N * D). Embedding inference time and
retrieval time are reported separately; an optional synthetic corpus of random
unit vectors characterises exact-search latency at a larger N without pretending
those vectors are images.

    python services/worker/scripts/visual_retrieval_eval.py \\
        --media App/public/videos/sintel-blender-cc.mp4 \\
        --model /abs/path/to/clip-vit-base-patch32/onnx/vision_model.onnx
"""

from __future__ import annotations

import argparse
import json
import random
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
    RetrievalPrediction,
    VisualCandidate,
    VisualCandidateRetriever,
    mean_reciprocal_rank,
    ndcg_at_k,
    retrieval_hit_rate_at_k,
    retrieval_recall_at_k,
)

QUERY_SECONDS = (8, 20, 32, 44, 56, 68, 80, 92)
NEIGHBOUR_OFFSET_SECONDS = 0.5
FRAME_LONG_EDGE = 768


@dataclass(frozen=True, slots=True)
class RetrievalQuery:
    """One benchmark query: an embedding plus the ids that count as relevant."""

    query_id: str
    embedding: tuple[float, ...]
    relevant_ids: frozenset[str]


def evaluate_retrieval(
    queries: Sequence[RetrievalQuery],
    retriever: VisualCandidateRetriever,
    *,
    k: int,
) -> dict:
    """Rank every query, then report metrics and inspectable per-query rankings.

    Pure over vectors: no inference happens here, so the reported retrieval
    latency is the exact cosine search alone.
    """

    if not queries:
        raise ValueError("at least one query is required")
    rankings = []
    predictions = []
    positives: list[float] = []
    distractors: list[float] = []
    started = time.perf_counter()
    for query in queries:
        results = retriever.retrieve(query.embedding, limit=len(retriever))
        rankings.append((query, results))
    retrieval_seconds = time.perf_counter() - started
    for query, results in rankings:
        predictions.append(
            RetrievalPrediction(
                tuple(item.candidate_id for item in results),
                query.relevant_ids,
            )
        )
        for item in results:
            (positives if item.candidate_id in query.relevant_ids else distractors).append(
                item.embedding_similarity
            )
    mean_positive = sum(positives) / len(positives) if positives else None
    mean_distractor = sum(distractors) / len(distractors) if distractors else None
    return {
        "queries": len(queries),
        "candidates": len(retriever),
        "dimension": retriever.dimension,
        "k": k,
        "metrics": {
            "top1": retrieval_hit_rate_at_k(predictions, k=1),
            f"hitRate@{k}": retrieval_hit_rate_at_k(predictions, k=k),
            f"recall@{k}": retrieval_recall_at_k(predictions, k=k),
            "mrr": mean_reciprocal_rank(predictions),
            f"ndcg@{k}": ndcg_at_k(predictions, k=k),
            "meanPositiveSimilarity": mean_positive,
            "meanDistractorSimilarity": mean_distractor,
            "positiveDistractorMargin": (
                None
                if mean_positive is None or mean_distractor is None
                else mean_positive - mean_distractor
            ),
        },
        "retrievalSecondsTotal": retrieval_seconds,
        "retrievalMillisecondsPerQuery": 1000 * retrieval_seconds / len(queries),
        "rankings": [
            {
                "query": query.query_id,
                "results": [
                    {
                        "rank": item.rank,
                        "candidate": item.candidate_id,
                        "cosine": round(item.embedding_similarity, 4),
                        "relevant": item.candidate_id in query.relevant_ids,
                    }
                    for item in results[:k]
                ],
            }
            for query, results in rankings
        ],
    }


def _synthetic_images(directory: Path) -> list[tuple[str, Path]]:
    import numpy as np
    from PIL import Image, ImageDraw

    images: list[tuple[str, Path]] = []
    for name, colour in (("flat-blue", (30, 90, 200)), ("flat-grey", (128, 128, 128))):
        path = directory / f"{name}.png"
        Image.new("RGB", (320, 180), colour).save(path)
        images.append((f"synthetic:{name}", path))
    gradient = np.linspace(0, 255, 320, dtype=np.uint8)
    gradient_pixels = np.stack([np.tile(gradient, (180, 1))] * 3, axis=-1)
    gradient_path = directory / "gradient.png"
    Image.fromarray(gradient_pixels, "RGB").save(gradient_path)
    images.append(("synthetic:gradient", gradient_path))
    noise = np.random.default_rng(7).integers(0, 256, size=(180, 320, 3), dtype=np.uint8)
    noise_path = directory / "noise.png"
    Image.fromarray(noise, "RGB").save(noise_path)
    images.append(("synthetic:noise", noise_path))
    shapes = Image.new("RGB", (320, 180), (255, 255, 255))
    draw = ImageDraw.Draw(shapes)
    draw.ellipse((40, 30, 140, 130), fill=(200, 30, 30))
    draw.rectangle((190, 50, 290, 150), fill=(30, 30, 200))
    shapes_path = directory / "shapes.png"
    shapes.save(shapes_path)
    images.append(("synthetic:shapes", shapes_path))
    grid = Image.new("RGB", (320, 180), (255, 255, 255))
    draw = ImageDraw.Draw(grid)
    for x in range(0, 320, 20):
        draw.line((x, 0, x, 180), fill=(0, 0, 0))
    for y in range(0, 180, 20):
        draw.line((0, y, 320, y), fill=(0, 0, 0))
    grid_path = directory / "grid.png"
    grid.save(grid_path)
    images.append(("synthetic:grid", grid_path))
    return images


def _augmented_copy(source: Path, destination: Path) -> None:
    from PIL import Image, ImageEnhance, ImageOps

    with Image.open(source) as image:
        flipped = ImageOps.mirror(image.convert("RGB"))
        width, height = flipped.size
        margin_x, margin_y = int(width * 0.075), int(height * 0.075)
        cropped = flipped.crop((margin_x, margin_y, width - margin_x, height - margin_y))
        ImageEnhance.Brightness(cropped).enhance(0.9).save(destination, format="JPEG", quality=90)


def build_corpus(
    media: Path, workspace: Path, provider
) -> tuple[list[RetrievalQuery], list[VisualCandidate], dict]:
    """Extract, augment and embed the benchmark images exactly once each."""

    from instadescribe_worker.investigation_runtime import _run_ffmpeg_frame

    images: list[tuple[str, str, Path]] = []  # (id, source, path)
    query_relevance: dict[str, set[str]] = {}
    for seconds in QUERY_SECONDS:
        query_id = f"sintel@{seconds}s"
        query_path = workspace / f"query-{seconds}.jpg"
        _run_ffmpeg_frame(media, query_path, seconds=seconds, long_edge=FRAME_LONG_EDGE)
        images.append((query_id, f"{media.name}@{seconds}s", query_path))
        neighbour_id = f"sintel@{seconds + NEIGHBOUR_OFFSET_SECONDS}s"
        neighbour_path = workspace / f"neighbour-{seconds}.jpg"
        _run_ffmpeg_frame(
            media,
            neighbour_path,
            seconds=seconds + NEIGHBOUR_OFFSET_SECONDS,
            long_edge=FRAME_LONG_EDGE,
        )
        images.append(
            (neighbour_id, f"{media.name}@{seconds + NEIGHBOUR_OFFSET_SECONDS}s", neighbour_path)
        )
        augmented_id = f"sintel@{seconds}s:flipped-cropped"
        augmented_path = workspace / f"augmented-{seconds}.jpg"
        _augmented_copy(query_path, augmented_path)
        images.append((augmented_id, f"{media.name}@{seconds}s augmented", augmented_path))
        query_relevance[query_id] = {neighbour_id, augmented_id}
    for synthetic_id, path in _synthetic_images(workspace):
        images.append((synthetic_id, "synthetic", path))

    embeddings: dict[str, tuple[float, ...]] = {}
    started = time.perf_counter()
    for image_id, _, path in images:
        embeddings[image_id] = provider.embed_frame(path)
    inference_seconds = time.perf_counter() - started

    queries = [
        RetrievalQuery(query_id, embeddings[query_id], frozenset(relevant))
        for query_id, relevant in query_relevance.items()
    ]
    candidates = [
        VisualCandidate(
            image_id,
            embeddings[image_id],
            source=source,
            image_ref=str(path),
            attributes={"kind": "synthetic" if source == "synthetic" else "frame"},
        )
        for image_id, source, path in images
        if image_id not in query_relevance
    ]
    timing = {
        "imagesEmbedded": len(images),
        "embeddingSecondsTotal": inference_seconds,
        "embeddingMillisecondsPerImage": 1000 * inference_seconds / len(images),
    }
    return queries, candidates, timing


def synthetic_latency(query: tuple[float, ...], *, count: int, seed: int = 11) -> dict:
    """Time exact search against ``count`` random unit vectors of the query's width."""

    rng = random.Random(seed)
    dimension = len(query)
    candidates = []
    for index in range(count):
        vector = [rng.gauss(0.0, 1.0) for _ in range(dimension)]
        norm = sum(value * value for value in vector) ** 0.5
        candidates.append(
            VisualCandidate(f"random-{index:05d}", tuple(value / norm for value in vector))
        )
    retriever = InMemoryVisualCandidateRetriever(candidates)
    started = time.perf_counter()
    retriever.retrieve(query, limit=10)
    return {
        "candidates": count,
        "dimension": dimension,
        "retrievalMilliseconds": 1000 * (time.perf_counter() - started),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--media", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--latency-corpus", type=int, default=500)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    from instadescribe_worker.frame_embeddings import OnnxClipFrameEmbeddingProvider

    provider = OnnxClipFrameEmbeddingProvider(args.model.expanduser().absolute())
    with tempfile.TemporaryDirectory(prefix="visual-retrieval-eval-") as workspace:
        queries, candidates, timing = build_corpus(args.media.resolve(), Path(workspace), provider)
    retriever = InMemoryVisualCandidateRetriever(candidates)
    report = evaluate_retrieval(queries, retriever, k=args.k)
    provenance = provider.provenance
    report["embeddingModel"] = {
        "name": provenance.name,
        "runtime": provenance.runtime,
        "version": provenance.version,
        "digest": provenance.digest,
    }
    report["embeddingTiming"] = timing
    report["syntheticLatency"] = synthetic_latency(queries[0].embedding, count=args.latency_corpus)
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    print(
        f"queries: {report['queries']}  candidates: {report['candidates']}  "
        f"dimension: {report['dimension']}"
    )
    print(
        f"embedding inference: {timing['imagesEmbedded']} images, "
        f"{timing['embeddingMillisecondsPerImage']:.1f} ms/image"
    )
    print(f"exact retrieval: {report['retrievalMillisecondsPerQuery']:.2f} ms/query")
    synthetic = report["syntheticLatency"]
    print(
        f"exact retrieval, synthetic corpus: {synthetic['candidates']} x "
        f"{synthetic['dimension']} -> {synthetic['retrievalMilliseconds']:.1f} ms/query"
    )
    print("metrics:")
    for name, value in report["metrics"].items():
        print(f"  {name}: {'n/a' if value is None else f'{value:.3f}'}")
    for entry in report["rankings"]:
        print(f"\nquery: {entry['query']}")
        for item in entry["results"]:
            flag = "yes" if item["relevant"] else "no"
            print(
                f"  {item['rank']}. {item['candidate']}  cosine = {item['cosine']:.3f}  relevant = {flag}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
