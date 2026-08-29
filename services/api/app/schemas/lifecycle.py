"""Closed wire schemas for integration lifecycle read endpoints."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class IntegrationReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    object: Literal["review"]
    jobId: uuid.UUID
    state: Literal["open", "completed", "expired"]
    version: int = Field(ge=1)
    locked: bool
    sceneCount: int | None = Field(default=None, ge=0)
    decidedSceneCount: int | None = Field(default=None, ge=0)
    approvedSceneCount: int | None = Field(default=None, ge=0)
    rejectedSceneCount: int | None = Field(default=None, ge=0)
    zeroAdConfirmed: bool
    lockedAt: datetime | None
    completedAt: datetime | None
    expiresAt: datetime
    createdAt: datetime
    updatedAt: datetime


class IntegrationRenderError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str


class IntegrationRenderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    object: Literal["render"]
    jobId: uuid.UUID
    reviewId: uuid.UUID
    state: Literal["queued", "rendering", "completed", "failed", "cancelled"]
    attemptCount: int = Field(ge=0)
    error: IntegrationRenderError | None
    createdAt: datetime
    updatedAt: datetime
    startedAt: datetime | None
    completedAt: datetime | None


class IntegrationDeliverableResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    jobId: uuid.UUID
    kind: Literal["mp4", "mp3", "srt", "csv", "docx"]
    fileName: str
    contentType: str
    byteSize: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    createdAt: datetime


class IntegrationDeliverablesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[IntegrationDeliverableResponse]
    completedSet: Literal[True]
