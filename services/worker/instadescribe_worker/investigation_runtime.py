"""Bounded local observation runtime for video investigations.

The product worker owns prompts and runtime policy; the Apache baseline owns
only stable contracts and transparent fusion.  Ollama is accepted exclusively
on a credential-free loopback origin, redirects and proxies are disabled, and
the response is validated as a small structured object before it can become
evidence.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from instadescribe_investigation_core import (
    ActionType,
    BeliefConfig,
    CandidatePrior,
    ConnectivityPolicy,
    DeterministicLocalRunner,
    EvidenceContribution,
    EvidenceItem,
    EvidenceKind,
    FrameDescriptor,
    FrameEmbeddingProvider,
    FrameRejection,
    InvestigationKind,
    InvestigationStep,
    Keyframe,
    KeyframeSelectionConfig,
    LocalRunExpectation,
    ModelProvenance,
    SelectionWeights,
    SourceRecord,
    StaticObservationAdapter,
    StepStatus,
    VerificationState,
    perceptual_hash,
    select_keyframes,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from instadescribe_worker.config import WorkerSettings, validate_semantic_keyframe_settings
from instadescribe_worker.failures import FailureCode, JobFailure
from instadescribe_worker.frame_embeddings import OnnxClipFrameEmbeddingProvider

_MAX_RESPONSE_BYTES = 1_000_000
_MAX_FRAME_BYTES = 3_000_000
_PROMPT_VERSION = "observe-v1"
_FIXTURE_TIME = datetime(2025, 1, 1, tzinfo=UTC)
_VIDEO_DEMUXERS = {".mp4": "mov", ".mov": "mov", ".webm": "matroska,webm"}
_OLLAMA_OPTIONS = {"temperature": 0, "num_predict": 1800}
_OLLAMA_KEEP_ALIVE = "5m"
_SELECTOR_VERSION = "ffmpeg-uniform+heuristic-v1"
_SEMANTIC_SELECTOR_VERSION = "ffmpeg-uniform+heuristic-v1+semantic-v1"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InvestigationRuntimeSettings(_StrictModel):
    """Exact non-secret configuration revalidated inside the isolated child."""

    investigation_runtime: Literal["ollama", "fixture"]
    investigation_test_fixture_enabled: bool = False
    investigation_test_fixture_scenario: Literal["supportive", "abstention"] = "supportive"
    investigation_model: str = Field(min_length=1, max_length=120)
    investigation_ollama_url: str = Field(max_length=200)
    investigation_timeout_secs: int = Field(ge=30, le=900)
    investigation_max_keyframes: int = Field(ge=4, le=24)
    investigation_batch_size: int = Field(ge=4, le=8)
    investigation_image_long_edge: int = Field(ge=768, le=1024)
    investigation_semantic_keyframes_enabled: bool = False
    investigation_semantic_novelty_weight: float = Field(default=0.3, ge=0, le=1)
    investigation_semantic_similarity_threshold: float | None = Field(default=None, ge=-1, le=1)
    investigation_frame_embedding_model_path: str | None = Field(default=None, max_length=1024)

    @model_validator(mode="after")
    def _semantic_keyframes_are_usable(self) -> InvestigationRuntimeSettings:
        validate_semantic_keyframe_settings(
            enabled=self.investigation_semantic_keyframes_enabled,
            novelty_weight=self.investigation_semantic_novelty_weight,
            similarity_threshold=self.investigation_semantic_similarity_threshold,
            model_path=self.investigation_frame_embedding_model_path,
        )
        return self

    @model_validator(mode="after")
    def _fixture_is_explicit(self) -> InvestigationRuntimeSettings:
        if self.investigation_runtime == "fixture" and not self.investigation_test_fixture_enabled:
            raise ValueError("fixture runtime requires explicit test enablement")
        if self.investigation_runtime != "fixture" and self.investigation_test_fixture_enabled:
            raise ValueError("fixture enablement requires the fixture runtime")
        if (
            self.investigation_runtime != "fixture"
            and self.investigation_test_fixture_scenario != "supportive"
        ):
            raise ValueError("fixture scenario requires the fixture runtime")
        return self

    @field_validator("investigation_model")
    @classmethod
    def _model_identifier(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}", value):
            raise ValueError("investigation model identifier is invalid")
        return value

    @field_validator("investigation_ollama_url")
    @classmethod
    def _loopback_origin(cls, value: str) -> str:
        parsed = urlsplit(value.strip())
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("Ollama URL must be a credential-free loopback HTTP origin")
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError("Ollama URL port is invalid") from error
        if port is None or not 1 <= port <= 65535:
            raise ValueError("Ollama URL must include a valid port")
        return value.strip().rstrip("/")


class _SourceRecordPayload(_StrictModel):
    source_id: str = Field(min_length=1, max_length=256)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    collected_at: datetime
    license_basis: str = Field(min_length=1, max_length=500)
    publisher: str | None = Field(default=None, max_length=500)
    source_url: str | None = Field(default=None, max_length=2_048)
    published_at: datetime | None = None
    consent_basis: str | None = Field(default=None, max_length=500)
    redistribution_policy: str = Field(min_length=1, max_length=120)
    retention_policy: str = Field(min_length=1, max_length=500)

    @field_validator("collected_at", "published_at")
    @classmethod
    def _timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("source timestamps must be timezone-aware")
        return value

    def to_core(self) -> SourceRecord:
        return SourceRecord(**self.model_dump())


class _CandidatePriorPayload(_StrictModel):
    candidate_id: str = Field(min_length=1, max_length=256)
    label: str = Field(min_length=1, max_length=500)
    prior: float = Field(gt=0, le=1)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

    @model_validator(mode="after")
    def _coordinates_are_a_pair(self) -> _CandidatePriorPayload:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("candidate latitude and longitude must be supplied together")
        return self

    def to_core(self) -> CandidatePrior:
        return CandidatePrior(**self.model_dump())


class _ModelProvenancePayload(_StrictModel):
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=120)
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    runtime: str = Field(min_length=1, max_length=120)
    prompt_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    def to_core(self) -> ModelProvenance:
        return ModelProvenance(**self.model_dump())


class _BeliefConfigPayload(_StrictModel):
    temperature: float = Field(gt=0)
    minimum_confidence: float = Field(ge=0, le=1)
    minimum_margin: float = Field(ge=0, le=1)
    maximum_normalized_entropy: float = Field(ge=0, le=1)
    minimum_independent_groups: int = Field(ge=0)
    minimum_group_support: float = Field(ge=0, le=1)
    conflict_threshold: float = Field(ge=0, le=1)
    unverified_weight: float = Field(ge=0, le=1)

    def to_core(self) -> BeliefConfig:
        return BeliefConfig(**self.model_dump())


class InvestigationRunExpectationPayload(_StrictModel):
    """Strict parent-authored inputs accepted by the isolated final-run child."""

    source: _SourceRecordPayload
    investigation_id: str = Field(min_length=1, max_length=256)
    trace_id: str = Field(min_length=1, max_length=256)
    candidates: list[_CandidatePriorPayload] = Field(min_length=1, max_length=8)
    model_provenance: list[_ModelProvenancePayload] = Field(max_length=1)
    belief_config: _BeliefConfigPayload
    kind: Literal["geolocateProvenance"]
    connectivity_policy: Literal["local"]

    @model_validator(mode="after")
    def _candidate_ids_are_unique(self) -> InvestigationRunExpectationPayload:
        identifiers = [candidate.candidate_id for candidate in self.candidates]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("candidate IDs must be unique")
        return self

    def to_core(self) -> LocalRunExpectation:
        return LocalRunExpectation(
            source=self.source.to_core(),
            investigation_id=self.investigation_id,
            trace_id=self.trace_id,
            candidates=tuple(candidate.to_core() for candidate in self.candidates),
            model_provenance=tuple(model.to_core() for model in self.model_provenance),
            belief_config=self.belief_config.to_core(),
            kind=InvestigationKind(self.kind),
            connectivity_policy=ConnectivityPolicy(self.connectivity_policy),
        )


class _Candidate(_StrictModel):
    label: str = Field(min_length=1, max_length=120)
    prior: float = Field(gt=0, le=1)

    @field_validator("label")
    @classmethod
    def _label_trimmed(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("candidate label is empty")
        return value


class _Contribution(_StrictModel):
    candidateIndex: int = Field(ge=0, le=7)
    score: float = Field(ge=-1, le=1)


class _BBox(_StrictModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def _inside_frame(self) -> _BBox:
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("bbox exceeds normalized frame bounds")
        return self


class _Observation(_StrictModel):
    summary: str = Field(min_length=1, max_length=500)
    kind: Literal["visual", "ocr", "metadata"]
    frameIndex: int = Field(ge=0, le=7)
    correlationGroup: str = Field(min_length=1, max_length=120)
    reliability: float = Field(ge=0, le=1)
    contributions: list[_Contribution] = Field(min_length=1, max_length=8)
    bbox: _BBox | None = None

    @field_validator("summary", "correlationGroup")
    @classmethod
    def _bounded_text(cls, value: str) -> str:
        return " ".join(value.split())


class _InitialObservation(_StrictModel):
    candidates: list[_Candidate] = Field(min_length=2, max_length=8)
    evidence: list[_Observation] = Field(default_factory=list, max_length=32)


class _ContinuationObservation(_StrictModel):
    evidence: list[_Observation] = Field(default_factory=list, max_length=32)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


@dataclass(frozen=True, slots=True)
class _OllamaCallResult:
    payload: dict
    started_at: datetime
    completed_at: datetime
    latency_ms: int


@dataclass(frozen=True, slots=True)
class ExtractedFrame:
    descriptor: FrameDescriptor
    path: Path
    # Populated for selected frames only: the core's ranked view including the
    # semantic diagnostics (embedding_similarity_max, semantic_novelty).
    keyframe: Keyframe | None = None


@dataclass(frozen=True, slots=True)
class RankedFrames:
    """Selected frames plus the selection diagnostics the audit step records."""

    selected: tuple[ExtractedFrame, ...]
    rejected: tuple[FrameRejection, ...]
    candidate_count: int
    selector_version: str
    semantic_enabled: bool
    embedding_inference_calls: int = 0
    embedding_dimension: int | None = None
    embedding_model: ModelProvenance | None = None

    def step_attributes(self) -> dict:
        rejections: dict[str, int] = {}
        for item in self.rejected:
            rejections[item.reason.value] = rejections.get(item.reason.value, 0) + 1
        attributes: dict = {
            "candidateFrames": self.candidate_count,
            "selectedFrames": len(self.selected),
            "rejections": rejections,
        }
        if self.semantic_enabled:
            attributes["semanticKeyframes"] = {
                "embeddingInferenceCalls": self.embedding_inference_calls,
                "embeddingDimension": self.embedding_dimension,
                "embeddingModel": (
                    None
                    if self.embedding_model is None
                    else {
                        "name": self.embedding_model.name,
                        "version": self.embedding_model.version,
                        "digest": self.embedding_model.digest,
                        "runtime": self.embedding_model.runtime,
                    }
                ),
                "semanticDuplicatesRejected": rejections.get("semanticDuplicate", 0),
            }
        return attributes


def _run_ffmpeg_frame(
    media_path: Path,
    destination: Path,
    *,
    seconds: float,
    long_edge: int,
) -> None:
    executable = shutil.which("ffmpeg")
    if executable is None:
        raise JobFailure(FailureCode.PIPELINE_FAILED, "local media decoder is unavailable")
    extension = media_path.suffix.lower()
    demuxer = _VIDEO_DEMUXERS.get(extension)
    if demuxer is None:
        raise JobFailure(FailureCode.INVALID_MEDIA, "local media container is unsupported")
    command = [
        executable,
        "-max_alloc",
        "67108864",
        "-nostdin",
        "-v",
        "error",
        "-threads",
        "1",
        "-filter_threads",
        "1",
        "-protocol_whitelist",
        "file,pipe",
        "-max_streams",
        "32",
        "-f",
        demuxer,
        *(
            ["-enable_drefs", "0", "-use_absolute_path", "0"]
            if extension in {".mp4", ".mov"}
            else []
        ),
        "-ss",
        f"{seconds:.3f}",
        "-i",
        str(media_path),
        "-frames:v",
        "1",
        "-vf",
        (f"scale='if(gt(iw,ih),min({long_edge},iw),-2)':'if(gt(iw,ih),-2,min({long_edge},ih))'"),
        "-q:v",
        "3",
        "-y",
        str(destination),
    ]
    try:
        subprocess.run(
            command,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise JobFailure(
            FailureCode.PIPELINE_FAILED,
            "local keyframe extraction failed",
        ) from error


def _image_features(path: Path) -> tuple[int, int, float, float, float]:
    # Pillow is intentionally lazy: the deterministic offline fixture child
    # does not need the image stack, while real keyframe extraction still
    # fails closed if the production dependency is unavailable.
    from PIL import Image, ImageFilter, ImageStat

    try:
        with Image.open(path) as image:
            image.load()
            width, height = image.size
            gray = image.convert("L")
            stats = ImageStat.Stat(gray)
            mean = float(stats.mean[0])
            edge_stats = ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES))
            edge_mean = float(edge_stats.mean[0])
            edge_variance = float(edge_stats.var[0])
    except Exception as error:
        raise JobFailure(FailureCode.PIPELINE_FAILED, "extracted keyframe is invalid") from error
    sharpness = min(1.0, math.log1p(edge_variance) / math.log1p(6000))
    exposure = max(0.0, 1.0 - abs(mean - 127.5) / 127.5)
    ocr_proxy = min(1.0, edge_mean / 42.0)
    return width, height, sharpness, exposure, ocr_proxy


def _frame_embedding_provider(settings: WorkerSettings) -> FrameEmbeddingProvider | None:
    """Return the configured provider, or None when semantic selection is off.

    Disabled mode never constructs a provider, so no inference runtime or model
    file is touched. Enabled mode fails closed on a missing configuration or
    model instead of degrading to pHash-only selection.
    """

    if not settings.investigation_semantic_keyframes_enabled:
        return None
    model_path = settings.investigation_frame_embedding_model_path
    if model_path is None or not model_path.strip():
        raise JobFailure(
            FailureCode.INVALID_SETTINGS,
            "semantic keyframe selection is enabled, but no frame embedding model path "
            "is configured",
        )
    return OnnxClipFrameEmbeddingProvider(Path(model_path))


def _selection_config(settings: WorkerSettings) -> KeyframeSelectionConfig:
    if not settings.investigation_semantic_keyframes_enabled:
        return KeyframeSelectionConfig(
            max_keyframes=settings.investigation_max_keyframes,
            max_per_shot=3,
            selector_version=_SELECTOR_VERSION,
        )
    # Soft term (weight) and hard gate (threshold) are configured independently
    # and keep their distinct meanings inside the Apache selector.
    return KeyframeSelectionConfig(
        max_keyframes=settings.investigation_max_keyframes,
        max_per_shot=3,
        semantic_similarity_threshold=settings.investigation_semantic_similarity_threshold,
        selector_version=_SEMANTIC_SELECTOR_VERSION,
        weights=SelectionWeights(semantic_novelty=settings.investigation_semantic_novelty_weight),
    )


def extract_candidate_frames(
    media_path: Path,
    workspace: Path,
    *,
    source_sha256: str,
    duration_seconds: float,
    settings: WorkerSettings,
    embedding_provider: FrameEmbeddingProvider | None = None,
) -> tuple[ExtractedFrame, ...]:
    """Uniformly sample candidate frames and compute their cheap descriptors.

    When an embedding provider is supplied every candidate is embedded exactly
    once here; the selector later reuses ``FrameDescriptor.embedding`` for every
    comparison, so ranking never re-runs the model.
    """

    candidate_count = min(settings.investigation_max_keyframes * 2, 48)
    frames_dir = workspace / "investigation-frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[ExtractedFrame] = []
    previous_phash: str | None = None
    for index in range(candidate_count):
        seconds = duration_seconds * (index + 1) / (candidate_count + 1)
        destination = frames_dir / f"frame-{index:03d}.jpg"
        _run_ffmpeg_frame(
            media_path,
            destination,
            seconds=seconds,
            long_edge=settings.investigation_image_long_edge,
        )
        body = destination.read_bytes()
        if not body or len(body) > _MAX_FRAME_BYTES:
            raise JobFailure(FailureCode.PIPELINE_FAILED, "extracted keyframe size is invalid")
        digest = hashlib.sha256(body).hexdigest()
        frame_phash = perceptual_hash(destination)
        width, height, sharpness, exposure, ocr_proxy = _image_features(destination)
        if previous_phash is None:
            novelty = 1.0
        else:
            novelty = min(
                1.0,
                (int(previous_phash, 16) ^ int(frame_phash, 16)).bit_count() / 32.0,
            )
        previous_phash = frame_phash
        embedding = (
            embedding_provider.embed_frame(destination) if embedding_provider is not None else None
        )
        try:
            descriptor = FrameDescriptor(
                frame_id=f"frame-{index:03d}",
                artifact_id=f"frame-{digest[:20]}",
                source_content_sha256=source_sha256,
                content_sha256=digest,
                shot_index=index,
                time_ms=round(seconds * 1000),
                size_bytes=len(body),
                width=width,
                height=height,
                perceptual_hash=frame_phash,
                sharpness=sharpness,
                exposure_quality=exposure,
                novelty=novelty,
                ocr_density=ocr_proxy,
                motion_stability=0.75,
                embedding=embedding,
            )
        except ValueError as error:
            raise JobFailure(
                FailureCode.PIPELINE_FAILED, "extracted keyframe descriptor is invalid"
            ) from error
        extracted.append(ExtractedFrame(descriptor=descriptor, path=destination))
    return tuple(extracted)


def rank_candidate_frames(
    candidates: tuple[ExtractedFrame, ...],
    *,
    settings: WorkerSettings,
    embedding_provider: FrameEmbeddingProvider | None = None,
) -> RankedFrames:
    """Run the open deterministic selector over already-described candidates."""

    config = _selection_config(settings)
    try:
        selection = select_keyframes(tuple(item.descriptor for item in candidates), config=config)
    except ValueError as error:
        raise JobFailure(
            FailureCode.PIPELINE_FAILED, "keyframe selection input is invalid"
        ) from error
    by_artifact = {item.descriptor.artifact_id: item for item in candidates}
    selected = tuple(
        replace(by_artifact[keyframe.artifact.artifact_id], keyframe=keyframe)
        for keyframe in selection.selected
    )
    embedded = [item.descriptor.embedding for item in candidates if item.descriptor.embedding]
    return RankedFrames(
        selected=selected,
        rejected=selection.rejected,
        candidate_count=len(candidates),
        selector_version=selection.selector_version,
        semantic_enabled=settings.investigation_semantic_keyframes_enabled,
        embedding_inference_calls=len(embedded),
        embedding_dimension=len(embedded[0]) if embedded else None,
        embedding_model=embedding_provider.provenance if embedding_provider is not None else None,
    )


def extract_ranked_frames(
    media_path: Path,
    workspace: Path,
    *,
    source_sha256: str,
    duration_seconds: float,
    settings: WorkerSettings,
    embedding_provider: FrameEmbeddingProvider | None = None,
) -> RankedFrames:
    """Uniform fallback extraction followed by open deterministic ranking.

    PySceneDetect can replace this descriptor provider without changing the
    selector or evidence contract.  Sampling twice the publication bound
    gives the deduper room to reject weak/near-identical frames.  The
    embedding provider is injected here (or built from settings when semantic
    selection is enabled) and never instantiated inside the Apache selector.
    """

    provider = (
        embedding_provider
        if embedding_provider is not None
        else _frame_embedding_provider(settings)
    )
    if not settings.investigation_semantic_keyframes_enabled:
        provider = None
    candidates = extract_candidate_frames(
        media_path,
        workspace,
        source_sha256=source_sha256,
        duration_seconds=duration_seconds,
        settings=settings,
        embedding_provider=provider,
    )
    return rank_candidate_frames(candidates, settings=settings, embedding_provider=provider)


def _load_images(frames: tuple[ExtractedFrame, ...]) -> list[str]:
    images: list[str] = []
    for item in frames:
        body = item.path.read_bytes()
        if not body or len(body) > _MAX_FRAME_BYTES:
            raise JobFailure(FailureCode.PIPELINE_FAILED, "keyframe upload payload is invalid")
        images.append(base64.b64encode(body).decode("ascii"))
    return images


def _post_ollama(
    settings: WorkerSettings,
    frames: tuple[ExtractedFrame, ...],
    *,
    prompt: str,
    schema: dict,
) -> _OllamaCallResult:
    payload = json.dumps(
        {
            "model": settings.investigation_model,
            "messages": [{"role": "user", "content": prompt, "images": _load_images(frames)}],
            "stream": False,
            "think": False,
            "format": schema,
            "options": _OLLAMA_OPTIONS,
            "keep_alive": _OLLAMA_KEEP_ALIVE,
        },
        separators=(",", ":"),
    ).encode()
    request = urllib.request.Request(
        f"{settings.investigation_ollama_url}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    started_at = datetime.now(UTC)
    monotonic_started = time.monotonic()
    try:
        with opener.open(request, timeout=settings.investigation_timeout_secs) as response:
            content_type = response.headers.get_content_type()
            body = response.read(_MAX_RESPONSE_BYTES + 1)
    except (OSError, urllib.error.URLError, TimeoutError) as error:
        raise JobFailure(
            FailureCode.PIPELINE_FAILED,
            "local observation runtime is unavailable",
        ) from error
    if content_type != "application/json" or len(body) > _MAX_RESPONSE_BYTES:
        raise JobFailure(FailureCode.PIPELINE_FAILED, "local observation response is invalid")
    try:
        envelope = json.loads(body)
        content = envelope["message"]["content"]
        parsed = json.loads(content)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise JobFailure(
            FailureCode.PIPELINE_FAILED, "local observation output is invalid"
        ) from error
    if not isinstance(parsed, dict):
        raise JobFailure(FailureCode.PIPELINE_FAILED, "local observation output is invalid")
    completed_at = datetime.now(UTC)
    return _OllamaCallResult(
        payload=parsed,
        started_at=started_at,
        completed_at=completed_at,
        latency_ms=max(0, round((time.monotonic() - monotonic_started) * 1_000)),
    )


def _model_digest_from_tags(payload: object, model_name: str) -> str:
    """Resolve the immutable Ollama artifact digest for an exact model tag."""

    if not isinstance(payload, dict) or set(payload) != {"models"}:
        raise ValueError("Ollama tags response has an unexpected shape")
    models = payload["models"]
    if not isinstance(models, list) or len(models) > 1_000:
        raise ValueError("Ollama tags response has an invalid model list")
    for item in models:
        if not isinstance(item, dict):
            raise ValueError("Ollama tags response contains an invalid model")
        if model_name not in {item.get("name"), item.get("model")}:
            continue
        digest = item.get("digest")
        if not isinstance(digest, str):
            raise ValueError("Ollama model digest is missing")
        normalized = digest.removeprefix("sha256:").lower()
        if re.fullmatch(r"[a-f0-9]{64}", normalized) is None:
            raise ValueError("Ollama model digest is invalid")
        return normalized
    raise ValueError("configured Ollama model is not installed")


def _ollama_model_digest(settings: WorkerSettings) -> str:
    request = urllib.request.Request(
        f"{settings.investigation_ollama_url}/api/tags",
        headers={"Accept": "application/json"},
        method="GET",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=settings.investigation_timeout_secs) as response:
            content_type = response.headers.get_content_type()
            body = response.read(_MAX_RESPONSE_BYTES + 1)
    except (OSError, urllib.error.URLError, TimeoutError) as error:
        raise JobFailure(
            FailureCode.PIPELINE_FAILED,
            "local model provenance is unavailable",
        ) from error
    if content_type != "application/json" or len(body) > _MAX_RESPONSE_BYTES:
        raise JobFailure(FailureCode.PIPELINE_FAILED, "local model provenance is invalid")
    try:
        return _model_digest_from_tags(json.loads(body), settings.investigation_model)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise JobFailure(
            FailureCode.PIPELINE_FAILED, "local model provenance is invalid"
        ) from error


def _runtime_version_from_payload(payload: object) -> str:
    if not isinstance(payload, dict) or set(payload) != {"version"}:
        raise ValueError("Ollama version response has an unexpected shape")
    version = payload["version"]
    if (
        not isinstance(version, str)
        or re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+-]{0,63}", version) is None
    ):
        raise ValueError("Ollama runtime version is invalid")
    return version


def _ollama_runtime_version(settings: WorkerSettings) -> str:
    request = urllib.request.Request(
        f"{settings.investigation_ollama_url}/api/version",
        headers={"Accept": "application/json"},
        method="GET",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=settings.investigation_timeout_secs) as response:
            content_type = response.headers.get_content_type()
            body = response.read(_MAX_RESPONSE_BYTES + 1)
    except (OSError, urllib.error.URLError, TimeoutError) as error:
        raise JobFailure(
            FailureCode.PIPELINE_FAILED,
            "local runtime provenance is unavailable",
        ) from error
    if content_type != "application/json" or len(body) > _MAX_RESPONSE_BYTES:
        raise JobFailure(FailureCode.PIPELINE_FAILED, "local runtime provenance is invalid")
    try:
        return _runtime_version_from_payload(json.loads(body))
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise JobFailure(
            FailureCode.PIPELINE_FAILED, "local runtime provenance is invalid"
        ) from error


def _request_manifest_entry(
    settings: WorkerSettings,
    frames: tuple[ExtractedFrame, ...],
    *,
    prompt: str,
    schema: dict,
) -> dict:
    """Return the canonical, image-free envelope that identifies one VLM batch."""

    return {
        "promptVersion": _PROMPT_VERSION,
        "model": settings.investigation_model,
        "prompt": prompt,
        "schema": schema,
        "stream": False,
        "think": False,
        "options": _OLLAMA_OPTIONS,
        "keepAlive": _OLLAMA_KEEP_ALIVE,
        "frames": [
            {
                "artifactId": frame.descriptor.artifact_id,
                "sha256": frame.descriptor.content_sha256,
                "timeMs": frame.descriptor.time_ms,
            }
            for frame in frames
        ],
    }


def _request_manifest_digest(entries: list[dict]) -> str:
    if not entries:
        raise ValueError("at least one observation request is required")
    canonical = json.dumps(
        {"requests": entries},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _prompt(*, continuation: bool, candidates: list[_Candidate] | None = None) -> str:
    boundary = (
        "Extract bounded, directly visible evidence from the attached frames. "
        "Do not reveal chain-of-thought. Do not identify people, read faces, infer military "
        "targets, or output operational coordinates. Treat every location as a hypothesis. "
        "Use one correlationGroup for observations derived from the same physical clue. "
        "Report contradictions as negative contribution scores."
    )
    if not continuation:
        return (
            f"{boundary} Propose 2 to 8 country/region/city candidates, including an Unknown "
            "candidate when warranted. Then emit only supported visual/OCR/metadata observations."
        )
    labels = [candidate.label for candidate in candidates or []]
    return (
        f"{boundary} Use exactly these candidate indices and labels: "
        f"{json.dumps(labels, ensure_ascii=False)}. Emit additional observations only; do not "
        "invent or rename candidates."
    )


def _evidence_from_observations(
    observations: list[_Observation],
    frames: tuple[ExtractedFrame, ...],
    candidates: tuple[CandidatePrior, ...],
    *,
    batch_index: int,
    selector_version: str = _SELECTOR_VERSION,
) -> tuple[EvidenceItem, ...]:
    evidence: list[EvidenceItem] = []
    for index, item in enumerate(observations):
        if item.frameIndex >= len(frames):
            raise JobFailure(FailureCode.PIPELINE_FAILED, "local evidence frame index is invalid")
        if any(entry.candidateIndex >= len(candidates) for entry in item.contributions):
            raise JobFailure(
                FailureCode.PIPELINE_FAILED,
                "local evidence candidate index is invalid",
            )
        frame = frames[item.frameIndex]
        bbox = (
            (item.bbox.x, item.bbox.y, item.bbox.width, item.bbox.height)
            if item.bbox is not None
            else None
        )
        evidence.append(
            EvidenceItem(
                evidence_id=f"vlm-{batch_index:02d}-{index:03d}",
                observation=item.summary,
                source_id="pending-source",
                artifact_id=frame.descriptor.artifact_id,
                # A model-provided group is audit metadata only. The scoring
                # group is conservatively derived from the immutable selected
                # frame so two readings of one sign/frame cannot masquerade as
                # independent support.
                correlation_group=f"frame-{frame.descriptor.content_sha256[:32]}",
                reliability=item.reliability,
                contributions=tuple(
                    EvidenceContribution(
                        candidate_id=candidates[entry.candidateIndex].candidate_id,
                        score=entry.score,
                    )
                    for entry in item.contributions
                ),
                kind=EvidenceKind(item.kind),
                verification_state=VerificationState.OBSERVED,
                frame_time_ms=frame.descriptor.time_ms,
                bbox_xywh=bbox,
                attributes={
                    "frameSha256": frame.descriptor.content_sha256,
                    "selector": selector_version,
                    "modelCorrelationGroup": item.correlationGroup,
                },
            )
        )
    return tuple(evidence)


def fixture_candidates(
    scenario: Literal["supportive", "abstention"],
) -> tuple[CandidatePrior, ...]:
    """Return the parent-computable candidate priors for the test-only seam."""

    if scenario == "abstention":
        return (
            CandidatePrior("candidate-a", "Fixture candidate A", 0.34),
            CandidatePrior("candidate-b", "Fixture candidate B", 0.33),
            CandidatePrior("unknown", "Unknown", 0.33),
        )
    return (
        CandidatePrior("candidate-a", "Fixture candidate A", 0.45),
        CandidatePrior("candidate-b", "Fixture candidate B", 0.35),
        CandidatePrior("unknown", "Unknown", 0.20),
    )


def fixture_model_provenance(
    scenario: Literal["supportive", "abstention"],
) -> ModelProvenance:
    """Return immutable provenance the parent binds before child launch."""

    return ModelProvenance(
        name="deterministic-fixture",
        version="1",
        digest=hashlib.sha256(b"deterministic-fixture-v1").hexdigest(),
        runtime="in-process-test-seam",
        prompt_digest=hashlib.sha256(
            (
                "fixture-observation-v1"
                if scenario == "supportive"
                else "fixture-observation-v1:abstention"
            ).encode()
        ).hexdigest(),
    )


def _fixture_observation(
    source_sha256: str,
    scenario: Literal["supportive", "abstention"],
) -> tuple[tuple[CandidatePrior, ...], tuple[EvidenceItem, ...]]:
    candidates = fixture_candidates(scenario)
    if scenario == "abstention":
        evidence = tuple(
            EvidenceItem(
                evidence_id=f"fixture-keyframe-{index}",
                observation="A deterministic frame contains no supported location clue.",
                source_id="pending-source",
                artifact_id=f"frame-{digest}",
                correlation_group=f"fixture-keyframe-{index}",
                reliability=1.0,
                contributions=(EvidenceContribution("unknown", 0.0),),
                frame_time_ms=time_ms,
                created_at=_FIXTURE_TIME,
                attributes={"role": "keyframe", "fixture": True, "rank": index},
            )
            for index, (digest, time_ms) in enumerate(
                ((source_sha256[:20], 4_000), (source_sha256[20:40], 12_500)),
                start=1,
            )
        )
        return candidates, evidence
    evidence = (
        EvidenceItem(
            evidence_id="fixture-keyframe-1",
            observation="A deterministic high-information fixture frame was selected locally.",
            source_id="pending-source",
            artifact_id=f"frame-{source_sha256[:20]}",
            correlation_group="fixture-keyframe-1",
            reliability=1.0,
            contributions=(EvidenceContribution("unknown", 0.0),),
            frame_time_ms=4_000,
            created_at=_FIXTURE_TIME,
            attributes={"role": "keyframe", "fixture": True, "rank": 1},
        ),
        EvidenceItem(
            evidence_id="fixture-keyframe-2",
            observation="A second deterministic fixture frame preserves temporal context.",
            source_id="pending-source",
            artifact_id=f"frame-{source_sha256[20:40]}",
            correlation_group="fixture-keyframe-2",
            reliability=1.0,
            contributions=(EvidenceContribution("unknown", 0.0),),
            frame_time_ms=12_500,
            created_at=_FIXTURE_TIME,
            attributes={"role": "keyframe", "fixture": True, "rank": 2},
        ),
        EvidenceItem(
            evidence_id="fixture-sign",
            observation="A synthetic sign clue supports fixture candidate A.",
            source_id="pending-source",
            artifact_id=f"frame-{source_sha256[:20]}",
            correlation_group="fixture-sign-frame",
            reliability=0.9,
            contributions=(EvidenceContribution("candidate-a", 0.9),),
            kind=EvidenceKind.OCR,
            created_at=_FIXTURE_TIME,
            attributes={"fixture": True},
        ),
        EvidenceItem(
            evidence_id="fixture-facade",
            observation="Synthetic facade geometry independently supports fixture candidate A.",
            source_id="pending-source",
            artifact_id=f"frame-{source_sha256[20:40]}",
            correlation_group="fixture-facade-frame",
            reliability=0.85,
            contributions=(
                EvidenceContribution("candidate-a", 0.8),
                EvidenceContribution("candidate-b", -0.35),
            ),
            created_at=_FIXTURE_TIME,
            attributes={"fixture": True},
        ),
    )
    return candidates, evidence


def _keyframe_attributes(item: ExtractedFrame, rank: int, selector_version: str) -> dict:
    attributes: dict = {
        "role": "keyframe",
        "frameSha256": item.descriptor.content_sha256,
        "selector": selector_version,
        "rank": rank,
        "width": item.descriptor.width,
        "height": item.descriptor.height,
    }
    keyframe = item.keyframe
    if keyframe is not None and keyframe.semantic_novelty is not None:
        # Analyst-inspectable semantic signal; the raw vector is never serialized.
        attributes["informationScore"] = keyframe.information_score
        attributes["embeddingSimilarityMax"] = keyframe.embedding_similarity_max
        attributes["semanticNovelty"] = keyframe.semantic_novelty
    return attributes


def _keyframe_evidence(
    frames: tuple[ExtractedFrame, ...],
    candidates: tuple[CandidatePrior, ...],
    *,
    selector_version: str = _SELECTOR_VERSION,
) -> tuple[EvidenceItem, ...]:
    """Represent ranked frame metadata in the durable evidence ledger.

    The month-one Browser API seeks the immutable source video by timecode;
    raw JPEG crops remain inside the local workspace and are never uploaded
    implicitly. A zero contribution keeps selection metadata observable
    without changing the geographic posterior.
    """

    neutral_candidate = next(
        (candidate for candidate in candidates if candidate.candidate_id == "unknown"),
        candidates[0],
    )
    return tuple(
        EvidenceItem(
            evidence_id=f"keyframe-{item.descriptor.content_sha256[:20]}",
            observation=(
                "A locally ranked keyframe was selected for analyst review at "
                f"{item.descriptor.time_ms / 1000:.3f} seconds."
            ),
            source_id="pending-source",
            artifact_id=item.descriptor.artifact_id,
            correlation_group=f"keyframe-{item.descriptor.content_sha256[:20]}",
            reliability=1.0,
            contributions=(EvidenceContribution(neutral_candidate.candidate_id, 0.0),),
            frame_time_ms=item.descriptor.time_ms,
            attributes=_keyframe_attributes(item, rank, selector_version),
        )
        for rank, item in enumerate(frames, start=1)
    )


def run_local_observation(
    media_path: Path,
    workspace: Path,
    *,
    source: SourceRecord,
    duration_seconds: float,
    settings: WorkerSettings,
    investigation_id: str,
    trace_id: str,
    kind: InvestigationKind,
    expected_candidates: tuple[CandidatePrior, ...] | None = None,
    expected_model_provenance: tuple[ModelProvenance, ...] | None = None,
    belief_config: BeliefConfig | None = None,
):
    """Run the bounded local stage; connected retrieval is intentionally absent."""

    source_sha256 = source.content_sha256
    if settings.investigation_runtime == "fixture":
        if not settings.investigation_test_fixture_enabled:
            raise JobFailure(
                FailureCode.INVALID_SETTINGS,
                "fixture investigation runtime is disabled",
            )
        candidates, evidence = _fixture_observation(
            source_sha256,
            settings.investigation_test_fixture_scenario,
        )
        model = fixture_model_provenance(settings.investigation_test_fixture_scenario)
        if expected_candidates is not None and candidates != expected_candidates:
            raise JobFailure(
                FailureCode.INVALID_SETTINGS,
                "fixture candidates do not match the parent-owned expectation",
            )
        if expected_model_provenance is not None and expected_model_provenance != (model,):
            raise JobFailure(
                FailureCode.INVALID_SETTINGS,
                "fixture model provenance does not match the parent-owned expectation",
            )
    else:
        model_digest = _ollama_model_digest(settings)
        runtime_version = _ollama_runtime_version(settings)
        extraction_started_at = datetime.now(UTC)
        extraction_monotonic = time.monotonic()
        ranked = extract_ranked_frames(
            media_path,
            workspace,
            source_sha256=source_sha256,
            duration_seconds=duration_seconds,
            settings=settings,
        )
        frames = ranked.selected
        if not frames:
            raise JobFailure(FailureCode.PIPELINE_FAILED, "no valid keyframes were selected")
        extraction_completed_at = datetime.now(UTC)
        extraction_latency_ms = max(
            0,
            round((time.monotonic() - extraction_monotonic) * 1_000),
        )
        batches = tuple(
            frames[index : index + settings.investigation_batch_size]
            for index in range(0, len(frames), settings.investigation_batch_size)
        )
        initial_prompt = _prompt(continuation=False)
        initial_schema = _InitialObservation.model_json_schema()
        request_manifest = [
            _request_manifest_entry(
                settings,
                batches[0],
                prompt=initial_prompt,
                schema=initial_schema,
            )
        ]
        initial_call = _post_ollama(
            settings,
            batches[0],
            prompt=initial_prompt,
            schema=initial_schema,
        )
        batch_calls = [initial_call]
        initial = _InitialObservation.model_validate(initial_call.payload)
        prior_total = sum(item.prior for item in initial.candidates)
        candidates = tuple(
            CandidatePrior(f"candidate-{index + 1}", item.label, item.prior / prior_total)
            for index, item in enumerate(initial.candidates)
        )
        evidence_parts = [
            _evidence_from_observations(
                initial.evidence,
                batches[0],
                candidates,
                batch_index=0,
                selector_version=ranked.selector_version,
            )
        ]
        for batch_index, batch in enumerate(batches[1:], start=1):
            continuation_prompt = _prompt(continuation=True, candidates=initial.candidates)
            continuation_schema = _ContinuationObservation.model_json_schema()
            request_manifest.append(
                _request_manifest_entry(
                    settings,
                    batch,
                    prompt=continuation_prompt,
                    schema=continuation_schema,
                )
            )
            continuation_call = _post_ollama(
                settings,
                batch,
                prompt=continuation_prompt,
                schema=continuation_schema,
            )
            batch_calls.append(continuation_call)
            continued = _ContinuationObservation.model_validate(continuation_call.payload)
            evidence_parts.append(
                _evidence_from_observations(
                    continued.evidence,
                    batch,
                    candidates,
                    batch_index=batch_index,
                    selector_version=ranked.selector_version,
                )
            )
        evidence = _keyframe_evidence(
            frames, candidates, selector_version=ranked.selector_version
        ) + tuple(item for part in evidence_parts for item in part)
        prompt_digest = _request_manifest_digest(request_manifest)
        if _ollama_model_digest(settings) != model_digest:
            raise JobFailure(
                FailureCode.PIPELINE_FAILED,
                "local model changed during investigation",
            )
        model = ModelProvenance(
            name=settings.investigation_model,
            version=runtime_version,
            digest=model_digest,
            runtime="ollama-loopback",
            prompt_digest=prompt_digest,
        )

    if expected_candidates is not None and candidates != expected_candidates:
        raise JobFailure(
            FailureCode.PIPELINE_FAILED,
            "runtime candidates do not match the parent-owned expectation",
        )
    if expected_model_provenance is not None and (model,) != expected_model_provenance:
        raise JobFailure(
            FailureCode.PIPELINE_FAILED,
            "runtime provenance does not match the parent-owned expectation",
        )

    runner = DeterministicLocalRunner(
        observer=StaticObservationAdapter(evidence, provenance=model),
        candidates=candidates,
        belief_config=belief_config,
        # CI and on-stage replay must be byte-for-byte reproducible. Real
        # local inference keeps wall-clock provenance; only the explicit
        # fixture runtime uses a fixed instant.
        clock=(
            (lambda: _FIXTURE_TIME)
            if settings.investigation_runtime == "fixture"
            else (lambda: datetime.now(UTC))
        ),
    )
    result = runner.run(
        media_path,
        connectivity_policy=ConnectivityPolicy.LOCAL,
        kind=kind,
        source=source,
        investigation_id=investigation_id,
        trace_id=trace_id,
    )
    if settings.investigation_runtime == "fixture":
        return result

    keyframe_ids = tuple(
        item.evidence_id for item in result.evidence if item.attributes.get("role") == "keyframe"
    )
    batch_steps = tuple(
        InvestigationStep(
            step_id=f"observe-batch-{source_sha256[:16]}-{index:02d}",
            action=ActionType.OBSERVE,
            status=StepStatus.SUCCEEDED,
            started_at=call.started_at,
            completed_at=call.completed_at,
            output_evidence_ids=tuple(
                item.evidence_id
                for item in result.evidence
                if item.evidence_id.startswith(f"vlm-{index:02d}-")
            ),
            model_digest=model_digest,
            prompt_digest=_request_manifest_digest([request_manifest[index]]),
            tool_version=runtime_version,
            latency_ms=call.latency_ms,
            attributes={
                "batchIndex": index,
                "frameHashes": [frame["sha256"] for frame in request_manifest[index]["frames"]],
            },
        )
        for index, call in enumerate(batch_calls)
    )
    extract_step = InvestigationStep(
        step_id=f"extract-keyframes-{source_sha256[:20]}",
        action=ActionType.EXTRACT_KEYFRAMES,
        status=StepStatus.SUCCEEDED,
        started_at=extraction_started_at,
        completed_at=extraction_completed_at,
        output_evidence_ids=keyframe_ids,
        tool_version=ranked.selector_version,
        latency_ms=extraction_latency_ms,
        attributes=ranked.step_attributes(),
    )
    return replace(
        result,
        steps=(result.steps[0], extract_step, *batch_steps, result.steps[-1]),
    )
