"""The `projects` table — the durable user-owned work item (ADR-0008 §1).

A project owns name/starred and future project-level lifecycle; each `jobs`
row is one processing execution/version belonging to a project.
"""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.tenancy import PORTFOLIO_ORGANIZATION_ID
from app.db.base import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        default=PORTFOLIO_ORGANIZATION_ID,
        server_default=sa.text(f"'{PORTFOLIO_ORGANIZATION_ID}'::uuid"),
    )
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    external_id: Mapped[str | None] = mapped_column(sa.String(255))
    starred: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("false")
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("1"))
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )

    __table_args__ = (
        sa.UniqueConstraint("organization_id", "id", name="uq_projects_organization_id_id"),
        sa.UniqueConstraint(
            "organization_id", "external_id", name="uq_projects_organization_id_external_id"
        ),
        sa.Index("ix_projects_organization_id_updated_at", "organization_id", "updated_at"),
        sa.Index("ix_projects_updated_at", "updated_at"),
        sa.CheckConstraint("version >= 1", name="version_min"),
        sa.CheckConstraint(
            "external_id IS NULL OR length(external_id) BETWEEN 1 AND 255",
            name="external_id_valid",
        ),
    )
