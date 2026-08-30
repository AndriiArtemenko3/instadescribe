"""Strict wire models consumed by the Next server-side BFF."""

import re
from decimal import Decimal
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    StrictBool,
    StrictStr,
    field_validator,
)

_INVITATION_EMAIL_RE = re.compile(
    r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
)


class BrowserSessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    subject: str
    email: str
    display_name: str = Field(alias="displayName")
    organization_id: str = Field(alias="organizationId")
    role: Literal["owner", "editor", "reviewer", "viewer"]
    mfa_verified: bool = Field(alias="mfaVerified")


class BrowserProjectSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str
    org_slug: str = Field(alias="orgSlug")
    current_job_id: str | None = Field(alias="currentJobId")
    name: str
    status: Literal["confirmation_pending", "processing", "ready", "draft", "failed"]
    updated_at: str = Field(alias="updatedAt")


class BrowserProjectListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: list[BrowserProjectSummary]


class BrowserInvitationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    role: Literal["editor", "reviewer", "viewer"]

    @field_validator("email")
    @classmethod
    def canonical_email(cls, value: str) -> str:
        canonical = value.strip().casefold()
        if (
            not 3 <= len(canonical) <= 254
            or not canonical.isascii()
            or _INVITATION_EMAIL_RE.fullmatch(canonical) is None
            or ".." in canonical
        ):
            raise ValueError("email must be a canonical deliverable address")
        local_part, domain = canonical.rsplit("@", 1)
        if (
            len(local_part) > 64
            or len(domain) > 253
            or local_part.startswith(".")
            or local_part.endswith(".")
        ):
            raise ValueError("email must be a canonical deliverable address")
        return canonical


class BrowserInvitationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    invitation_id: str = Field(alias="invitationId")
    email: str
    role: Literal["editor", "reviewer", "viewer"]
    state: Literal["active"]


class BrowserFinishReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    zero_ad_confirmed: StrictBool = Field(alias="zeroAdConfirmed")


class BrowserFinishReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jobId: str
    reviewId: str
    renderId: str
    reviewState: Literal["completed"]
    renderState: Literal["queued"]
    idempotent: bool


class BrowserSceneOverride(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    active: bool
    locked: bool
    version: int
    review_status: Literal["generated", "edited", "approved", "rejected"] = Field(
        alias="reviewStatus"
    )
    reviewed_at: str | None = Field(alias="reviewedAt")
    updated_at: str = Field(alias="updatedAt")
    ad: str | None = None
    voice: str | None = None
    speed: float | None = None


class BrowserScenePatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    project_id: str = Field(alias="projectId")
    job_id: str = Field(alias="jobId")
    scene_id: str = Field(alias="sceneId")
    version: int
    review_status: Literal["edited", "approved", "rejected"] = Field(alias="reviewStatus")
    reviewed_at: str | None = Field(alias="reviewedAt")
    updated_at: str = Field(alias="updatedAt")
    override: BrowserSceneOverride


class BrowserTtsPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: StrictStr = Field(min_length=1, max_length=2000)
    voice: Literal["onyx", "nova", "alloy", "shimmer", "echo", "fable"]
    speed: Decimal = Field(ge=Decimal("0.50"), le=Decimal("2.50"), max_digits=3, decimal_places=2)

    @field_validator("text")
    @classmethod
    def bounded_spoken_text(cls, value: str) -> str:
        value = value.strip()
        if not value or any(
            ord(character) <= 0x08
            or ord(character) in {0x0B, 0x0C}
            or 0x0E <= ord(character) <= 0x1F
            or ord(character) == 0x7F
            for character in value
        ):
            raise ValueError("text contains unsupported control characters")
        return value

    @field_validator("speed")
    @classmethod
    def precise_speed(cls, value: Decimal) -> Decimal:
        if value.as_tuple().exponent < -2:
            raise ValueError("speed supports at most two decimal places")
        return value.quantize(Decimal("0.01"))


class BrowserTtsPreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    preview_id: str = Field(alias="previewId")
    job_id: str = Field(alias="jobId")
    scene_id: str = Field(alias="sceneId")
    state: Literal["queued", "rendering", "completed", "failed", "cancelled"]
    content_ready: bool = Field(alias="contentReady")
    error_code: str | None = Field(alias="errorCode")
    created_at: str = Field(alias="createdAt")
    updated_at: str = Field(alias="updatedAt")
    expires_at: str = Field(alias="expiresAt")


class BrowserOverridesResponse(RootModel[dict[str, BrowserSceneOverride]]):
    pass
