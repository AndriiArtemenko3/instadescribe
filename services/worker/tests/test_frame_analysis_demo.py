"""Manifest arithmetic and shape for the frame analysis demo export.

These tests drive the real manifest builder with deterministic descriptors, so
they need no video file and no model weights. The full pipeline is exercised by
running the script itself against local media.
"""

from __future__ import annotations

import importlib.util
import json
import math
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from instadescribe_investigation_core import (
    FrameDescriptor,
    ModelProvenance,
    cosine_similarity,
    l2_norm,
    select_keyframes,
    semantic_novelty,
)
from instadescribe_worker.investigation_runtime import ExtractedFrame, RankedFrames

REPO = Path(__file__).resolve().parents[3]


def _load_demo():
    path = REPO / "services" / "worker" / "scripts" / "frame_analysis_demo.py"
    spec = importlib.util.spec_from_file_location("frame_analysis_demo", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


demo = _load_demo()

DIMENSION = 6  # deliberately not 512: the builder must read the width off the data
PROVENANCE = ModelProvenance(
    name="clip-vision-onnx",
    version="1.0.0",
    digest="a" * 64,
    runtime="onnxruntime-cpu",
)


def _embedding(*leading: float) -> tuple[float, ...]:
    """A DIMENSION-wide embedding whose direction is set by the leading values."""

    values = list(leading) + [0.0] * (DIMENSION - len(leading))
    # Non-unit magnitude on purpose: the provider does not normalize either.
    return tuple(value * 3.0 for value in values)


def _descriptor(index: int, embedding: tuple[float, ...], **overrides) -> FrameDescriptor:
    defaults = {
        "frame_id": f"frame-{index:03d}",
        "artifact_id": f"artifact-{index:03d}",
        "source_content_sha256": "0" * 64,
        "content_sha256": f"{index:064x}",
        "shot_index": 0,
        "time_ms": index * 1_000,
        "size_bytes": 1_024,
        "width": 640,
        "height": 360,
        "sharpness": 0.6,
        "exposure_quality": 0.7,
        "novelty": 0.5,
        "ocr_density": 0.1,
        "motion_stability": 0.8,
        "embedding": embedding,
    }
    defaults.update(overrides)
    return FrameDescriptor(**defaults)


def _rank(descriptors, *, max_keyframes: int = 8):
    """Run the real core selector and wrap it the way the runtime does."""

    from instadescribe_investigation_core import KeyframeSelectionConfig, SelectionWeights

    config = KeyframeSelectionConfig(
        max_keyframes=max_keyframes,
        minimum_time_distance_ms=0,
        perceptual_hash_distance=0,
        weights=SelectionWeights(semantic_novelty=0.3),
    )
    selection = select_keyframes(tuple(descriptors), config=config)
    by_artifact = {item.artifact_id: item for item in descriptors}
    selected = tuple(
        ExtractedFrame(
            descriptor=by_artifact[keyframe.artifact.artifact_id],
            path=Path("unused"),
            keyframe=keyframe,
        )
        for keyframe in selection.selected
    )
    return RankedFrames(
        selected=selected,
        rejected=selection.rejected,
        candidate_count=len(descriptors),
        selector_version=selection.selector_version,
        semantic_enabled=True,
        embedding_inference_calls=len(descriptors),
        embedding_dimension=DIMENSION,
        embedding_model=PROVENANCE,
    )


def _build(descriptors, ranked=None) -> dict:
    candidates = tuple(
        ExtractedFrame(descriptor=descriptor, path=Path("unused")) for descriptor in descriptors
    )
    return demo.build_manifest(
        candidates,
        ranked if ranked is not None else _rank(descriptors),
        media_name="fixture.mp4",
        duration_seconds=len(descriptors),
        source_sha256="0" * 64,
        provider_provenance=PROVENANCE,
        selector_config={"maxKeyframes": 8, "noveltyWeight": 0.3, "similarityThreshold": None},
    )


# --- centroid arithmetic ---------------------------------------------------


def test_centroid_of_identical_directions_is_that_direction() -> None:
    embeddings = [_embedding(1, 0), _embedding(2, 0), _embedding(0.5, 0)]
    centroid = demo.embedding_centroid(embeddings)

    assert l2_norm(centroid) == pytest.approx(1.0)
    for embedding in embeddings:
        assert cosine_similarity(embedding, centroid) == pytest.approx(1.0)


def test_identical_frames_all_report_centroid_similarity_of_one() -> None:
    descriptors = [_descriptor(index, _embedding(1, 1)) for index in range(3)]
    manifest = _build(descriptors)

    for frame in manifest["frames"]:
        assert frame["vectorMetrics"]["clipCentroidSimilarity"] == pytest.approx(1.0)


def test_a_frame_pointing_away_from_the_rest_scores_below_the_others() -> None:
    descriptors = [
        _descriptor(0, _embedding(1, 0)),
        _descriptor(1, _embedding(1, 0.05)),
        _descriptor(2, _embedding(1, -0.05)),
        _descriptor(3, _embedding(0, 1)),  # orthogonal to the rest
    ]
    manifest = _build(descriptors)
    scores = [frame["vectorMetrics"]["clipCentroidSimilarity"] for frame in manifest["frames"]]

    assert scores[3] == min(scores)
    assert scores[3] < min(scores[:3])


def test_normalizing_before_averaging_stops_a_long_vector_dominating() -> None:
    """A large-magnitude outlier must not drag the centroid onto itself."""

    short = [_embedding(1, 0), _embedding(1, 0)]
    huge = tuple(value * 1_000 for value in _embedding(0, 1))
    centroid = demo.embedding_centroid([*short, huge])

    assert cosine_similarity(centroid, short[0]) > cosine_similarity(centroid, huge)


def test_centroid_rejects_undefined_inputs() -> None:
    with pytest.raises(ValueError, match="empty set"):
        demo.embedding_centroid([])
    with pytest.raises(ValueError, match="share one dimension"):
        demo.embedding_centroid([(1.0, 0.0), (1.0, 0.0, 0.0)])
    with pytest.raises(ValueError, match="zero-norm"):
        demo.unit_vector((0.0, 0.0))


# --- per-frame metrics -----------------------------------------------------


def test_previous_frame_similarity_is_null_for_the_first_frame_only() -> None:
    descriptors = [_descriptor(index, _embedding(1, index * 0.1)) for index in range(4)]
    manifest = _build(descriptors)

    values = [frame["vectorMetrics"]["previousFrameSimilarity"] for frame in manifest["frames"]]
    assert values[0] is None
    assert all(value is not None for value in values[1:])


def test_previous_frame_similarity_orders_a_repeat_above_a_change() -> None:
    descriptors = [
        _descriptor(0, _embedding(1, 0)),
        _descriptor(1, _embedding(1, 0)),  # same direction as its predecessor
        _descriptor(2, _embedding(0, 1)),  # turns away from its predecessor
    ]
    manifest = _build(descriptors)
    values = [frame["vectorMetrics"]["previousFrameSimilarity"] for frame in manifest["frames"]]

    assert values[1] == pytest.approx(1.0)
    assert values[2] == pytest.approx(0.0, abs=1e-9)
    assert values[1] > values[2]


def test_frames_are_ordered_by_time_and_indexed_from_zero() -> None:
    descriptors = [
        _descriptor(0, _embedding(1, 0), time_ms=5_000),
        _descriptor(1, _embedding(0, 1), time_ms=1_000),
        _descriptor(2, _embedding(1, 1), time_ms=3_000),
    ]
    manifest = _build(descriptors)

    assert [frame["timeMs"] for frame in manifest["frames"]] == [1_000, 3_000, 5_000]
    assert [frame["index"] for frame in manifest["frames"]] == [0, 1, 2]


def test_novelty_matches_the_core_selector_function() -> None:
    descriptors = [_descriptor(index, _embedding(1, index * 0.3)) for index in range(6)]
    ranked = _rank(descriptors, max_keyframes=3)
    manifest = _build(descriptors, ranked)

    selected = {item.descriptor.frame_id: item.descriptor.embedding for item in ranked.selected}
    for frame in manifest["frames"]:
        embedding = next(
            item.embedding for item in descriptors if item.frame_id == frame["frameId"]
        )
        others = [value for frame_id, value in selected.items() if frame_id != frame["frameId"]]
        nearest, novelty = semantic_novelty(embedding, others)
        metrics = frame["vectorMetrics"]
        assert metrics["nearestSelectedSimilarity"] == pytest.approx(nearest, abs=1e-6)
        assert metrics["semanticNovelty"] == pytest.approx(novelty, abs=1e-6)


def test_a_frame_is_never_compared_against_itself() -> None:
    """Self-comparison would report a similarity of one for every keyframe."""

    descriptors = [
        _descriptor(0, _embedding(1, 0)),
        _descriptor(1, _embedding(0, 1)),
    ]
    ranked = _rank(descriptors)
    manifest = _build(descriptors, ranked)

    assert len(ranked.selected) == 2
    for frame in manifest["frames"]:
        assert frame["vectorMetrics"]["nearestSelectedSimilarity"] < 1.0
        assert frame["vectorMetrics"]["mostSimilarFrameId"] != frame["frameId"]


def test_most_similar_frame_id_names_the_frame_the_similarity_came_from() -> None:
    descriptors = [
        _descriptor(0, _embedding(1, 0)),
        _descriptor(1, _embedding(1, 0.02)),  # nearly the same direction as frame 0
        _descriptor(2, _embedding(0, 1)),
    ]
    ranked = _rank(descriptors)
    manifest = _build(descriptors, ranked)
    by_id = {frame["frameId"]: frame for frame in manifest["frames"]}

    assert by_id["frame-000"]["vectorMetrics"]["mostSimilarFrameId"] == "frame-001"
    assert by_id["frame-001"]["vectorMetrics"]["mostSimilarFrameId"] == "frame-000"


# --- selection results and shape -------------------------------------------


def test_keyframe_block_carries_the_real_selection_outcome() -> None:
    descriptors = [_descriptor(index, _embedding(1, index * 0.4)) for index in range(5)]
    ranked = _rank(descriptors, max_keyframes=2)
    manifest = _build(descriptors, ranked)

    selected_ids = {item.descriptor.frame_id for item in ranked.selected}
    reasons = {item.frame_id: item.reason.value for item in ranked.rejected}
    assert selected_ids and reasons

    for frame in manifest["frames"]:
        block = frame["keyframe"]
        if frame["frameId"] in selected_ids:
            assert block["selected"] is True
            assert block["rank"] is not None
            assert block["informationScore"] is not None
            assert block["rejectionReason"] is None
        else:
            assert block["selected"] is False
            assert block["rank"] is None
            assert block["informationScore"] is None
            assert block["rejectionReason"] == reasons[frame["frameId"]]


def test_selection_time_readings_come_from_the_selector_not_the_final_set() -> None:
    descriptors = [_descriptor(index, _embedding(1, index * 0.4)) for index in range(4)]
    ranked = _rank(descriptors, max_keyframes=3)
    manifest = _build(descriptors, ranked)
    by_id = {frame["frameId"]: frame for frame in manifest["frames"]}

    for item in ranked.selected:
        block = by_id[item.descriptor.frame_id]["keyframe"]
        assert block["selectionNearestSimilarity"] == (
            None
            if item.keyframe.embedding_similarity_max is None
            else pytest.approx(item.keyframe.embedding_similarity_max, abs=1e-6)
        )
    first = next(item for item in ranked.selected if item.keyframe.rank == 0)
    # Nothing was selected before the first keyframe, so it has no reading.
    assert by_id[first.descriptor.frame_id]["keyframe"]["selectionNearestSimilarity"] is None


def test_embedding_dimension_is_read_from_the_data_not_assumed() -> None:
    descriptors = [_descriptor(index, _embedding(1, index * 0.2)) for index in range(3)]
    manifest = _build(descriptors)

    assert manifest["embeddingModel"]["dimension"] == DIMENSION
    assert manifest["embeddingModel"]["dimension"] != 512


def test_manifest_serializes_no_embedding_vectors() -> None:
    descriptors = [_descriptor(index, _embedding(1, index * 0.2)) for index in range(4)]
    manifest = _build(descriptors)

    numeric_runs: list[int] = []

    def walk(node) -> None:
        if isinstance(node, list):
            if node and all(isinstance(value, int | float) for value in node):
                numeric_runs.append(len(node))
            for value in node:
                walk(value)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)

    walk(json.loads(json.dumps(manifest)))
    assert not numeric_runs, "the manifest must carry scalars only, never raw embeddings"


