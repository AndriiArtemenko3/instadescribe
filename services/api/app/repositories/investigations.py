"""Tenant-qualified investigation selectors.

Malformed and cross-tenant public identifiers are resolved by the route layer
to the same 404.  These repository helpers never offer an unscoped lookup.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.core.tenancy import PrincipalContext
from app.models import (
    AnalystDecision,
    BeliefSnapshot,
    EvidenceItem,
    Investigation,
    InvestigationStep,
    Job,
    Project,
    SourceRecord,
)


@dataclass(frozen=True, slots=True)
class InvestigationRow:
    investigation: Investigation
    job: Job
    project: Project


def get_investigation(
    session: Session,
    principal: PrincipalContext,
    investigation_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> InvestigationRow | None:
    statement = (
        sa.select(Investigation, Job, Project)
        .join(
            Job,
            sa.and_(
                Job.organization_id == Investigation.organization_id,
                Job.id == Investigation.job_id,
            ),
        )
        .join(
            Project,
            sa.and_(
                Project.organization_id == Job.organization_id,
                Project.id == Job.project_id,
            ),
        )
        .where(
            Investigation.organization_id == principal.organization_id,
            Investigation.id == investigation_id,
            Job.workflow_kind == "video_investigation",
        )
    )
    if for_update:
        statement = statement.with_for_update(of=(Investigation, Job))
    row = session.execute(statement).one_or_none()
    return InvestigationRow(*row) if row is not None else None


def list_investigations(
    session: Session,
    principal: PrincipalContext,
    *,
    limit: int = 100,
) -> list[InvestigationRow]:
    rows = session.execute(
        sa.select(Investigation, Job, Project)
        .join(
            Job,
            sa.and_(
                Job.organization_id == Investigation.organization_id,
                Job.id == Investigation.job_id,
            ),
        )
        .join(
            Project,
            sa.and_(
                Project.organization_id == Job.organization_id,
                Project.id == Job.project_id,
            ),
        )
        .where(
            Investigation.organization_id == principal.organization_id,
            Job.workflow_kind == "video_investigation",
        )
        .order_by(Investigation.created_at.desc(), Investigation.id.desc())
        .limit(limit)
    ).all()
    return [InvestigationRow(*row) for row in rows]


def list_steps(
    session: Session,
    principal: PrincipalContext,
    investigation_id: uuid.UUID,
) -> list[InvestigationStep]:
    return list(
        session.execute(
            sa.select(InvestigationStep)
            .where(
                InvestigationStep.organization_id == principal.organization_id,
                InvestigationStep.investigation_id == investigation_id,
            )
            .order_by(InvestigationStep.sequence, InvestigationStep.id)
        ).scalars()
    )


def list_evidence(
    session: Session,
    principal: PrincipalContext,
    investigation_id: uuid.UUID,
) -> list[EvidenceItem]:
    return list(
        session.execute(
            sa.select(EvidenceItem)
            .where(
                EvidenceItem.organization_id == principal.organization_id,
                EvidenceItem.investigation_id == investigation_id,
            )
            .order_by(EvidenceItem.created_at, EvidenceItem.id)
        ).scalars()
    )


def list_beliefs(
    session: Session,
    principal: PrincipalContext,
    investigation_id: uuid.UUID,
) -> list[BeliefSnapshot]:
    return list(
        session.execute(
            sa.select(BeliefSnapshot)
            .where(
                BeliefSnapshot.organization_id == principal.organization_id,
                BeliefSnapshot.investigation_id == investigation_id,
            )
            .order_by(BeliefSnapshot.sequence, BeliefSnapshot.id)
        ).scalars()
    )


def get_decision(
    session: Session,
    principal: PrincipalContext,
    investigation_id: uuid.UUID,
) -> AnalystDecision | None:
    return session.execute(
        sa.select(AnalystDecision).where(
            AnalystDecision.organization_id == principal.organization_id,
            AnalystDecision.investigation_id == investigation_id,
        )
    ).scalar_one_or_none()


def get_source_record(
    session: Session,
    principal: PrincipalContext,
    investigation_id: uuid.UUID,
) -> SourceRecord | None:
    """Load the single tenant-qualified source-lineage record."""

    return session.execute(
        sa.select(SourceRecord).where(
            SourceRecord.organization_id == principal.organization_id,
            SourceRecord.investigation_id == investigation_id,
        )
    ).scalar_one_or_none()
