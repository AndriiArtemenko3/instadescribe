"""The InstaDescribe v0.1 stored-job-settings contract (G5.1 B4).

`jobs.settings` is written once by the API (`to_worker_settings()`) and read
by the worker. The worker must not trust the database blindly: a malformed or
tampered persisted document is a DETERMINISTIC `invalid_settings` failure,
not three retryable child crashes. This module is the single strict shape
both sides share — exact keys, strict types (bool is not an int here), and
the same v0.1 allowlists/bounds the API enforces at creation time.

These allowlists are v0.1 CODE POLICY, not environment configuration.
"""

import re

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

MODEL_ALLOWLIST = ("gpt-4.1",)
FRAME_QUALITY_ALLOWLIST = ("low",)
FPS_ALLOWLIST = (0.5, 1.0)
CHUNK_SIZE_ALLOWLIST = (60, 120)
DETAIL_LEVEL_MIN, DETAIL_LEVEL_MAX = 1, 5
PRESET_STYLE_ALLOWLIST = ("documentary", "cinematic", "news", "sports", "education")
VOICE_ALLOWLIST = ("onyx", "nova", "alloy", "shimmer", "echo", "fable")
MAX_CUSTOM_PROMPT_CHARS = 2000
MAX_PROJECT_NAME_CHARS = 200
# The persisted worker contract covers both the legacy portfolio route and the
# Integration API beta.  Legacy request validation keeps its own five-minute
# limit; a beta job may legitimately persist the published 60-minute maximum.
MAX_DURATION_SECS = 3600

# Exact filename-extension <-> MIME pairing (G5.1 C3): each side is already
# allowlisted individually; this map makes the PAIR the contract, so a
# ".webm" name cannot ride in under "video/mp4".
EXTENSION_CONTENT_TYPES = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
}

_LANGUAGE_RE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$")
_PROMPT_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class StoredJobSettings(BaseModel):
    """Strict parse of the persisted settings document — extra keys forbidden,
    coercions disabled where they could mask tampering."""

    model_config = ConfigDict(extra="forbid", strict=True)

    model: str
    frame_quality: str
    fps: float
    chunk_size: int
    audio_extraction: StrictBool
    custom_prompt: str = Field(max_length=MAX_CUSTOM_PROMPT_CHARS)
    language: str | None
    detail_level: int = Field(ge=DETAIL_LEVEL_MIN, le=DETAIL_LEVEL_MAX)
    preset_style: str
    # Integration API v1 persists the stable product voice alias for the
    # later review/render phase.  Legacy jobs predate that field, so the
    # canonical default preserves their existing onyx behaviour.
    voice: str = "onyx"
    project_name: str = Field(min_length=1, max_length=MAX_PROJECT_NAME_CHARS)
    duration_secs: float = Field(gt=0, le=MAX_DURATION_SECS)

    @field_validator("model")
    @classmethod
    def _model(cls, v: str) -> str:
        if v not in MODEL_ALLOWLIST:
            raise ValueError("model not in the v0.1 allowlist")
        return v

    @field_validator("frame_quality")
    @classmethod
    def _quality(cls, v: str) -> str:
        if v not in FRAME_QUALITY_ALLOWLIST:
            raise ValueError("frame quality not in the v0.1 allowlist")
        return v

    @field_validator("fps", mode="before")
    @classmethod
    def _fps(cls, v):
        # JSON round-trips 1.0 as 1; accept exact ints for allowlisted floats
        # but nothing else (strict mode rejects strings/bools upstream).
        if type(v) is int:
            v = float(v)
        if v not in FPS_ALLOWLIST:
            raise ValueError("fps not in the v0.1 allowlist")
        return v

    @field_validator("chunk_size")
    @classmethod
    def _chunk(cls, v: int) -> int:
        if v not in CHUNK_SIZE_ALLOWLIST:
            raise ValueError("chunk size not in the v0.1 allowlist")
        return v

    @field_validator("custom_prompt")
    @classmethod
    def _prompt(cls, v: str) -> str:
        if _PROMPT_CONTROL_RE.search(v):
            raise ValueError("custom prompt contains unsupported control characters")
        return v

    @field_validator("language")
    @classmethod
    def _language(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if len(v) > 10 or not _LANGUAGE_RE.match(v):
            raise ValueError("language must be a short BCP-47-style tag")
        return v

    @field_validator("preset_style")
    @classmethod
    def _style(cls, v: str) -> str:
        if v not in PRESET_STYLE_ALLOWLIST:
            raise ValueError("preset style not in the v0.1 allowlist")
        return v

    @field_validator("voice")
    @classmethod
    def _voice(cls, v: str) -> str:
        if v not in VOICE_ALLOWLIST:
            raise ValueError("voice not in the product alias allowlist")
        return v
