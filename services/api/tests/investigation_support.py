"""Test-only deterministic investigation result persistence."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from app.core.tenancy import PrincipalContext
from app.domain.states import JobState
from app.models import BeliefSnapshot, EvidenceItem, InvestigationStep
from app.repositories.investigations import InvestigationRow
from app.repositories.jobs import transition_job
from sqlalchemy.orm import Session


class InvestigationNotFound(Exception):
    pass


class InvestigationConflict(Exception):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


def seed_deterministic_result(
    session: Session,
    principal: PrincipalContext,
    row: InvestigationRow,
) -> None:
    """Seed a stable, network-free result after a fake worker claimed the job.

    This helper is intentionally not an HTTP endpoint.  Tests and the fake
    worker may call it only from PROCESSING; production runtimes persist their
    own validated open-core output through the same models.
    """
    investigation = row.investigation
    if investigation.organization_id != principal.organization_id:
        raise InvestigationNotFound
    existing = session.execute(
        sa.select(sa.func.count(EvidenceItem.id)).where(
            EvidenceItem.organization_id == principal.organization_id,
            EvidenceItem.investigation_id == investigation.id,
        )
    ).scalar_one()
    if existing:
        if investigation.status == "needs_review":
            return
        raise InvestigationConflict("seed_conflict", "Investigation evidence already exists.")
    if JobState(row.job.status) != JobState.PROCESSING:
        raise InvestigationConflict(
            "seed_conflict", "The fake result can only be seeded for a processing job."
        )

    def stable(label: str) -> uuid.UUID:
        return uuid.uuid5(uuid.NAMESPACE_URL, f"instadescribe:{investigation.id}:{label}")

    first = EvidenceItem(
        id=stable("evidence:keyframe"),
        organization_id=principal.organization_id,
        job_id=row.job.id,
        investigation_id=investigation.id,
        kind="keyframe",
        observation={"summary": "A high-information frame selected from the source video."},
        frame_time_ms=4_000,
        bbox=None,
        polarity="neutral",
        reliability=0.95,
        verification_state="proposed",
        correlation_group="frame-4000",
    )
    second = EvidenceItem(
        id=stable("evidence:ocr"),
        organization_id=principal.organization_id,
        job_id=row.job.id,
        investigation_id=investigation.id,
        kind="ocr",
        observation={
            "summary": "A Latin-script place clue is visible.",
            "details": {
                "text": "EXAMPLE",
                "contributions": [{"candidate_id": "candidate-a", "score": 0.9}],
            },
        },
        frame_time_ms=4_000,
        bbox={"x": 0.12, "y": 0.22, "width": 0.35, "height": 0.12},
        polarity="supports",
        reliability=0.72,
        verification_state="proposed",
        correlation_group="frame-4000-sign",
    )
    session.add_all((first, second))
    step_one = InvestigationStep(
        id=stable("step:observe"),
        organization_id=principal.organization_id,
        job_id=row.job.id,
        investigation_id=investigation.id,
        sequence=1,
        kind="observe",
        tool="fake-local-vlm",
        state="completed",
        input_evidence_ids=[],
        output_evidence_ids=[str(first.id), str(second.id)],
        latency_ms=25,
        peak_memory_mb=128,
        cost_microunits=0,
        policy_decision="not_required",
        entropy_before=0.69314718,
        entropy_after=0.59295332,
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    session.add(step_one)
    session.add_all(
        (
            BeliefSnapshot(
                id=stable("belief:1"),
                organization_id=principal.organization_id,
                job_id=row.job.id,
                investigation_id=investigation.id,
                sequence=1,
                candidates=[
                    {"id": "candidate-a", "label": "Candidate A", "probability": 0.5},
                    {"id": "candidate-b", "label": "Candidate B", "probability": 0.5},
                ],
                entropy=0.69314718,
            ),
            BeliefSnapshot(
                id=stable("belief:2"),
                organization_id=principal.organization_id,
                job_id=row.job.id,
                investigation_id=investigation.id,
                sequence=2,
                candidates=[
                    {"id": "candidate-a", "label": "Candidate A", "probability": 0.72},
                    {"id": "candidate-b", "label": "Candidate B", "probability": 0.28},
                ],
                entropy=0.59295332,
            ),
        )
    )
    moved = transition_job(
        session,
        row.job.id,
        JobState.PROCESSING,
        JobState.READY_FOR_REVIEW,
        values={"stage": "needs_review", "progress": 100},
    )
    if moved is None:
        raise InvestigationConflict("state_conflict", "The job changed while seeding evidence.")
    investigation.status = "needs_review"
    investigation.trace_id = stable("trace")
    investigation.model_provenance = {
        "modelId": "fake-local-vlm",
        "modelDigest": "0" * 64,
        "promptDigest": "1" * 64,
        "executedLocally": True,
    }
    investigation.runtime_provenance = {
        "runtime": "deterministic-fake",
        "runtimeVersion": "1",
        "platform": "test",
    }
    investigation.calibrated_confidence = None
    investigation.updated_at = datetime.now(UTC)
    session.flush()