def test_every_metric_in_the_manifest_is_documented() -> None:
    descriptors = [_descriptor(index, _embedding(1, index * 0.2)) for index in range(3)]
    manifest = _build(descriptors)
    frame = manifest["frames"][0]

    documented = set(manifest["metricDefinitions"])
    assert set(frame["vectorMetrics"]) <= documented
    assert {"informationScore", "selectionNearestSimilarity", "selectionSemanticNovelty"} <= (
        documented
    )


def test_no_metric_is_presented_as_a_confidence() -> None:
    """A cosine may be disclaimed as one of these, never labelled as one."""

    descriptors = [_descriptor(index, _embedding(1, index * 0.2)) for index in range(3)]
    manifest = _build(descriptors)
    definitions = manifest["metricDefinitions"]
    names = set(definitions) | set(manifest["frames"][0]["vectorMetrics"])

    for forbidden in ("confidence", "probability", "accuracy", "certainty"):
        assert not any(forbidden in name.lower() for name in names)
        for text in definitions.values():
            for match in re.finditer(forbidden, text.lower()):
                preceding = text.lower()[: match.start()]
                assert preceding.rstrip().endswith("not a"), (
                    f"{forbidden!r} must appear only as a disclaimer, got: {text}"
                )


def test_quality_features_are_carried_through_unchanged() -> None:
    descriptor = _descriptor(0, _embedding(1, 0), sharpness=0.42, ocr_density=0.13)
    manifest = _build([descriptor, _descriptor(1, _embedding(0, 1))])
    quality = manifest["frames"][0]["qualityMetrics"]

    assert quality["sharpness"] == pytest.approx(0.42)
    assert quality["ocrDensity"] == pytest.approx(0.13)
    assert quality["motionStability"] == pytest.approx(0.8)


def test_a_frame_without_an_embedding_is_refused() -> None:
    descriptors = [_descriptor(0, _embedding(1, 0)), _descriptor(1, None)]
    ranked = SimpleNamespace(
        selected=(),
        rejected=(),
        selector_version="test",
        semantic_enabled=False,
    )
    with pytest.raises(ValueError, match="must carry an embedding"):
        _build(descriptors, ranked)


def test_manifest_is_json_serializable_and_finite() -> None:
    descriptors = [_descriptor(index, _embedding(1, index * 0.2)) for index in range(4)]
    payload = json.loads(json.dumps(_build(descriptors)))

    def check(node) -> None:
        if isinstance(node, float):
            assert math.isfinite(node)
        elif isinstance(node, list):
            for value in node:
                check(value)
        elif isinstance(node, dict):
            for value in node.values():
                check(value)

    check(payload)
    assert payload["schemaVersion"] == demo.SCHEMA_VERSION
