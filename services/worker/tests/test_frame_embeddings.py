from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path

import numpy as np
import pytest
from instadescribe_investigation_core import FrameEmbeddingProvider, cosine_similarity
from instadescribe_worker.failures import JobFailure
from instadescribe_worker.frame_embeddings import (
    MODEL_NAME,
    RUNTIME_NAME,
    OnnxClipFrameEmbeddingProvider,
    preprocess_frame,
)
from PIL import Image


class _Tensor:
    def __init__(self, name: str, shape: list[object]) -> None:
        self.name = name
        self.shape = shape


class FakeSession:
    """Deterministic stand-in for onnxruntime with the same IO contract."""

    def __init__(
        self,
        *,
        outputs: list[np.ndarray] | None = None,
        input_name: str = "pixel_values",
        output_shape: list[object] | None = None,
    ) -> None:
        self.feeds: list[np.ndarray] = []
        self._outputs = list(outputs) if outputs is not None else None
        self._input_name = input_name
        self._output_shape = output_shape or ["batch_size", 4]

    def get_inputs(self):
        return [_Tensor(self._input_name, ["batch_size", 3, 224, 224])]

    def get_outputs(self):
        return [_Tensor("image_embeds", self._output_shape)]

    def run(self, output_names, input_feed):
        pixels = input_feed["pixel_values"]
        self.feeds.append(pixels)
        if self._outputs is not None:
            return [self._outputs.pop(0)]
        # A pixel-derived vector: identical frames embed identically, different
        # frames differ, which is all the provider contract needs.
        vector = np.asarray(
            [[pixels.mean(), pixels[0, 0].mean(), pixels[0, 1].std(), 1.0]], dtype=np.float32
        )
        return [vector]


def _write_frame(path: Path, *, seed: int, size: tuple[int, int] = (320, 180)) -> Path:
    rng = np.random.default_rng(seed)
    pixels = rng.integers(0, 256, size=(size[1], size[0], 3), dtype=np.uint8)
    Image.fromarray(pixels, "RGB").save(path, format="JPEG", quality=90)
    return path


def _model_file(tmp_path: Path) -> Path:
    model = tmp_path / "vision_model.onnx"
    model.write_bytes(b"not a real graph; the fake session ignores the bytes")
    return model


def test_provider_is_lazy_deterministic_and_satisfies_the_core_seam(tmp_path):
    model = _model_file(tmp_path)
    sessions: list[FakeSession] = []

    def factory(path: Path) -> FakeSession:
        assert path == model.resolve()
        sessions.append(FakeSession())
        return sessions[-1]

    provider = OnnxClipFrameEmbeddingProvider(
        model, session_factory=factory, runtime_version=lambda: "1.28.0-test"
    )
    assert isinstance(provider, FrameEmbeddingProvider)
    assert provider.network_access is False
    assert sessions == [] and not provider.loaded and provider.dimension is None

    first_frame = _write_frame(tmp_path / "a.jpg", seed=1)
    second_frame = _write_frame(tmp_path / "b.jpg", seed=2)
    first = provider.embed_frame(first_frame)
    again = provider.embed_frame(first_frame)
    second = provider.embed_frame(second_frame)

    assert len(sessions) == 1
    assert first == again
    assert first != second
    assert len(first) == len(second) == provider.dimension == 4
    assert all(math.isfinite(value) for value in first)
    assert sessions[0].feeds[0].shape == (1, 3, 224, 224)
    assert sessions[0].feeds[0].dtype == np.float32
    provenance = provider.provenance
    assert provenance is not None
    assert (provenance.name, provenance.runtime, provenance.version) == (
        MODEL_NAME,
        RUNTIME_NAME,
        "1.28.0-test",
    )
    assert provenance.digest == hashlib.sha256(model.read_bytes()).hexdigest()


def test_provider_rejects_unusable_model_paths(tmp_path):
    with pytest.raises(JobFailure, match="unavailable") as missing:
        OnnxClipFrameEmbeddingProvider(tmp_path / "absent.onnx")
    assert missing.value.code.value == "invalid_settings"
    with pytest.raises(JobFailure, match="absolute"):
        OnnxClipFrameEmbeddingProvider(Path("relative/model.onnx"))
    wrong_suffix = tmp_path / "model.bin"
    wrong_suffix.write_bytes(b"x")
    with pytest.raises(JobFailure, match="ONNX file"):
        OnnxClipFrameEmbeddingProvider(wrong_suffix)


