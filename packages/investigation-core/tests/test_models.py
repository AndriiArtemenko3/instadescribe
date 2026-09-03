from __future__ import annotations

from datetime import datetime

import pytest
from conftest import FIXED_TIME

from instadescribe_investigation_core import (
    ActionType,
    ArtifactRef,
    CandidatePrior,
    EvidenceContribution,
    EvidenceItem,
    InvestigationStep,
    Keyframe,
    SourceRecord,
    StepStatus,
)


def test_source_requires_aware_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        SourceRecord(
            source_id="source-1",
            content_sha256="a" * 64,
            collected_at=datetime(2026, 1, 1),
            license_basis="CC-BY-4.0",
        )


def test_evidence_rejects_duplicate_candidate_contributions() -> None:
    with pytest.raises(ValueError, match="only once"):
        EvidenceItem(
            evidence_id="evidence-1",
            observation="A sign",
            source_id="source-1",
            artifact_id="frame-1",
            correlation_group="sign-1",
            reliability=0.8,
            contributions=(
                EvidenceContribution("pl", 0.5),
                EvidenceContribution("pl", 0.7),
            ),
        )


def test_candidate_and_artifact_ranges_are_validated() -> None:
    with pytest.raises(ValueError, match="latitude"):
        CandidatePrior("place", "Place", 1, latitude=91)
    with pytest.raises(ValueError, match="positive size"):
        ArtifactRef("frame", "a" * 64, "image/jpeg", 10, bbox_xywh=(0, 0, 0, 1))


def test_audit_extension_dictionaries_are_defensively_copied() -> None:
    nested_values = ["original"]
    attributes = {"extension": {"values": nested_values}}
    evidence = EvidenceItem(
        evidence_id="evidence-1",
        observation="A sign",
        source_id="source-1",
        artifact_id="frame-1",
        correlation_group="sign-1",
        reliability=0.8,
        contributions=(EvidenceContribution("pl", 0.5),),
        created_at=FIXED_TIME,
        attributes=attributes,
    )
    step = InvestigationStep(
        step_id="step-1",
        action=ActionType.OBSERVE,
        status=StepStatus.SUCCEEDED,
        started_at=FIXED_TIME,
        attributes=attributes,
    )

    nested_values.append("mutated")
    attributes["extension"] = {"values": ["replaced"]}

    expected = {"extension": {"values": ["original"]}}
    assert evidence.attributes == expected
    assert step.attributes == expected


def test_keyframe_semantic_diagnostics_are_validated() -> None:
    artifact = ArtifactRef("frame-1", "a" * 64, "image/jpeg", 10)
    keyframe = Keyframe(
        "keyframe-1",
        artifact,
        0,
        0,
        0.5,
        0.5,
        embedding_similarity_max=-0.2,
        semantic_novelty=1.0,
    )

    assert keyframe.embedding_similarity_max == -0.2
    with pytest.raises(ValueError, match="embedding_similarity_max"):
        Keyframe("k", artifact, 0, 0, 0.5, 0.5, embedding_similarity_max=1.5, semantic_novelty=0)
    with pytest.raises(ValueError, match="semantic_novelty must"):
        Keyframe("k", artifact, 0, 0, 0.5, 0.5, semantic_novelty=-0.1)
    with pytest.raises(ValueError, match="semantic_novelty is required"):
        Keyframe("k", artifact, 0, 0, 0.5, 0.5, embedding_similarity_max=0.4)
