from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from instadescribe_investigation_core import InMemoryVisualCandidateRetriever, VisualCandidate
from PIL import Image, ImageDraw

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from visual_retrieval_eval import (  # noqa: E402
    RetrievalQuery,
    evaluate_retrieval,
    synthetic_latency,
)

LANDMARK_A = (0.82, 0.11, 0.43, 0.05)
LANDMARK_A_VARIANT = (0.80, 0.13, 0.45, 0.07)
UNRELATED_SCENE = (-0.10, 0.78, 0.04, 0.60)
INDOOR = (0.05, -0.20, 0.90, 0.10)


def test_evaluate_retrieval_reports_metrics_and_inspectable_rankings():
    retriever = InMemoryVisualCandidateRetriever(
        [
            VisualCandidate("landmark-variant", LANDMARK_A_VARIANT, source="fixture"),
            VisualCandidate("street", UNRELATED_SCENE, source="fixture"),
            VisualCandidate("indoor", INDOOR, source="fixture"),
        ]
    )
    queries = [
        RetrievalQuery("landmark", LANDMARK_A, frozenset({"landmark-variant"})),
        RetrievalQuery("street-query", UNRELATED_SCENE, frozenset({"indoor"})),
    ]

    report = evaluate_retrieval(queries, retriever, k=3)

    assert report["queries"] == 2 and report["candidates"] == 3 and report["dimension"] == 4
    metrics = report["metrics"]
    # The street query ranks the landmark variant (cos ~0.07) above the indoor
    # scene (cos ~-0.07), so its relevant item sits at rank 3.
    assert metrics["top1"] == 0.5
    assert metrics["hitRate@3"] == 1.0
    assert metrics["recall@3"] == 1.0
    assert metrics["mrr"] == pytest.approx((1 + 1 / 3) / 2)
    assert metrics["meanPositiveSimilarity"] > metrics["meanDistractorSimilarity"]
    assert metrics["positiveDistractorMargin"] > 0
    first = report["rankings"][0]
    assert first["query"] == "landmark"
    assert first["results"][0] == {
        "rank": 1,
        "candidate": "landmark-variant",
        "cosine": pytest.approx(0.9991, abs=1e-3),
        "relevant": True,
    }
    assert first["results"][1]["relevant"] is False
    assert report["retrievalMillisecondsPerQuery"] >= 0
    with pytest.raises(ValueError, match="at least one query"):
        evaluate_retrieval([], retriever, k=3)


def test_synthetic_latency_corpus_is_seeded_and_dimension_bound():
    first = synthetic_latency((1.0, 0.0, 0.0), count=25)
    second = synthetic_latency((1.0, 0.0, 0.0), count=25)

    assert first["candidates"] == second["candidates"] == 25
    assert first["dimension"] == 3
    assert first["retrievalMilliseconds"] >= 0


REAL_MODEL = os.environ.get("INSTADESCRIBE_TEST_FRAME_EMBEDDING_MODEL")


def _shape_image(path: Path, *, circle: tuple[int, int, int, int] | None, grid: bool) -> Path:
    image = Image.new("RGB", (320, 240), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    if circle is not None:
        draw.ellipse(circle, fill=(200, 30, 30))
    if grid:
        for x in range(0, 320, 16):
            draw.line((x, 0, x, 240), fill=(20, 20, 120))
        for y in range(0, 240, 16):
            draw.line((0, y, 320, y), fill=(20, 20, 120))
    image.save(path)
    return path


@pytest.mark.skipif(
    not REAL_MODEL,
    reason="INSTADESCRIBE_TEST_FRAME_EMBEDDING_MODEL not set (path to a CLIP vision ONNX export)",
)
def test_real_clip_embeddings_rank_related_image_above_unrelated(tmp_path):
    from instadescribe_worker.frame_embeddings import OnnxClipFrameEmbeddingProvider

    provider = OnnxClipFrameEmbeddingProvider(Path(REAL_MODEL))
    query = _shape_image(tmp_path / "query.png", circle=(80, 60, 200, 180), grid=False)
    related = _shape_image(tmp_path / "related.png", circle=(110, 70, 250, 210), grid=False)
    unrelated = _shape_image(tmp_path / "unrelated.png", circle=None, grid=True)
    retriever = InMemoryVisualCandidateRetriever(
        [
            VisualCandidate(
                "unrelated-grid", provider.embed_frame(unrelated), image_ref=str(unrelated)
            ),
            VisualCandidate(
                "related-circle", provider.embed_frame(related), image_ref=str(related)
            ),
        ]
    )

    results = retriever.retrieve(provider.embed_frame(query), limit=2)

    assert [item.candidate_id for item in results] == ["related-circle", "unrelated-grid"]
    assert results[0].embedding_similarity > results[1].embedding_similarity + 0.05
    assert retriever.dimension == 512
    assert results[0].image_ref == str(related)
