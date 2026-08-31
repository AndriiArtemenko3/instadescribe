"""Optimistic, atomic project metadata persistence."""

import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.models import Job, Project


class ProjectNotFoundError(Exception):
    pass


class StaleProjectVersionError(Exception):
    pass


def update_project(
    session: Session,
    project_id: uuid.UUID,
    organization_id: uuid.UUID,
    *,
    expected_version: int,
    column_values: dict,
    commit_transaction: bool = True,
) -> Project:
    any_job = sa.exists().where(
        Job.organization_id == organization_id,
        Job.project_id == Project.id,
    )
    audio_job = sa.exists().where(
        Job.organization_id == organization_id,
        Job.project_id == Project.id,
        Job.workflow_kind == "audio_description",
    )
    on_audio_surface = sa.or_(~any_job, audio_job)
    stmt = (
        sa.update(Project)
        .where(
            Project.id == project_id,
            Project.organization_id == organization_id,
            Project.version == expected_version,
            on_audio_surface,
        )
        .values(
            **column_values,
            updated_at=sa.func.now(),
            version=Project.version + 1,
        )
        .returning(Project)
    )
    row = session.execute(stmt).scalar_one_or_none()
    if row is None:
        exists = session.execute(
            sa.select(Project.id).where(
                Project.id == project_id,
                Project.organization_id == organization_id,
                on_audio_surface,
            )
        ).scalar_one_or_none()
        if exists is None:
            raise ProjectNotFoundError
        raise StaleProjectVersionError
    if commit_transaction:
        session.commit()
    else:
        session.flush()
    return row
