"""Turn a verified ``VisualMatch`` into one structured ``EvidenceItem``.

This is the interpretation layer between visual perception and the belief
system, and it is deliberately thin::

    VisualMatch (geometry)  ->  EvidenceItem (claim)  ->  update_beliefs (fusion)

Semantics this layer fixes:

- ``verified=True`` is a **positive support event** for the hypothesis the
  matched reference candidate is explicitly bound to. It is not a probability
  and carries no calibrated strength: the contribution is a fixed ``+1.0``
  and the raw geometry (cosine, matches, inliers, ratio, reprojection error)
  rides along as diagnostics that explain *why verification happened*.
- ``verified=False`` produces **no evidence at all** — not negative evidence.
  A failed RANSAC means "positive correspondence was not established", never
  "the hypothesis is false", so no hypothesis may be penalised for it.
- A retrieval hit is never evidence on its own. Without an explicit
  ``VisualCandidateBinding`` a verified match stays a diagnostic result;
  a hypothesis is never inferred from a candidate id, filename or rank.

One verified observation yields exactly one evidence item. The diagnostics are
NOT split into separate cosine/feature/RANSAC contributions, because that would
count a single visual observation several times.

Correlation follows the package's existing convention (see ``belief._group_scores``,
which keeps only the strongest signed contribution per correlation group): the
group identifies the **observation** — one query observation against one
underlying reference capture — not the claim. Transformed variants of one
reference capture therefore collapse into a single contribution instead of
masquerading as independent support.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from math import isfinite

from .models import (
    EvidenceContribution,
    EvidenceItem,
    EvidenceKind,
    JsonValue,
    VerificationState,
    VisualMatch,
)
from .serialization import canonical_json

__all__ = [
    "VisualCandidateBinding",
    "VisualEvidenceConfig",
    "visual_evidence_correlation_group",
    "visual_evidence_id",
    "visual_match_to_evidence",
]

_EVIDENCE_ID_PREFIX = "visual-match"
_ADAPTER_VERSION = "visual-match-evidence-1"
# The persisted correlation_group column is bounded; keep group keys short
# enough to survive storage without silent truncation.
_MAX_CORRELATION_GROUP = 120


def _require_identifier(value: str, field_name: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field_name} must not be empty")


@dataclass(frozen=True, slots=True)
class VisualCandidateBinding:
    """The explicit, typed link from a reference image to a hypothesis.

    ``candidate_id`` is the RETRIEVAL candidate (an image in the reference set).
    ``hypothesis_id`` is the BELIEF candidate it supports — the same identifier
    space as ``CandidatePrior.candidate_id``. The two are separate on purpose:
    a retrieval id is never usable as a hypothesis id.

    ``source_observation_id`` names the underlying capture the image derives
    from. Several candidate records (an original and its crop, scale or
    brightness variants) share one ``source_observation_id`` and therefore one
    correlation group; that is what stops variants of a single reference photo
    from behaving like independent observations.
    """

    candidate_id: str
    hypothesis_id: str
    source_observation_id: str

    def __post_init__(self) -> None:
        for name in ("candidate_id", "hypothesis_id", "source_observation_id"):
            _require_identifier(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class VisualEvidenceConfig:
    """Bounded, inspectable settings for admitting visual matches as evidence.

    ``enabled`` is False by default: the visual pipeline is library-and-benchmark
    only, and a verified match must not silently start moving beliefs. The two
    magnitudes below are NOT calibrated — they exist so the strength of a
    verified geometric event is explicit and adjustable in one place rather than
    inferred from geometry.
    """

    enabled: bool = False
    support_score: float = 1.0
    reliability: float = 1.0

    def __post_init__(self) -> None:
        if not isfinite(self.support_score) or not 0 < self.support_score <= 1:
            raise ValueError("support_score must be finite and within (0, 1]")
        if not isfinite(self.reliability) or not 0 < self.reliability <= 1:
            raise ValueError("reliability must be finite and within (0, 1]")


def visual_evidence_correlation_group(
    query_observation_id: str,
    source_observation_id: str,
) -> str:
    """Correlation key for one query observation against one reference capture.

    The hypothesis is intentionally absent: the group identifies the
    observation, matching how frame-derived evidence is grouped elsewhere in
    the package. ``belief._group_scores`` already separates candidates within a
    group, so one observation supporting two hypotheses must not be counted as
    two independent groups.
    """

    _require_identifier(query_observation_id, "query_observation_id")
    _require_identifier(source_observation_id, "source_observation_id")
    group = f"visual:{query_observation_id}:{source_observation_id}"
    if len(group) <= _MAX_CORRELATION_GROUP:
        return group
    digest = hashlib.sha256(group.encode()).hexdigest()[:32]
    return f"visual:{digest}"


def visual_evidence_id(
    query_observation_id: str,
    binding: VisualCandidateBinding,
    *,
    matcher: str,
) -> str:
    """Deterministic identity for one verified match interpreted as evidence.

    Derived from the semantic inputs only (never a random UUID, never the
    geometry), so replaying the same verified match yields the same id and the
    belief layer cannot double-count it.
    """

    _require_identifier(query_observation_id, "query_observation_id")
    _require_identifier(matcher, "matcher")
    digest = hashlib.sha256(
        canonical_json(
            {
                "adapter": _ADAPTER_VERSION,
                "candidateId": binding.candidate_id,
                "hypothesisId": binding.hypothesis_id,
                "kind": EvidenceKind.VISUAL_MATCH.value,
                "matcher": matcher,
                "queryObservationId": query_observation_id,
                "sourceObservationId": binding.source_observation_id,
            }
        ).encode()
    ).hexdigest()
    return f"{_EVIDENCE_ID_PREFIX}-{digest[:20]}"


def visual_match_to_evidence(
    match: VisualMatch,
    binding: VisualCandidateBinding,
    *,
    query_observation_id: str,
    query_source_id: str,
    matcher: str,
    config: VisualEvidenceConfig | None = None,
    retrieval_rank: int | None = None,
    candidate_source: str | None = None,
    candidate_image_ref: str | None = None,
    embedding_model: str | None = None,
    local_feature_method: str | None = None,
    geometric_model: str | None = None,
    frame_time_ms: int | None = None,
) -> EvidenceItem | None:
    """Interpret one verified match as evidence, or return None.

    Returns ``None`` — never negative evidence — when verification did not
    succeed or when the feature is disabled. Raises when the caller's wiring is
    inconsistent (a binding for a different candidate), because that is a
    programming error rather than an evidence outcome.
    """

    settings = config or VisualEvidenceConfig()
    _require_identifier(query_observation_id, "query_observation_id")
    _require_identifier(query_source_id, "query_source_id")
    _require_identifier(matcher, "matcher")
    if binding.candidate_id != match.candidate_artifact_id:
        raise ValueError(
            f"binding is for candidate {binding.candidate_id!r} but the match verified "
            f"{match.candidate_artifact_id!r}"
        )
    if retrieval_rank is not None and retrieval_rank < 1:
        raise ValueError("retrieval_rank starts at one")
    if not settings.enabled:
        return None
    if not match.verified:
        # Absence of geometric support is not contradictory evidence.
        return None

    attributes: dict[str, JsonValue] = {
        "adapterVersion": _ADAPTER_VERSION,
        "candidateId": binding.candidate_id,
        "candidateSourceObservationId": binding.source_observation_id,
        "embeddingSimilarity": match.embedding_similarity,
        "featureMatchCount": match.feature_matches,
        "hypothesisId": binding.hypothesis_id,
        "matcher": matcher,
        "queryObservationId": query_observation_id,
        "ransacInlierCount": match.ransac_inliers,
        "ransacInlierRatio": match.ransac_inlier_ratio,
        "reprojectionError": match.reprojection_error,
        "visualMatchId": match.match_id,
    }
    for name, value in (
        ("candidateSource", candidate_source),
        ("candidateImageRef", candidate_image_ref),
        ("embeddingModel", embedding_model),
        ("localFeatureMethod", local_feature_method),
        ("geometricModel", geometric_model),
    ):
        if value is not None:
            attributes[name] = value
    if retrieval_rank is not None:
        attributes["retrievalRank"] = retrieval_rank

    return EvidenceItem(
        evidence_id=visual_evidence_id(query_observation_id, binding, matcher=matcher),
        observation=(
            f"Geometric verification matched this observation to reference "
            f"{binding.candidate_id} ({match.ransac_inliers} RANSAC inliers of "
            f"{match.feature_matches} descriptor matches)."
        ),
        source_id=query_source_id,
        artifact_id=match.query_artifact_id,
        correlation_group=visual_evidence_correlation_group(
            query_observation_id, binding.source_observation_id
        ),
        reliability=settings.reliability,
        # One verified observation, one contribution. The diagnostics above are
        # not additional contributions: that would count one observation twice.
        contributions=(
            EvidenceContribution(
                candidate_id=binding.hypothesis_id,
                score=settings.support_score,
            ),
        ),
        kind=EvidenceKind.VISUAL_MATCH,
        verification_state=VerificationState.VERIFIED,
        frame_time_ms=frame_time_ms,
        attributes=attributes,
    )
