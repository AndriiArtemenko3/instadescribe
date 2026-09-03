from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from instadescribe_investigation_core import (
    EvidenceContribution,
    EvidenceItem,
    VerificationState,
)

FIXED_TIME = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)


def evidence_item(
    evidence_id: str,
    *,
    group: str,
    candidate_id: str = "pl",
    score: float = 0.8,
    reliability: float = 1,
    state: VerificationState = VerificationState.OBSERVED,
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        observation=f"Observation {evidence_id}",
        source_id="source-1",
        artifact_id="frame-1",
        correlation_group=group,
        reliability=reliability,
        contributions=(EvidenceContribution(candidate_id, score),),
        verification_state=state,
        created_at=FIXED_TIME,
    )


# Deterministic fixture embeddings. They are not real model outputs; they only need
# to exercise cosine geometry predictably: A and its variant point almost the same
# way, the unrelated scene is close to orthogonal, and the opposite scene is A negated.
LANDMARK_A = (0.82, 0.11, 0.43, 0.05)
LANDMARK_A_VARIANT = (0.80, 0.13, 0.45, 0.07)
UNRELATED_SCENE = (-0.10, 0.78, 0.04, 0.60)
OPPOSITE_SCENE = (-0.82, -0.11, -0.43, -0.05)


class FakeFrameEmbeddingProvider:
    """Network-free FrameEmbeddingProvider keyed by frame file name."""

    def __init__(self, embeddings: dict[str, tuple[float, ...]]) -> None:
        self._embeddings = embeddings

    @property
    def provenance(self) -> None:
        return None

    @property
    def network_access(self) -> bool:
        return False

    def embed_frame(self, frame_path: Path) -> tuple[float, ...]:
        return self._embeddings[frame_path.name]
