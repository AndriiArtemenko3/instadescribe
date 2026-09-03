"""Local CLIP-vision ONNX frame embeddings for semantic keyframe selection.

The provider turns one extracted frame image into a raw embedding vector for the
Apache core's cosine-based semantic novelty. It runs a CLIP ViT-B/32 vision encoder
exported to ONNX (``pixel_values`` -> ``image_embeds``) on CPU through onnxruntime,
which is already part of the worker dependency lock. Nothing here touches the
network: the model file is a configured local path and is opened lazily on the
first frame, so a worker with semantic selection disabled never loads it.

Preprocessing is the CLIP feature-extractor recipe, applied to a private copy of
the frame: RGB conversion, bicubic resize of the shortest edge to 224, centre crop
to 224x224, rescale to [0, 1] and per-channel normalization. The model output is
returned as-is; it is NOT unit-normalized (norms are about 11 for this model), and
the core's ``cosine_similarity`` divides by both L2 norms so magnitude never enters
the comparison.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol

from instadescribe_investigation_core import ModelProvenance

from instadescribe_worker.failures import FailureCode, JobFailure

_IMAGE_SIZE = 224
_IMAGE_MEAN = (0.48145466, 0.4578275, 0.40821073)
_IMAGE_STD = (0.26862954, 0.26130258, 0.27577711)
_INPUT_NAME = "pixel_values"
_OUTPUT_NAME = "image_embeds"
_MAX_MODEL_BYTES = 2_000_000_000
_MAX_EMBEDDING_DIMENSION = 4096
MODEL_NAME = "clip-vision-onnx"
RUNTIME_NAME = "onnxruntime-cpu"


class EmbeddingSession(Protocol):
    """The slice of ``onnxruntime.InferenceSession`` this provider relies on."""

    def get_inputs(self) -> Sequence[Any]: ...

    def get_outputs(self) -> Sequence[Any]: ...

    def run(self, output_names: list[str] | None, input_feed: dict[str, Any]) -> Sequence[Any]: ...


SessionFactory = Callable[[Path], EmbeddingSession]


def _onnxruntime_session(model_path: Path) -> EmbeddingSession:
    # Imported here on purpose: importing this module must stay cheap and the
    # inference runtime must only load when a frame is actually embedded.
    import onnxruntime

    options = onnxruntime.SessionOptions()
    # One thread keeps the isolated child bounded and the float sums reproducible,
    # matching the single-threaded ffmpeg extraction in the same child.
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    options.log_severity_level = 3
    return onnxruntime.InferenceSession(
        str(model_path),
        sess_options=options,
        providers=["CPUExecutionProvider"],
    )


def _onnxruntime_version() -> str:
    import onnxruntime

    return str(onnxruntime.__version__)


def preprocess_frame(frame_path: Path) -> Any:
    """Return a float32 ``[1, 3, 224, 224]`` array in CLIP's normalized space."""

    import numpy as np
    from PIL import Image

    with Image.open(frame_path) as image:
        image.load()
        rgb = image.convert("RGB")
    width, height = rgb.size
    if width <= 0 or height <= 0:
        raise ValueError("frame has no pixels")
    short, long = (width, height) if width <= height else (height, width)
    new_long = max(_IMAGE_SIZE, int(_IMAGE_SIZE * long / short))
    target = (_IMAGE_SIZE, new_long) if width <= height else (new_long, _IMAGE_SIZE)
    resized = rgb.resize(target, Image.Resampling.BICUBIC)
    left = (resized.width - _IMAGE_SIZE) // 2
    top = (resized.height - _IMAGE_SIZE) // 2
    cropped = resized.crop((left, top, left + _IMAGE_SIZE, top + _IMAGE_SIZE))
    pixels = np.asarray(cropped, dtype=np.float32) / np.float32(255.0)
    normalized = (pixels - np.asarray(_IMAGE_MEAN, dtype=np.float32)) / np.asarray(
        _IMAGE_STD, dtype=np.float32
    )
    return np.ascontiguousarray(normalized.transpose(2, 0, 1)[np.newaxis, ...])


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _static_dimension(shape: object) -> int | None:
    if not isinstance(shape, list | tuple) or len(shape) != 2:
        raise ValueError("embedding output must be a [batch, dimension] tensor")
    last = shape[-1]
    if isinstance(last, bool) or not isinstance(last, int):
        return None
    if not 1 <= last <= _MAX_EMBEDDING_DIMENSION:
        raise ValueError("embedding dimension is outside the supported range")
    return last


