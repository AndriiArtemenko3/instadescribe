"""Conflict-safe scene override and review persistence.

First writes use INSERT ... ON CONFLICT DO NOTHING. Existing rows use a
single conditional UPDATE against the exact expected server version. A lost
race therefore returns a stable conflict instead of silently overwriting a
human decision.
"""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import SceneOverride


class StaleVersionError(Exception):
    """The row exists at another version, or the expected row is absent."""


def write_override(
    session: Session,
    job_id: uuid.UUID,
    scene_id: str,
    column_values: dict,
    *,
    expected_version: int,
    review_status: str,
    commit_transaction: bool = True,
) -> SceneOverride:
    """Insert once or update exactly one known version, atomically."""
    reviewed_at = datetime.now(UTC) if review_status in ("approved", "rejected") else None
    review_values = {
        "review_status": review_status,
        "reviewed_at": reviewed_at,
    }

    if expected_version == 0:
        stmt = (
            pg_insert(SceneOverride)
            .values(
                id=uuid.uuid4(),
                job_id=job_id,
                scene_id=scene_id,
                **column_values,
                **review_values,
            )
            .on_conflict_do_nothing(index_elements=["job_id", "scene_id"])
            .returning(SceneOverride)
        )
    else:
        stmt = (
            sa.update(SceneOverride)
            .where(
                SceneOverride.job_id == job_id,
                SceneOverride.scene_id == scene_id,
                SceneOverride.version == expected_version,
            )
            .values(
                **column_values,
                **review_values,
                updated_at=sa.func.now(),
                version=SceneOverride.version + 1,
            )
            .returning(SceneOverride)
        )

    row = session.execute(stmt).scalar_one_or_none()
    if row is None:
        raise StaleVersionError
    if commit_transaction:
        session.commit()
    else:
        session.flush()
    return row


def list_overrides(session: Session, job_id: uuid.UUID) -> list[SceneOverride]:
    """Deterministic numeric scene order (scene_2 before scene_10)."""
    return list(
        session.execute(
            sa.select(SceneOverride)
            .where(SceneOverride.job_id == job_id)
            .order_by(sa.func.length(SceneOverride.scene_id), SceneOverride.scene_id)
        ).scalars()
    )
