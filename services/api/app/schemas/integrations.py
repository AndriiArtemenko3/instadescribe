"""Strict request contracts for the public Integration API."""

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    field_validator,
    model_validator,
)

from app.core.config import get_settings
from app.schemas.jobs import CreateJobRequest, UploadSettingsIn
from app.schemas.scenes import VOICE_ALLOWLIST

_NAME_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_PROMPT_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_LANGUAGE_RE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$")
_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]")
_TRANSCRIPT_CONTENT_TYPES = {
    "vtt": "text/vtt",
    "srt": "application/x-subrip",
}
MAX_TRANSCRIPT_BYTES = 10 * 1024 * 1024


def _bounded_text(value: str, *, field: str, maximum: int) -> str:
    if _NAME_CONTROL_RE.search(value):
        raise ValueError(f"{field} contains unsupported control characters")
    value = value.strip()
    if not 1 <= len(value) <= maximum:
        raise ValueError(f"{field} must be 1-{maximum} characters after trimming")
    return value


def _safe_basename(value: str, *, allowed_extensions: set[str]) -> str:
    if "\x00" in value or "/" in value or "\\" in value or ".." in value:
        raise ValueError("fileName must be a plain basename")
    stem, dot, extension = value.rpartition(".")
    normalized_extension = f".{extension.lower()}" if dot else ""
    if not dot or normalized_extension not in allowed_extensions:
        raise ValueError("unsupported file extension")
    sanitized_stem = _FILENAME_SAFE_RE.sub("_", stem)[:120].strip("._-") or "upload"
    return f"{sanitized_stem}{normalized_extension}"


class IntegrationProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: StrictStr

    @field_validator("name")
    @classmethod
    def _name_valid(cls, value: str) -> str:
        return _bounded_text(value, field="name", maximum=200)


class IntegrationProjectPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: StrictStr | None = None
    external_id: StrictStr | None = Field(default=None, alias="externalId")
    starred: StrictBool | None = None

    @field_validator("name")
    @classmethod
    def _name_valid(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _bounded_text(value, field="name", maximum=200)

    @field_validator("external_id")
    @classmethod
    def _external_id_valid(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _bounded_text(value, field="externalId", maximum=255)

    @model_validator(mode="after")
    def _has_change(self) -> "IntegrationProjectPatch":
        if not self.model_fields_set:
            raise ValueError("at least one mutable field is required")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("name cannot be null")
        if "starred" in self.model_fields_set and self.starred is None:
            raise ValueError("starred cannot be null")
        return self

    def column_values(self) -> dict[str, str | bool | None]:
        values: dict[str, str | bool | None] = {}
        if "name" in self.model_fields_set and self.name is not None:
            values["name"] = self.name
        if "external_id" in self.model_fields_set:
            values["external_id"] = self.external_id
        if "starred" in self.model_fields_set and self.starred is not None:
            values["starred"] = self.starred
        return values


class IntegrationProjectSelector(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: uuid.UUID | None = None
    name: StrictStr | None = None
    external_id: StrictStr | None = Field(default=None, alias="externalId")

    @field_validator("name")
    @classmethod
    def _name_valid(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _bounded_text(value, field="project.name", maximum=200)

    @field_validator("external_id")
    @classmethod
    def _external_id_valid(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _bounded_text(value, field="project.externalId", maximum=255)

    @model_validator(mode="after")
    def _exactly_one_identifier(self) -> "IntegrationProjectSelector":
        if (self.id is None) == (self.name is None):
            raise ValueError("exactly one of project.id or project.name is required")
        return self


class IntegrationVideoInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    file_name: StrictStr = Field(alias="fileName")
    content_type: StrictStr = Field(alias="contentType")
    size_bytes: int = Field(alias="sizeBytes", ge=1)
    duration_seconds: float | None = Field(default=None, alias="durationSeconds", gt=0)

    @model_validator(mode="after")
    def _media_contract(self) -> "IntegrationVideoInput":
        # Reuse the legacy route's proven extension/MIME/limit policy while
        # allowing the public beta to omit a client-estimated duration. The
        # worker remains authoritative and persists its measured duration.
        legacy = CreateJobRequest.model_validate(
            {
                "name": "validation-project",
                "durationSecs": self.duration_seconds or 1.0,
                "fileName": self.file_name,
                "contentType": self.content_type,
                "fileSizeBytes": self.size_bytes,
            }
        )
        self.file_name = legacy.file_name
        self.content_type = legacy.content_type
        self.size_bytes = legacy.file_size_bytes
        return self


class IntegrationTranscriptInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    file_name: StrictStr = Field(alias="fileName")
    format: Literal["vtt", "srt"]
    content_type: Literal["text/vtt", "application/x-subrip"] = Field(alias="contentType")
    size_bytes: int = Field(alias="sizeBytes", ge=1)

    @model_validator(mode="after")
    def _timed_transcript_contract(self) -> "IntegrationTranscriptInput":
        self.file_name = _safe_basename(
            self.file_name,
            allowed_extensions={f".{self.format}"},
        )
        if self.content_type != _TRANSCRIPT_CONTENT_TYPES[self.format]:
            raise ValueError("transcript format, extension and contentType must agree")
        if self.size_bytes > MAX_TRANSCRIPT_BYTES:
            raise ValueError("transcript size exceeds the upload limit")
        return self


class IntegrationProductSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset: Literal["economy", "standard"]
    style: StrictStr
    detail: int = Field(ge=1, le=5)
    language: StrictStr | None = Field(default=None, max_length=10)
    instructions: StrictStr | None = Field(default=None, max_length=2000)
    voice: StrictStr | None = Field(default=None, max_length=80)

    @field_validator("style")
    @classmethod
    def _style_allowed(cls, value: str) -> str:
        value = _bounded_text(value, field="settings.style", maximum=80)
        if value not in get_settings().preset_style_allowlist:
            raise ValueError("settings.style is not supported")
        return value

    @field_validator("language")
    @classmethod
    def _language_valid(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        if not _LANGUAGE_RE.fullmatch(value):
            raise ValueError("language must be a short BCP-47-style tag")
        return value

    @field_validator("instructions")
    @classmethod
    def _instructions_valid(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if _PROMPT_CONTROL_RE.search(value):
            raise ValueError("instructions contain unsupported control characters")
        return value

    @field_validator("voice")
    @classmethod
    def _voice_valid(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = _bounded_text(value, field="settings.voice", maximum=80)
        if value not in VOICE_ALLOWLIST:
            raise ValueError("settings.voice is not supported")
        return value

    def to_worker_settings(
        self,
        *,
        project_name: str,
        duration_seconds: float | None,
        provided_transcript: bool,
    ) -> dict[str, object]:
        # Keep product terminology at this API boundary and map it to the
        # narrow, server-owned worker knobs.
        mapped = UploadSettingsIn.model_validate(
            {
                "model": "gpt-4.1",
                "frameQuality": "low",
                "fps": 0.5 if self.preset == "economy" else 1.0,
                "chunkSizeSecs": 120 if self.preset == "economy" else 60,
                "audioExtraction": not provided_transcript,
                "customPrompt": self.instructions or "",
                "language": self.language,
                "detailLevel": self.detail,
                "presetStyle": self.style,
            }
        )
        # Until the shared worker contract makes measured duration optional,
        # retain a bounded placeholder when the client omits its estimate. The
        # worker overwrites this with ffprobe's measured value before running.
        return {
            "model": mapped.model,
            "frame_quality": mapped.frame_quality,
            "fps": mapped.fps,
            "chunk_size": mapped.chunk_size_secs,
            "audio_extraction": mapped.audio_extraction,
            "custom_prompt": mapped.custom_prompt,
            "language": mapped.language,
            "detail_level": mapped.detail_level,
            "preset_style": mapped.preset_style,
            "voice": self.voice or "onyx",
            "project_name": project_name,
            "duration_secs": duration_seconds or 1.0,
        }


class IntegrationJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    project: IntegrationProjectSelector
    client_reference: StrictStr | None = Field(default=None, alias="clientReference")
    video: IntegrationVideoInput
    transcript: IntegrationTranscriptInput | None = None
    settings: IntegrationProductSettings

    @field_validator("client_reference")
    @classmethod
    def _client_reference_valid(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _bounded_text(value, field="clientReference", maximum=255)


class IntegrationNestedJobCreate(BaseModel):
    """Compatibility request accepted only by the hidden nested beta route."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, validate_default=True)

    duration_seconds: float = Field(alias="durationSeconds", gt=0)
    file_name: str = Field(alias="fileName", min_length=1, max_length=255)
    content_type: str = Field(alias="contentType")
    size_bytes: int = Field(alias="sizeBytes", ge=1)
    settings: UploadSettingsIn = Field(default_factory=UploadSettingsIn)

    @model_validator(mode="after")
    def _legacy_media_guards(self) -> "IntegrationNestedJobCreate":
        legacy = self.to_legacy("validation-project")
        self.file_name = legacy.file_name
        return self

    def to_legacy(self, project_name: str) -> CreateJobRequest:
        return CreateJobRequest.model_validate(
            {
                "name": project_name,
                "durationSecs": self.duration_seconds,
                "fileName": self.file_name,
                "contentType": self.content_type,
                "fileSizeBytes": self.size_bytes,
                "settings": self.settings.model_dump(by_alias=True),
            }
        )


class IntegrationPresignedUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: Literal["POST"]
    url: str
    fields: dict[str, str]
    expiresAt: datetime


class IntegrationJobSourceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["awaiting_upload", "uploaded"]
    contentType: str | None
    sizeBytes: int | None
    durationSeconds: float | None


class IntegrationJobErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str | None
    message: str | None


class IntegrationJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    object: Literal["job"]
    projectId: uuid.UUID
    clientReference: str | None
    state: Literal[
        "awaiting_upload",
        "queued",
        "processing",
        "needs_review",
        "rendering",
        "completed",
        "failed",
        "cancelled",
    ]
    progress: int = Field(ge=0, le=100)
    stage: str | None
    pipelineRevision: str
    source: IntegrationJobSourceResponse
    reviewUrl: str | None
    error: IntegrationJobErrorResponse | None
    createdAt: datetime
    updatedAt: datetime


class IntegrationUploadsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    video: IntegrationPresignedUpload
    transcript: IntegrationPresignedUpload | None = None


class IntegrationJobCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job: IntegrationJobResponse
    uploads: IntegrationUploadsResponse


class IntegrationProjectResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    object: Literal["project"]
    name: str
    externalId: str | None
    starred: bool
    version: int = Field(ge=1)
    createdAt: datetime
    updatedAt: datetime


class IntegrationOrganizationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    object: Literal["organization"]
    slug: str
    name: str
    active: bool
    createdAt: datetime
    updatedAt: datetime


class IntegrationJobListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object: Literal["list"]
    data: list[IntegrationJobResponse]
    nextCursor: str | None


class IntegrationProjectListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object: Literal["list"]
    data: list[IntegrationProjectResponse]
    nextCursor: str | None


class IntegrationReviewCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["web"]


class IntegrationUploadCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    maxBytes: int = Field(ge=1)
    maxDurationSeconds: int = Field(ge=1)
    contentTypes: list[str]


class IntegrationIdempotencyCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requiredForWrites: Literal[True]
    retentionSeconds: int = Field(ge=1)


class IntegrationTtsPreviewCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rollingWindowSeconds: int = Field(ge=1)
    maxRequestsPerJob: int = Field(ge=1)
    maxRequestsPerOrganization: int = Field(ge=1)
    maxActivePerOrganization: int = Field(ge=1)
    maxAttemptsPerRequest: int = Field(ge=1)


class IntegrationTtsCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    maxApprovedScenesPerReview: int = Field(ge=1)
    maxRenderAttemptsPerReview: int = Field(ge=1)
    maxFinalSynthesisCallsPerReview: int = Field(ge=1)
    previews: IntegrationTtsPreviewCapabilities


class IntegrationCapabilitiesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    brand: Literal["InstaDescribe"]
    apiVersion: Literal["v1-beta"]
    organizationId: uuid.UUID
    resources: list[str]
    jobStates: list[
        Literal[
            "awaiting_upload",
            "queued",
            "processing",
            "needs_review",
            "rendering",
            "completed",
            "failed",
            "cancelled",
        ]
    ]
    review: IntegrationReviewCapabilities
    uploads: IntegrationUploadCapabilities
    idempotency: IntegrationIdempotencyCapabilities
    tts: IntegrationTtsCapabilities