class OnnxClipFrameEmbeddingProvider:
    """``FrameEmbeddingProvider`` backed by a local CLIP vision ONNX export."""

    def __init__(
        self,
        model_path: Path,
        *,
        session_factory: SessionFactory = _onnxruntime_session,
        runtime_version: Callable[[], str] = _onnxruntime_version,
    ) -> None:
        configured = model_path.expanduser()
        if not configured.is_absolute():
            raise JobFailure(
                FailureCode.INVALID_SETTINGS, "frame embedding model path must be absolute"
            )
        # Check the configured name before resolving: model caches (for example the
        # Hugging Face hub layout) expose the .onnx file as a symlink to a blob.
        if configured.suffix.lower() != ".onnx":
            raise JobFailure(
                FailureCode.INVALID_SETTINGS, "frame embedding model must be an ONNX file"
            )
        resolved = configured.resolve()
        if not resolved.is_file():
            raise JobFailure(
                FailureCode.INVALID_SETTINGS,
                "frame embedding model file is unavailable",
            )
        if resolved.stat().st_size > _MAX_MODEL_BYTES:
            raise JobFailure(
                FailureCode.INVALID_SETTINGS, "frame embedding model exceeds the size bound"
            )
        self._model_path = resolved
        self._session_factory = session_factory
        self._runtime_version = runtime_version
        self._session: EmbeddingSession | None = None
        self._dimension: int | None = None
        self._provenance: ModelProvenance | None = None

    @property
    def model_path(self) -> Path:
        return self._model_path

    @property
    def network_access(self) -> bool:
        return False

    @property
    def loaded(self) -> bool:
        return self._session is not None

    @property
    def dimension(self) -> int | None:
        """Embedding width once known; None until the model is loaded."""

        return self._dimension

    @property
    def provenance(self) -> ModelProvenance | None:
        """Model identity; reading it loads the model so the digest is real."""

        self._load()
        return self._provenance

    def _load(self) -> EmbeddingSession:
        if self._session is not None:
            return self._session
        try:
            session = self._session_factory(self._model_path)
            input_names = [item.name for item in session.get_inputs()]
            outputs = {item.name: item.shape for item in session.get_outputs()}
            if input_names != [_INPUT_NAME] or _OUTPUT_NAME not in outputs:
                raise ValueError("model is not a CLIP vision export")
            dimension = _static_dimension(outputs[_OUTPUT_NAME])
            version = self._runtime_version()
        except JobFailure:
            raise
        except Exception as error:
            raise JobFailure(
                FailureCode.PIPELINE_FAILED, "frame embedding model failed to load"
            ) from error
        self._provenance = ModelProvenance(
            name=MODEL_NAME,
            version=version,
            digest=_sha256_file(self._model_path),
            runtime=RUNTIME_NAME,
        )
        self._dimension = dimension
        self._session = session
        return session

    def embed_frame(self, frame_path: Path) -> tuple[float, ...]:
        session = self._load()
        try:
            pixels = preprocess_frame(frame_path)
        except Exception as error:
            raise JobFailure(
                FailureCode.PIPELINE_FAILED, "frame embedding input is invalid"
            ) from error
        try:
            outputs = session.run([_OUTPUT_NAME], {_INPUT_NAME: pixels})
        except Exception as error:
            raise JobFailure(
                FailureCode.PIPELINE_FAILED, "frame embedding inference failed"
            ) from error
        return self._validate_output(outputs)

    def _validate_output(self, outputs: Sequence[Any]) -> tuple[float, ...]:
        try:
            if len(outputs) != 1:
                raise ValueError("unexpected output count")
            rows = outputs[0].tolist() if hasattr(outputs[0], "tolist") else list(outputs[0])
            if len(rows) != 1:
                raise ValueError("unexpected batch size")
            vector = tuple(float(value) for value in rows[0])
        except (AttributeError, TypeError, ValueError) as error:
            raise JobFailure(
                FailureCode.PIPELINE_FAILED, "frame embedding output is invalid"
            ) from error
        if not vector or len(vector) > _MAX_EMBEDDING_DIMENSION:
            raise JobFailure(
                FailureCode.PIPELINE_FAILED, "frame embedding output is empty or too wide"
            )
        if self._dimension is None:
            self._dimension = len(vector)
        if len(vector) != self._dimension:
            raise JobFailure(FailureCode.PIPELINE_FAILED, "frame embedding dimension changed")
        if not all(math.isfinite(value) for value in vector):
            raise JobFailure(
                FailureCode.PIPELINE_FAILED, "frame embedding contains non-finite values"
            )
        if math.hypot(*vector) == 0:
            raise JobFailure(FailureCode.PIPELINE_FAILED, "frame embedding has zero norm")
        return vector
