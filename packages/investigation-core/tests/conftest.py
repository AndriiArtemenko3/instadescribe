from __future__ import annotations

from datetime import UTC, datetime

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