def test_provider_rejects_a_graph_that_is_not_a_clip_vision_export(tmp_path):
    provider = OnnxClipFrameEmbeddingProvider(
        _model_file(tmp_path),
        session_factory=lambda path: FakeSession(input_name="input_ids"),
        runtime_version=lambda: "test",
    )
    with pytest.raises(JobFailure, match="failed to load") as error:
        provider.embed_frame(_write_frame(tmp_path / "a.jpg", seed=1))
    assert error.value.code.value == "pipeline_failed"


@pytest.mark.parametrize(
    ("outputs", "message"),
    [
        ([np.asarray([[1.0, float("nan"), 0.5, 0.1]], dtype=np.float32)], "non-finite"),
        ([np.zeros((1, 0), dtype=np.float32)], "empty"),
        ([np.zeros((1, 4), dtype=np.float32)], "zero norm"),
        ([np.ones((2, 4), dtype=np.float32)], "output is invalid"),
        (
            [np.ones((1, 4), dtype=np.float32), np.ones((1, 5), dtype=np.float32)],
            "dimension changed",
        ),
    ],
)
def test_provider_fails_explicitly_on_malformed_embeddings(tmp_path, outputs, message):
    provider = OnnxClipFrameEmbeddingProvider(
        _model_file(tmp_path),
        session_factory=lambda path: FakeSession(outputs=outputs, output_shape=["batch_size", "d"]),
        runtime_version=lambda: "test",
    )
    frame = _write_frame(tmp_path / "a.jpg", seed=3)
    with pytest.raises(JobFailure, match=message) as error:
        for _ in outputs:
            provider.embed_frame(frame)
    assert error.value.code.value == "pipeline_failed"


def test_provider_rejects_undecodable_frames(tmp_path):
    provider = OnnxClipFrameEmbeddingProvider(
        _model_file(tmp_path),
        session_factory=lambda path: FakeSession(),
        runtime_version=lambda: "test",
    )
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"\xff\xd8not-a-jpeg")
    with pytest.raises(JobFailure, match="input is invalid"):
        provider.embed_frame(broken)


def test_preprocessing_follows_the_clip_recipe(tmp_path):
    wide = tmp_path / "wide.png"
    Image.new("RGB", (448, 224), (255, 255, 255)).save(wide)
    pixels = preprocess_frame(wide)

    assert pixels.shape == (1, 3, 224, 224)
    assert pixels.dtype == np.float32
    expected_red = (1.0 - 0.48145466) / 0.26862954
    assert pixels[0, 0].min() == pytest.approx(expected_red, abs=1e-5)
    assert pixels[0, 0].max() == pytest.approx(expected_red, abs=1e-5)
    tall = tmp_path / "tall.png"
    Image.new("RGB", (100, 400), (0, 0, 0)).save(tall)
    assert preprocess_frame(tall).shape == (1, 3, 224, 224)


REAL_MODEL = os.environ.get("INSTADESCRIBE_TEST_FRAME_EMBEDDING_MODEL")


@pytest.mark.skipif(
    not REAL_MODEL,
    reason="INSTADESCRIBE_TEST_FRAME_EMBEDDING_MODEL not set (path to a CLIP vision ONNX export)",
)
def test_real_clip_vision_model_smoke(tmp_path):
    provider = OnnxClipFrameEmbeddingProvider(Path(REAL_MODEL))
    frame = _write_frame(tmp_path / "noise.jpg", seed=7)
    flat = tmp_path / "flat.png"
    Image.new("RGB", (320, 180), (30, 90, 200)).save(flat)

    first = provider.embed_frame(frame)
    again = provider.embed_frame(frame)
    other = provider.embed_frame(flat)

    assert provider.dimension == 512 and len(first) == 512
    assert first == again
    assert all(math.isfinite(value) for value in first)
    assert math.hypot(*first) > 1, "the export returns raw, not unit-normalized, vectors"
    assert cosine_similarity(first, other) < 0.9
    assert provider.provenance is not None
    assert provider.provenance.runtime == RUNTIME_NAME
    assert provider.network_access is False
