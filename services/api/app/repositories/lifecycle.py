"""Tenant-scoped persistence queries for review, render and delivery lifecycle.

Every public identifier lookup takes an authenticated ``PrincipalContext`` and
includes the organization predicate in SQL.  Callers can therefore give the
same 404 response for an absent identifier and an identifier owned by another
tenant without first probing global existence.
"""

import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.core.tenancy import PrincipalContext
from app.models import Artifact, Deliverable, Job, Render, Review, SceneOverride


def get_job(
    session: Session,
    principal: PrincipalContext,
    job_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> Job | None:
    statement = sa.select(Job).where(
        Job.id == job_id,
        Job.organization_id == principal.organization_id,
    )
    if for_update:
        # A long-lived worker session may already hold an older ORM identity.
        # Row locking alone does not refresh it when expire_on_commit=False;
        # populate_existing makes the locked database row authoritative.
        statement = statement.with_for_update().execution_options(populate_existing=True)
    return session.execute(statement).scalar_one_or_none()


def get_review(
    session: Session,
    principal: PrincipalContext,
    job_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> Review | None:
    statement = sa.select(Review).where(
        Review.organization_id == principal.organization_id,
        Review.job_id == job_id,
    )
    if for_update:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    return session.execute(statement).scalar_one_or_none()


def get_render(
    session: Session,
    principal: PrincipalContext,
    job_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> Render | None:
    statement = sa.select(Render).where(
        Render.organization_id == principal.organization_id,
        Render.job_id == job_id,
    )
    if for_update:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    return session.execute(statement).scalar_one_or_none()


def get_scene_manifest(
    session: Session,
    principal: PrincipalContext,
    job_id: uuid.UUID,
) -> Artifact | None:
    return session.execute(
        sa.select(Artifact)
        .join(Job, Artifact.job_id == Job.id)
        .where(
            Artifact.organization_id == principal.organization_id,
            Artifact.job_id == job_id,
            Artifact.artifact_type == "scenes_json",
            Artifact.retention_state == "active",
            Job.organization_id == principal.organization_id,
        )
    ).scalar_one_or_none()


def list_scene_overrides(
    session: Session,
    principal: PrincipalContext,
    job_id: uuid.UUID,
) -> list[SceneOverride]:
    return list(
        session.execute(
            sa.select(SceneOverride)
            .join(Job, SceneOverride.job_id == Job.id)
            .where(
                SceneOverride.job_id == job_id,
                Job.organization_id == principal.organization_id,
            )
            .order_by(sa.func.length(SceneOverride.scene_id), SceneOverride.scene_id)
        ).scalars()
    )


def list_render_deliverables(
    session: Session,
    principal: PrincipalContext,
    render_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> list[Deliverable]:
    statement = (
        sa.select(Deliverable)
        .where(
            Deliverable.organization_id == principal.organization_id,
            Deliverable.render_id == render_id,
        )
        .order_by(Deliverable.format)
    )
    if for_update:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    return list(session.execute(statement).scalars())


def get_published_deliverable(
    session: Session,
    principal: PrincipalContext,
    deliverable_id: uuid.UUID,
) -> Deliverable | None:
    """Return only a member of an atomically completed five-file set."""

    return session.execute(
        sa.select(Deliverable)
        .join(
            Job,
            sa.and_(
                Deliverable.organization_id == Job.organization_id,
                Deliverable.job_id == Job.id,
            ),
        )
        .join(
            Render,
            sa.and_(
                Deliverable.organization_id == Render.organization_id,
                Deliverable.job_id == Render.job_id,
                Deliverable.render_id == Render.id,
            ),
        )
        .where(
            Deliverable.id == deliverable_id,
            Deliverable.organization_id == principal.organization_id,
            Deliverable.state == "published",
            Job.status == "COMPLETED",
            Render.state == "completed",
        )
    ).scalar_one_or_none()
