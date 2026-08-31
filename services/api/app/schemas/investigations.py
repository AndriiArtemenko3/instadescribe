"""Strict Browser API contracts for video investigations."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from instadescribe_contracts.provider import (
    INVESTIGATION_MVP_MAX_DURATION_SECS,
    INVESTIGATION_MVP_MIN_DURATION_SECS,
)
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    field_validator,
    model_validator,
)

from app.schemas.integrations import IntegrationPresignedUpload, IntegrationVideoInput

InvestigationKind = Literal["geolocateProvenance", "damageChange"]
ConnectivityPolicy = Literal["local", "textOnly", "approvedCrops", "connected"]
InvestigationStatus = Literal[
    "awaitingUpload",
    "queued",
    "preprocessing",
    "investigating",
    "needsReview",
    "completed",
    "failed",
    "cancelled",
]

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


def _trimmed(value: str, *, field: str, maximum: int) -> str:
    value = value.strip()
    if not value or len(value) > maximum or _CONTROL_RE.search(value):
        raise ValueError(f"{field} must be 1-{maximum} safe characters")
    return value


class InvestigationSourceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    publisher_url: StrictStr | None = Field(default=None, alias="publisherUrl", max_length=2048)
    published_at: datetime | None = Field(default=None, alias="publishedAt")
    legal_basis: Literal["publicDomain", "licensed", "consent", "analystAuthorized"] = Field(
        alias="legalBasis"
    )
    license_name: StrictStr | None = Field(default=None, alias="license", max_length=200)
    redistribution_policy: Literal["prohibited", "metadataOnly", "permitted"] = Field(
        alias="redistributionPolicy"
    )
    retention_days: int = Field(
        default=30,
        alias="retentionDays",
        ge=1,
        le=30,
        description=("S3 lifecycle eligibility tier, not a guaranteed physical-deletion deadline"),
    )

    @field_validator("publisher_url")
    @classmethod
    def _https_publisher(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or any(character.isspace() for character in value)
        ):
            raise ValueError("publisherUrl must be an absolute HTTPS URL without credentials")
        return value

    @field_validator("published_at")
    @classmethod
    def _published_at_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("publishedAt must include an RFC 3339 timezone")
        return value

    @field_validator("license_name")
    @classmethod
    def _license_safe(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _trimmed(value, field="license", maximum=200)

    @model_validator(mode="after")
    def _licensed_source_names_license(self) -> InvestigationSourceInput:
        if self.legal_basis == "licensed" and self.license_name is None:
            raise ValueError("license is required when legalBasis is licensed")
        return self


class InvestigationVideoInput(IntegrationVideoInput):
    """Month-one investigation media bound, measured again by the worker."""

    duration_seconds: float = Field(
        alias="durationSeconds",
        ge=INVESTIGATION_MVP_MIN_DURATION_SECS,
        le=INVESTIGATION_MVP_MAX_DURATION_SECS,
    )


class InvestigationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: StrictStr
    kind: InvestigationKind
    connectivity_policy: ConnectivityPolicy = Field(alias="connectivityPolicy")
    video: InvestigationVideoInput
    source: InvestigationSourceInput

    @field_validator("name")
    @classmethod
    def _name_safe(cls, value: str) -> str:
        return _trimmed(value, field="name", maximum=200)


class InvestigationModelProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    model_id: str | None = Field(default=None, alias="modelId", max_length=200)
    model_digest: str | None = Field(default=None, alias="modelDigest")
    prompt_digest: str | None = Field(default=None, alias="promptDigest")
    executed_locally: bool = Field(alias="executedLocally")

    @field_validator("model_digest", "prompt_digest")
    @classmethod
    def _digest_valid(cls, value: str | None) -> str | None:
        if value is not None and _SHA256_RE.fullmatch(value) is None:
            raise ValueError("digest must be lowercase SHA-256")
        return value


class InvestigationRuntimeProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    runtime: str | None = Field(default=None, max_length=120)
    runtime_version: str | None = Field(default=None, alias="runtimeVersion", max_length=120)
    platform: str | None = Field(default=None, max_length=120)


class InvestigationHypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: StrictStr = Field(min_length=1, max_length=120)
    label: StrictStr = Field(min_length=1, max_length=200)
    country_code: str | None = Field(default=None, alias="countryCode", pattern=r"^[A-Z]{2}$")
    region: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=200)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    summary: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _coordinates_are_a_pair(self) -> InvestigationHypothesis:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be supplied together")
        return self


class InvestigationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    investigation_id: uuid.UUID = Field(alias="investigationId")
    project_id: uuid.UUID = Field(alias="projectId")
    job_id: uuid.UUID = Field(alias="jobId")
    name: str
    kind: InvestigationKind
    connectivity_policy: ConnectivityPolicy = Field(alias="connectivityPolicy")
    status: InvestigationStatus
    abstained: bool
    calibrated_confidence: float | None = Field(alias="calibratedConfidence")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class InvestigationDetail(InvestigationSummary):
    trace_id: uuid.UUID | None = Field(alias="traceId")
    model_provenance: InvestigationModelProvenance = Field(alias="modelProvenance")
    runtime_provenance: InvestigationRuntimeProvenance = Field(alias="runtimeProvenance")
    final_hypothesis: InvestigationHypothesis | None = Field(alias="finalHypothesis")
    abstention_reason: str | None = Field(alias="abstentionReason")
    completed_at: datetime | None = Field(alias="completedAt")


class InvestigationListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: list[InvestigationSummary]


class InvestigationCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    investigation: InvestigationDetail
    upload: IntegrationPresignedUpload


class InvestigationSourceResponse(BaseModel):
    """Immutable source-lineage projection for the analyst report."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    source_record_id: uuid.UUID = Field(alias="sourceRecordId")
    publisher_url: str | None = Field(default=None, alias="publisherUrl")
    published_at: datetime | None = Field(default=None, alias="publishedAt")
    collected_at: datetime = Field(alias="collectedAt")
    legal_basis: Literal["publicDomain", "licensed", "consent", "analystAuthorized"] = Field(
        alias="legalBasis"
    )
    license_name: str | None = Field(default=None, alias="license", max_length=200)
    media_sha256: str | None = Field(default=None, alias="mediaSha256")
    redistribution_policy: Literal["prohibited", "metadataOnly", "permitted"] = Field(
        alias="redistributionPolicy"
    )
    retention_days: int = Field(
        alias="retentionDays",
        ge=1,
        le=30,
        description=("S3 lifecycle eligibility tier, not a guaranteed physical-deletion deadline"),
    )
    purge_after: datetime = Field(
        alias="purgeAfter",
        description="Exact-version janitor target for the pinned source asset",
    )

    @field_validator("media_sha256")
    @classmethod
    def _source_digest_valid(cls, value: str | None) -> str | None:
        if value is not None and _SHA256_RE.fullmatch(value) is None:
            raise ValueError("mediaSha256 must be lowercase SHA-256")
        return value


