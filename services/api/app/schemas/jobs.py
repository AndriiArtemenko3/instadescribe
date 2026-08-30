"""Create-job request/response contracts (camelCase aliases matching the
eventual Vite client; unknown fields forbidden so client attempts to supply
`provider`/`pipelineRevision` are rejected as validation errors).

Every bound here is a server-side spend/workload guard (ADR-0006/0008): the
declared duration and MIME are untrusted hints — G5 still validates real media
with ffprobe before any model call.
"""

import re
from typing import Any

from instadescribe_contracts.settings import EXTENSION_CONTENT_TYPES
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.config import get_settings
from app.domain.states import JobState

_LANGUAGE_RE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z]{2,4})?$")
_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]")
# C0 controls PostgreSQL cannot safely store (NUL) or that make no sense in
# names; prompts may keep \t \n \r.
_NAME_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_PROMPT_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class JobSummary(BaseModel):
    """Temporary legacy adapter plus the authoritative persisted state.

    ``status`` remains the existing four-value compatibility projection.
    ``canonicalState`` is the closed ``JobState`` enum and lets cloud clients
    distinguish an upload reservation from a processing job.
    ``sourceUploaded`` is separate because a verified source may truthfully
    remain ``AWAITING_UPLOAD`` while another job owns the compute slot.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    project_id: str = Field(alias="projectId")
    project_name: str
    starred: bool
    project_version: int = Field(alias="projectVersion", ge=1)
    status: str
    canonical_state: JobState = Field(alias="canonicalState")
    source_uploaded: bool = Field(alias="sourceUploaded")
    progress: int
    stage: str | None
    duration_secs: float | None
    model: str | None
    chunk_size: int | None
    pipeline_revision: str
    created_at: str | None
    updated_at: str | None
    error: str | None
    error_code: str | None


class UploadSettingsIn(BaseModel):
    # validate_default: a misconfigured server allowlist fails even for
    # requests that omit the field entirely (defaults are validated too).
    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, protected_namespaces=(), validate_default=True
    )

    model: str = "gpt-4.1"
    frame_quality: str = Field(default="low", alias="frameQuality")
    fps: float = 1.0
    chunk_size_secs: int = Field(default=60, alias="chunkSizeSecs")
    audio_extraction: bool = Field(default=True, alias="audioExtraction")
    custom_prompt: str = Field(default="", alias="customPrompt", max_length=2000)

    @field_validator("custom_prompt")
    @classmethod
    def _prompt_no_unsafe_controls(cls, v: str) -> str:
        if _PROMPT_CONTROL_RE.search(v):
            raise ValueError("custom prompt contains unsupported control characters")
        return v

    language: str | None = Field(default=None, max_length=10)
    detail_level: int = Field(default=3, ge=1, le=5, alias="detailLevel")
    preset_style: str = Field(default="documentary", alias="presetStyle")

    @field_validator("model")
    @classmethod
    def _model_allowed(cls, v: str) -> str:
        if v not in get_settings().model_allowlist:
            raise ValueError("model not in the approved allowlist")
        return v

    @field_validator("frame_quality")
    @classmethod
    def _quality_allowed(cls, v: str) -> str:
        if v not in get_settings().frame_quality_allowlist:
            raise ValueError("frame quality not in the approved allowlist")
        return v

    @field_validator("fps")
    @classmethod
    def _fps_allowed(cls, v: float) -> float:
        if v not in get_settings().fps_allowlist:
            raise ValueError("fps not in the approved allowlist")
        return v

    @field_validator("chunk_size_secs")
    @classmethod
    def _chunk_allowed(cls, v: int) -> int:
        if v not in get_settings().chunk_size_allowlist:
            raise ValueError("chunk size not in the approved allowlist")
        return v

    @field_validator("preset_style")
    @classmethod
    def _style_allowed(cls, v: str) -> str:
        if v not in get_settings().preset_style_allowlist:
            raise ValueError("preset style not in the approved allowlist")
        return v

    @field_validator("language")
    @classmethod
    def _language_shape(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not _LANGUAGE_RE.match(v):
            raise ValueError("language must be a short BCP-47-style tag")
        return v


class CreateJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, validate_default=True)

    name: str
    duration_secs: float = Field(alias="durationSecs", gt=0)
    file_name: str = Field(alias="fileName", min_length=1, max_length=255)
    content_type: str = Field(alias="contentType")
    file_size_bytes: int = Field(alias="fileSizeBytes", ge=1)
    settings: UploadSettingsIn = Field(default_factory=UploadSettingsIn)

    @field_validator("name")
    @classmethod
    def _name_trimmed(cls, v: str) -> str:
        if _NAME_CONTROL_RE.search(v):
            raise ValueError("name contains unsupported control characters")
        v = v.strip()
        if not 1 <= len(v) <= 200:
            raise ValueError("name must be 1-200 characters after trimming")
        return v

    @field_validator("duration_secs")
    @classmethod
    def _duration_bounded(cls, v: float) -> float:
        if v > get_settings().max_duration_secs:
            raise ValueError("declared duration exceeds the portfolio limit")
        return v

    @field_validator("file_size_bytes")
    @classmethod
    def _size_bounded(cls, v: int) -> int:
        if v > get_settings().max_upload_bytes:
            raise ValueError("file size exceeds the portfolio limit")
        return v

    @field_validator("content_type")
    @classmethod
    def _content_type_allowed(cls, v: str) -> str:
        if v not in get_settings().allowed_content_types:
            raise ValueError("content type not in the approved allowlist")
        return v

    @field_validator("file_name")
    @classmethod
    def _filename_safe(cls, v: str) -> str:
        if "\x00" in v or "/" in v or "\\" in v or ".." in v:
            raise ValueError("filename must be a plain basename")
        stem, dot, ext = v.rpartition(".")
        if not dot or f".{ext.lower()}" not in get_settings().allowed_extensions:
            raise ValueError("unsupported file extension")
        sanitized_stem = _FILENAME_SAFE_RE.sub("_", stem)[:120].strip("._-") or "upload"
        return f"{sanitized_stem}.{ext.lower()}"

    @model_validator(mode="after")
    def _extension_pairs_with_content_type(self) -> "CreateJobRequest":
        """G5.1 C3: each value being individually allowlisted is not enough —
        the PAIR is the contract (a .webm name cannot ride in as video/mp4)."""
        extension = "." + self.file_name.rpartition(".")[2].lower()
        expected = EXTENSION_CONTENT_TYPES.get(extension)
        if expected is None or self.content_type.lower() != expected:
            raise ValueError("file extension does not pair with the declared content type")
        return self

    def to_worker_settings(self) -> dict[str, Any]:
        """Normalized snake_case settings matching the existing pipeline
        contract (server.py:77-91); job_id/video_path are synthesized by the
        G5 executor at run time."""
        s = self.settings
        return {
            "model": s.model,
            "frame_quality": s.frame_quality,
            "fps": s.fps,
            "chunk_size": s.chunk_size_secs,
            "audio_extraction": s.audio_extraction,
            "custom_prompt": s.custom_prompt,
            "language": s.language,
            "detail_level": s.detail_level,
            "preset_style": s.preset_style,
            "project_name": self.name,
            "duration_secs": self.duration_secs,
        }
