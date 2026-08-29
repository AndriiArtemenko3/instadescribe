"""The `artifacts` table — S3 object keys per job (never expiring URLs)."""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.tenancy import PORTFOLIO_ORGANIZATION_ID
from app.db.base import Base


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        nullable=False,
        default=PORTFOLIO_ORGANIZATION_ID,
        server_default=sa.text(f"'{PORTFOLIO_ORGANIZATION_ID}'::uuid"),
    )
    job_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    artifact_type: Mapped[str] = mapped_column(sa.String(60), nullable=False)
    object_key: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # Exact-version identity was historically hidden in ``metadata``.  The
    # normalized column is now authoritative for retention.  It remains
    # nullable so pre-versioning/corrupt legacy rows fail closed instead of
    # being deleted by key.
    version_id: Mapped[str | None] = mapped_column(sa.Text)
    content_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(sa.BigInteger)
    checksum_sha256: Mapped[str | None] = mapped_column(sa.Text)
    meta: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )
    retention_state: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, server_default=sa.text("'active'")
    )
    purged_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    purge_after: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("now() + interval '30 days'"),
    )

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["organization_id", "job_id"],
            ["jobs.organization_id", "jobs.id"],
            name="fk_artifacts_organization_id_job_id_jobs",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_artifacts_organization_id_id"),
        # One current artifact per (job, type); the worker upserts on conflict.
        sa.UniqueConstraint("job_id", "artifact_type", name="uq_artifacts_job_id_artifact_type"),
        sa.CheckConstraint(
            "version_id IS NULL OR (octet_length(version_id) BETWEEN 1 AND 1024 "
            "AND btrim(version_id) <> '')",
            name="version_id_valid",
        ),
        sa.CheckConstraint(
            "((retention_state = 'active' AND purged_at IS NULL) OR "
            "(retention_state = 'unrecoverable' AND purged_at IS NULL) OR "
            "(retention_state = 'purged' AND version_id IS NOT NULL "
            "AND purged_at IS NOT NULL AND purged_at >= created_at))",
            name="retention_state_consistent",
        ),
        sa.CheckConstraint("purge_after > created_at", name="retention_valid"),
        sa.Index("ix_artifacts_job_id", "job_id"),
        sa.Index("ix_artifacts_organization_id_job_id", "organization_id", "job_id"),
        sa.Index("ix_artifacts_purge_after", "purge_after"),
    )
