"""Manifest response contract (G6 Gate 2).

Every non-null reference has exactly url/contentType/sizeBytes/
checksumSha256. Required references are never null; optional posters are an
explicit null when absent. The video URL's query may carry the opaque signed
version parameter — no separate raw VersionId field exists on the wire.
"""

from pydantic import BaseModel, ConfigDict, Field


class ArtifactRef(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    url: str
    content_type: str = Field(alias="contentType")
    size_bytes: int = Field(alias="sizeBytes")
    checksum_sha256: str = Field(alias="checksumSha256")


class ManifestArtifacts(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    video: ArtifactRef
    scenes: ArtifactRef
    entities: ArtifactRef
    audio_events: ArtifactRef = Field(alias="audioEvents")
    placement_gaps: ArtifactRef = Field(alias="placementGaps")
    transcript: ArtifactRef
    system_info: ArtifactRef | None = Field(alias="systemInfo")
    poster_jpg: ArtifactRef | None = Field(alias="posterJpg")
    poster_avif: ArtifactRef | None = Field(alias="posterAvif")


class ManifestResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    project_id: str = Field(alias="projectId")
    job_id: str = Field(alias="jobId")
    pipeline_revision: str = Field(alias="pipelineRevision")
    expires_at: str = Field(alias="expiresAt")  # UTC RFC3339 ending in Z
    artifacts: ManifestArtifacts