class BoundingBox(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def _inside_frame(self) -> BoundingBox:
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("bounding box must remain inside the normalized frame")
        return self


class EvidenceObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: StrictStr = Field(min_length=1, max_length=1000)
    details: dict[str, Any] | None = None


class EvidenceItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    evidence_id: uuid.UUID = Field(alias="evidenceId")
    kind: Literal["keyframe", "visual", "ocr", "audio", "metadata", "web", "geospatial", "change"]
    observation: EvidenceObservation
    frame_time_ms: int | None = Field(alias="frameTimeMs", ge=0)
    bbox: BoundingBox | None
    polarity: Literal["supports", "contradicts", "neutral"]
    reliability: float = Field(ge=0, le=1)
    verification_state: Literal["proposed", "verified", "rejected"] = Field(
        alias="verificationState"
    )
    correlation_group: str = Field(alias="correlationGroup", min_length=1, max_length=120)
    created_at: datetime = Field(alias="createdAt")


class EvidenceListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: list[EvidenceItemResponse]


class KeyframeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    evidence_id: uuid.UUID = Field(alias="evidenceId")
    frame_time_ms: int = Field(alias="frameTimeMs", ge=0)
    observation: EvidenceObservation
    bbox: BoundingBox | None
    created_at: datetime = Field(alias="createdAt")


class KeyframeListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: list[KeyframeResponse]


class PolicyDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    decision: Literal["pending", "approved", "rejected", "notRequired"]
    decided_by_principal_id: uuid.UUID | None = Field(alias="decidedByPrincipalId")
    decided_at: datetime | None = Field(alias="decidedAt")


class InvestigationStepResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    step_id: uuid.UUID = Field(alias="stepId")
    sequence: int = Field(ge=1)
    kind: str = Field(min_length=1, max_length=40)
    tool: str = Field(min_length=1, max_length=120)
    state: Literal["pending", "running", "completed", "failed", "approved", "rejected"]
    input_evidence_ids: list[uuid.UUID] = Field(alias="inputEvidenceIds")
    output_evidence_ids: list[uuid.UUID] = Field(alias="outputEvidenceIds")
    model_digest: str | None = Field(alias="modelDigest")
    prompt_digest: str | None = Field(alias="promptDigest")
    latency_ms: int | None = Field(alias="latencyMs", ge=0)
    peak_memory_mb: int | None = Field(alias="peakMemoryMb", ge=0)
    cost_microunits: int | None = Field(alias="costMicrounits", ge=0)
    policy_decision: PolicyDecisionResponse = Field(alias="policyDecision")
    entropy_before: float | None = Field(alias="entropyBefore", ge=0)
    entropy_after: float | None = Field(alias="entropyAfter", ge=0)
    started_at: datetime | None = Field(alias="startedAt")
    completed_at: datetime | None = Field(alias="completedAt")


class InvestigationStepListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: list[InvestigationStepResponse]


class BeliefCandidate(InvestigationHypothesis):
    probability: float = Field(ge=0, le=1)


class BeliefSnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    belief_snapshot_id: uuid.UUID = Field(alias="beliefSnapshotId")
    sequence: int = Field(ge=1)
    candidates: list[BeliefCandidate]
    entropy: float = Field(ge=0)
    abstained: bool
    created_at: datetime = Field(alias="createdAt")

    @model_validator(mode="after")
    def _probabilities_normalized(self) -> BeliefSnapshotResponse:
        total = sum(candidate.probability for candidate in self.candidates)
        if self.candidates and abs(total - 1.0) > 1e-5:
            raise ValueError("candidate probabilities must sum to one")
        return self


class BeliefSnapshotListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: list[BeliefSnapshotResponse]


class EvidenceDecisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    evidence_id: uuid.UUID = Field(alias="evidenceId")
    decision: Literal["accepted", "rejected"]


class AnalystDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    evidence_decisions: list[EvidenceDecisionInput] = Field(alias="evidenceDecisions")
    final_hypothesis: InvestigationHypothesis | None = Field(default=None, alias="finalHypothesis")
    abstain: StrictBool
    abstention_reason: StrictStr | None = Field(
        default=None, alias="abstentionReason", max_length=500
    )
    notes: StrictStr | None = Field(default=None, max_length=2000)

    @field_validator("abstention_reason", "notes")
    @classmethod
    def _optional_text_safe(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        return _trimmed(
            value,
            field=info.field_name,
            maximum=500 if info.field_name == "abstention_reason" else 2000,
        )

    @model_validator(mode="after")
    def _outcome_and_decisions_consistent(self) -> AnalystDecisionRequest:
        ids = [item.evidence_id for item in self.evidence_decisions]
        if len(ids) != len(set(ids)):
            raise ValueError("evidenceDecisions must contain each evidenceId at most once")
        if self.abstain:
            if self.final_hypothesis is not None or self.abstention_reason is None:
                raise ValueError("an abstention requires a reason and no finalHypothesis")
        elif self.final_hypothesis is None or self.abstention_reason is not None:
            raise ValueError("a non-abstaining decision requires finalHypothesis and no reason")
        return self


class AnalystDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    decision_id: uuid.UUID = Field(alias="decisionId")
    status: Literal["final"]
    evidence_decisions: list[EvidenceDecisionInput] = Field(alias="evidenceDecisions")
    final_hypothesis: InvestigationHypothesis | None = Field(alias="finalHypothesis")
    abstained: bool
    abstention_reason: str | None = Field(alias="abstentionReason")
    notes: str | None
    decided_by_principal_id: uuid.UUID = Field(alias="decidedByPrincipalId")
    created_at: datetime = Field(alias="createdAt")


class AnalystDecisionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    investigation: InvestigationDetail
    decision: AnalystDecisionResponse


class InvestigationReportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    investigation: InvestigationDetail
    source: InvestigationSourceResponse
    decision: AnalystDecisionResponse | None
    evidence: list[EvidenceItemResponse]
    latest_belief: BeliefSnapshotResponse | None = Field(alias="latestBelief")
