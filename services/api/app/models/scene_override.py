"""Atomic, versioned human review state for immutable generated scenes.

The original generated ``scenes_json`` artifact is never rewritten. Each row
stores only the human-controlled override/review projection, guarded by an
optimistic version and database-level review/timestamp invariants.
"""

import uuid
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SceneOverride(Base):
    __tablename__ = "scene_overrides"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    scene_id: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    text: Mapped[str | None] = mapped_column(sa.Text)
    active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.text("true"))
    locked: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("false")
    )
    voice: Mapped[str | None] = mapped_column(sa.String(80))
    speed: Mapped[Decimal | None] = mapped_column(sa.Numeric(4, 2))
    review_status: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("1"))
    # The G6 Core upsert sets updated_at explicitly; ORM onupdate is a
    # convenience for ORM-path writes only and is NOT relied upon.
    updated_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )

    __table_args__ = (
        sa.UniqueConstraint("job_id", "scene_id"),
        sa.Index("ix_scene_overrides_job_id", "job_id"),
        # Base names: the naming convention expands them (ck_scene_overrides_*).
        sa.CheckConstraint("version >= 1", name="version_min"),
        sa.CheckConstraint(
            "speed IS NULL OR (speed >= 0.50 AND speed <= 2.50)", name="speed_range"
        ),
        sa.CheckConstraint("scene_id ~ '^scene_[1-9][0-9]*$'", name="scene_id_canonical"),
        sa.CheckConstraint(
            "review_status IN ('edited', 'approved', 'rejected')",
            name="review_status_valid",
        ),
        sa.CheckConstraint(
            "((review_status IN ('approved', 'rejected') AND reviewed_at IS NOT NULL) "
            "OR (review_status = 'edited' AND reviewed_at IS NULL))",
            name="review_timestamp_consistent",
        ),
    )
