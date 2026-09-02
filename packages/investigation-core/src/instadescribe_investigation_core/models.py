"""Stable, dependency-free contracts for observable video investigations."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]


def utc_now() -> datetime:
    """Return an aware UTC timestamp.

    The runner accepts an injectable clock; this function is for callers that do not
    require deterministic time.
    """

    return datetime.now(UTC)


def _require_identifier(value: str, field_name: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


class InvestigationKind(StrEnum):
    GEOLOCATE_PROVENANCE = "geolocateProvenance"
    DAMAGE_CHANGE = "damageChange"


class ConnectivityPolicy(StrEnum):
    LOCAL = "local"
    TEXT_ONLY = "textOnly"
    APPROVED_CROPS = "approvedCrops"
    CONNECTED = "connected"


class InvestigationStatus(StrEnum):
    AWAITING_UPLOAD = "awaiting_upload"
    QUEUED = "queued"
    PREPROCESSING = "preprocessing"
    INVESTIGATING = "investigating"
    NEEDS_REVIEW = "needs_review"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EvidenceKind(StrEnum):
    VISUAL = "visual"
    OCR = "ocr"
    AUDIO = "audio"
    METADATA = "metadata"
    GEO_PRIOR = "geoPrior"
    WEB = "web"
    VISUAL_MATCH = "visualMatch"
    ANALYST = "analyst"


class VerificationState(StrEnum):
    OBSERVED = "observed"
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    REJECTED = "rejected"


class StepStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class EgressDecision(StrEnum):
    NOT_APPLICABLE = "notApplicable"
    BLOCKED = "blocked"
    APPROVED = "approved"


class ActionType(StrEnum):
    INSPECT_MEDIA = "inspectMedia"
    EXTRACT_KEYFRAMES = "extractKeyframes"
    OBSERVE = "observe"
    OCR = "ocr"
    TRANSCRIBE = "transcribe"
    COMPUTE_GEO_PRIOR = "computeGeoPrior"
    SEARCH_TEXT = "searchText"
    SEARCH_CROP = "searchCrop"
    VERIFY_VISUAL_MATCH = "verifyVisualMatch"
    REQUEST_REVIEW = "requestReview"
    STOP = "stop"


class TraceEventType(StrEnum):
    INVESTIGATION_STARTED = "investigation.started"
    MEDIA_INSPECTED = "media.inspected"
    STEP_STARTED = "step.started"
    EVIDENCE_RECORDED = "evidence.recorded"
    BELIEF_UPDATED = "belief.updated"
    STEP_COMPLETED = "step.completed"
    INVESTIGATION_NEEDS_REVIEW = "investigation.needsReview"
    INVESTIGATION_COMPLETED = "investigation.completed"


class AnalystDecisionKind(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    CORRECT = "correct"
    ABSTAIN = "abstain"


@dataclass(frozen=True, slots=True)
class ModelProvenance:
    name: str
    version: str
    digest: str
    runtime: str
    prompt_digest: str | None = None

    def __post_init__(self) -> None:
        for name in ("name", "version", "digest", "runtime"):
            _require_identifier(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class SourceRecord:
    source_id: str
    content_sha256: str
    collected_at: datetime
    license_basis: str
    publisher: str | None = None
    source_url: str | None = None
    published_at: datetime | None = None
    consent_basis: str | None = None
    redistribution_policy: str = "unknown"
    retention_policy: str = "callerManaged"

    def __post_init__(self) -> None:
        _require_identifier(self.source_id, "source_id")
        if len(self.content_sha256) != 64:
            raise ValueError("content_sha256 must be a 64-character hexadecimal digest")
        try:
            int(self.content_sha256, 16)
        except ValueError as error:
            raise ValueError("content_sha256 must be hexadecimal") from error
        _require_aware(self.collected_at, "collected_at")
        if self.published_at is not None:
            _require_aware(self.published_at, "published_at")
        _require_identifier(self.license_basis, "license_basis")


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    artifact_id: str
    content_sha256: str
    media_type: str
    size_bytes: int
    frame_time_ms: int | None = None
    bbox_xywh: tuple[float, float, float, float] | None = None
    perceptual_hash: str | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.artifact_id, "artifact_id")
        if len(self.content_sha256) != 64:
            raise ValueError("content_sha256 must be a 64-character digest")
        try:
            int(self.content_sha256, 16)
        except ValueError as error:
            raise ValueError("content_sha256 must be hexadecimal") from error
        if self.size_bytes < 0:
            raise ValueError("size_bytes must not be negative")
        if self.frame_time_ms is not None and self.frame_time_ms < 0:
            raise ValueError("frame_time_ms must not be negative")
        if self.bbox_xywh is not None:
            x, y, width, height = self.bbox_xywh
            if not all(isfinite(value) for value in self.bbox_xywh):
                raise ValueError("bbox_xywh values must be finite")
            if x < 0 or y < 0 or width <= 0 or height <= 0:
                raise ValueError("bbox_xywh must have non-negative origin and positive size")


@dataclass(frozen=True, slots=True)
class Keyframe:
    keyframe_id: str
    artifact: ArtifactRef
    shot_index: int
    rank: int
    information_score: float
    quality_score: float
    selector_cache_key: str | None = None
    # Semantic-redundancy diagnostics. Both None: the frame carried no embedding.
    # (None, 1.0): embedded, but nothing was selected yet to compare against.
    # (x, 1 - max(0, x)): x is the highest cosine similarity to an earlier keyframe.
    embedding_similarity_max: float | None = None
    semantic_novelty: float | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.keyframe_id, "keyframe_id")
        if self.shot_index < 0 or self.rank < 0:
            raise ValueError("shot_index and rank must not be negative")
        for name in ("information_score", "quality_score"):
            value = getattr(self, name)
            if not isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be finite and between zero and one")
        if self.selector_cache_key is not None:
            _require_identifier(self.selector_cache_key, "selector_cache_key")
        if self.embedding_similarity_max is not None and (
            not isfinite(self.embedding_similarity_max)
            or not -1 <= self.embedding_similarity_max <= 1
        ):
            raise ValueError("embedding_similarity_max must be finite and between -1 and 1")
        if self.semantic_novelty is not None and (
            not isfinite(self.semantic_novelty) or not 0 <= self.semantic_novelty <= 1
        ):
            raise ValueError("semantic_novelty must be finite and between zero and one")
        if self.embedding_similarity_max is not None and self.semantic_novelty is None:
            raise ValueError("semantic_novelty is required when embedding_similarity_max is set")


@dataclass(frozen=True, slots=True)
class VisualMatch:
    match_id: str
    query_artifact_id: str
    candidate_artifact_id: str
    embedding_similarity: float
    feature_matches: int
    ransac_inliers: int
    reprojection_error: float | None
    verified: bool

    def __post_init__(self) -> None:
        for name in ("match_id", "query_artifact_id", "candidate_artifact_id"):
            _require_identifier(getattr(self, name), name)
        if not isfinite(self.embedding_similarity) or not -1 <= self.embedding_similarity <= 1:
            raise ValueError("embedding_similarity must be finite and between -1 and 1")
        if self.feature_matches < 0 or self.ransac_inliers < 0:
            raise ValueError("feature and inlier counts must not be negative")
        if self.ransac_inliers > self.feature_matches:
            raise ValueError("ransac_inliers must not exceed feature_matches")
        if self.reprojection_error is not None and (
            not isfinite(self.reprojection_error) or self.reprojection_error < 0
        ):
            raise ValueError("reprojection_error must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class EventCluster:
    cluster_id: str
    source_ids: tuple[str, ...]
    exact_hashes: tuple[str, ...] = ()
    perceptual_hashes: tuple[str, ...] = ()
    earliest_source_id: str | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.cluster_id, "cluster_id")
        if len(self.source_ids) != len(set(self.source_ids)):
            raise ValueError("source_ids must be unique")
        if self.earliest_source_id is not None and self.earliest_source_id not in self.source_ids:
            raise ValueError("earliest_source_id must belong to source_ids")


@dataclass(frozen=True, slots=True)
class CandidatePrior:
    candidate_id: str
    label: str
    prior: float
    latitude: float | None = None
    longitude: float | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.candidate_id, "candidate_id")
        _require_identifier(self.label, "label")
        if not isfinite(self.prior) or self.prior <= 0:
            raise ValueError("prior must be finite and greater than zero")
        if self.latitude is not None and not -90 <= self.latitude <= 90:
            raise ValueError("latitude must be between -90 and 90")
        if self.longitude is not None and not -180 <= self.longitude <= 180:
            raise ValueError("longitude must be between -180 and 180")


@dataclass(frozen=True, slots=True)
class EvidenceContribution:
    candidate_id: str
    score: float

    def __post_init__(self) -> None:
        _require_identifier(self.candidate_id, "candidate_id")
        if not isfinite(self.score) or not -1 <= self.score <= 1:
            raise ValueError("score must be finite and between -1 and 1")


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    evidence_id: str
    observation: str
    source_id: str
    artifact_id: str
    correlation_group: str
    reliability: float
    contributions: tuple[EvidenceContribution, ...]
    kind: EvidenceKind = EvidenceKind.VISUAL
    verification_state: VerificationState = VerificationState.OBSERVED
    created_at: datetime = field(default_factory=utc_now)
    frame_time_ms: int | None = None
    bbox_xywh: tuple[float, float, float, float] | None = None
    attributes: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "attributes", deepcopy(self.attributes))
        for name in ("evidence_id", "observation", "source_id", "artifact_id", "correlation_group"):
            _require_identifier(getattr(self, name), name)
        if not isfinite(self.reliability) or not 0 <= self.reliability <= 1:
            raise ValueError("reliability must be finite and between 0 and 1")
        if not self.contributions:
            raise ValueError("contributions must not be empty")
        candidate_ids = [item.candidate_id for item in self.contributions]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("an evidence item may contribute to a candidate only once")
        _require_aware(self.created_at, "created_at")
        if self.frame_time_ms is not None and self.frame_time_ms < 0:
            raise ValueError("frame_time_ms must not be negative")
        if self.bbox_xywh is not None:
            x, y, width, height = self.bbox_xywh
            if not all(isfinite(value) for value in self.bbox_xywh):
                raise ValueError("bbox_xywh values must be finite")
            if x < 0 or y < 0 or width <= 0 or height <= 0:
                raise ValueError("bbox_xywh must have non-negative origin and positive size")


@dataclass(frozen=True, slots=True)
class EvidenceBatch:
    evidence: tuple[EvidenceItem, ...]
    model: ModelProvenance | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BeliefCandidate:
    candidate_id: str
    label: str
    log_score: float
    probability: float
    group_scores: dict[str, float]

    def __post_init__(self) -> None:
        object.__setattr__(self, "group_scores", deepcopy(self.group_scores))
        if not isfinite(self.log_score):
            raise ValueError("log_score must be finite")
        if not isfinite(self.probability) or not 0 <= self.probability <= 1:
            raise ValueError("probability must be finite and between 0 and 1")
        if any(not isfinite(value) for value in self.group_scores.values()):
            raise ValueError("group_scores values must be finite")


@dataclass(frozen=True, slots=True)
class BeliefSnapshot:
    snapshot_id: str
    created_at: datetime
    candidates: tuple[BeliefCandidate, ...]
    entropy: float
    normalized_entropy: float
    abstained: bool
    abstention_reasons: tuple[str, ...]
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_identifier(self.snapshot_id, "snapshot_id")
        _require_aware(self.created_at, "created_at")
        if self.entropy < 0 or not isfinite(self.entropy):
            raise ValueError("entropy must be finite and non-negative")
        if not 0 <= self.normalized_entropy <= 1:
            raise ValueError("normalized_entropy must be between 0 and 1")
        if not self.candidates:
            raise ValueError("candidates must not be empty")
        total = sum(candidate.probability for candidate in self.candidates)
        if abs(total - 1.0) > 1e-9:
            raise ValueError("candidate probabilities must sum to one")


@dataclass(frozen=True, slots=True)
class ActionCandidate:
    action: ActionType
    expected_entropy_reduction: float
    expected_latency_seconds: float = 0
    expected_cost_units: float = 0
    privacy_risk: float = 0
    parameters: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", deepcopy(self.parameters))
        for name in (
            "expected_entropy_reduction",
            "expected_latency_seconds",
            "expected_cost_units",
            "privacy_risk",
        ):
            value = getattr(self, name)
            if not isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.privacy_risk > 1:
            raise ValueError("privacy_risk must not exceed one")


@dataclass(frozen=True, slots=True)
class ActionDecision:
    action: ActionType
    utility: float
    expected_entropy_reduction: float
    parameters: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", deepcopy(self.parameters))
        if not isfinite(self.utility):
            raise ValueError("utility must be finite")
        if not isfinite(self.expected_entropy_reduction) or self.expected_entropy_reduction < 0:
            raise ValueError("expected_entropy_reduction must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class InvestigationStep:
    step_id: str
    action: ActionType
    status: StepStatus
    started_at: datetime
    completed_at: datetime | None = None
    input_evidence_ids: tuple[str, ...] = ()
    output_evidence_ids: tuple[str, ...] = ()
    model_digest: str | None = None
    prompt_digest: str | None = None
    tool_version: str | None = None
    latency_ms: int | None = None
    peak_memory_bytes: int | None = None
    cost_units: float = 0
    egress_decision: EgressDecision = EgressDecision.NOT_APPLICABLE
    entropy_before: float | None = None
    entropy_after: float | None = None
    error: str | None = None
    attributes: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "attributes", deepcopy(self.attributes))
        _require_identifier(self.step_id, "step_id")
        _require_aware(self.started_at, "started_at")
        if self.completed_at is not None:
            _require_aware(self.completed_at, "completed_at")
            if self.completed_at < self.started_at:
                raise ValueError("completed_at must not be before started_at")
        for name in ("latency_ms", "peak_memory_bytes"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} must not be negative")
        if self.cost_units < 0 or not isfinite(self.cost_units):
            raise ValueError("cost_units must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class Investigation:
    investigation_id: str
    kind: InvestigationKind
    connectivity_policy: ConnectivityPolicy
    status: InvestigationStatus
    source_id: str
    trace_id: str
    created_at: datetime
    updated_at: datetime
    model_provenance: tuple[ModelProvenance, ...] = ()
    final_hypothesis_id: str | None = None
    confidence: float | None = None
    abstained: bool = False

    def __post_init__(self) -> None:
        for name in ("investigation_id", "source_id", "trace_id"):
            _require_identifier(getattr(self, name), name)
        _require_aware(self.created_at, "created_at")
        _require_aware(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not be before created_at")
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class AnalystDecision:
    decision_id: str
    investigation_id: str
    kind: AnalystDecisionKind
    decided_at: datetime
    evidence_ids: tuple[str, ...] = ()
    corrected_hypothesis_id: str | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.decision_id, "decision_id")
        _require_identifier(self.investigation_id, "investigation_id")
        _require_aware(self.decided_at, "decided_at")


@dataclass(frozen=True, slots=True)
class TraceEvent:
    trace_id: str
    sequence: int
    event_type: TraceEventType
    occurred_at: datetime
    payload: dict[str, JsonValue]

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", deepcopy(self.payload))
        _require_identifier(self.trace_id, "trace_id")
        if self.sequence < 0:
            raise ValueError("sequence must not be negative")
        _require_aware(self.occurred_at, "occurred_at")
