"""The `jobs` table — **processing jobs**: one execution/version per row,
belonging to a durable project (ADR-0008 §1; table name retained for bounded
v0.1 compatibility).

Server-side defaults and CHECK constraints carry correctness across the API
and worker processes. The partial unique index enforces one PROCESSING job per
organization atomically; queued and awaiting-upload limits use tenant capacity
counters rather than a count-then-insert race.
"""

import uuid
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.tenancy import PORTFOLIO_ORGANIZATION_ID
from app.db.base import Base
from app.domain.states import JobState

_STATUS_LIST = ", ".join(f"'{s.value}'" for s in JobState)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        nullable=False,
        default=PORTFOLIO_ORGANIZATION_ID,
        server_default=sa.text(f"'{PORTFOLIO_ORGANIZATION_ID}'::uuid"),
    )
    workflow_kind: Mapped[str] = mapped_column(
        sa.String(40),
        nullable=False,
        default="audio_description",
        server_default=sa.text("'audio_description'"),
    )
    project_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    client_reference: Mapped[str | None] = mapped_column(sa.String(255))
    # Immutable, server-supplied provenance — clients may never choose it.
    # 'dev' locally; the immutable code/image revision in deployment;
    # 'unknown-pre-g3' honest sentinel for rows backfilled by migration 0002.
    pipeline_revision: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    stage: Mapped[str | None] = mapped_column(sa.String(80))
    progress: Mapped[int] = mapped_column(
        sa.SmallInteger, nullable=False, server_default=sa.text("0")
    )
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False)

    input_object_key: Mapped[str | None] = mapped_column(sa.Text)
    input_content_type: Mapped[str | None] = mapped_column(sa.Text)
    input_size_bytes: Mapped[int | None] = mapped_column(sa.BigInteger)
    duration_secs: Mapped[Decimal | None] = mapped_column(sa.Numeric)

    provider: Mapped[str | None] = mapped_column(sa.String(40))
    model: Mapped[str | None] = mapped_column(sa.String(120))

    attempt_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("3")
    )
    enqueue_failed_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    enqueue_error: Mapped[str | None] = mapped_column(sa.Text)

    # G4 durable source verification (from HeadObject, never the client).
    # source_etag is an opaque object validator — NOT a checksum; a SHA-256 is
    # stored only when S3 genuinely supplies one.
    source_etag: Mapped[str | None] = mapped_column(sa.Text)
    source_version_id: Mapped[str | None] = mapped_column(sa.Text)
    source_checksum_sha256: Mapped[str | None] = mapped_column(sa.Text)
    upload_verified_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))

    # G4 recoverable enqueue: one stable message identity per job across
    # retries; attempts are metadata, never a new logical message.
    enqueue_message_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
    enqueue_requested_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    enqueue_attempt_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    enqueued_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))

    lease_expires_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    worker_id: Mapped[str | None] = mapped_column(sa.Text)

    version: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("1"))

    # Safe, classified error surface — never raw tracebacks (they stay in logs).
    error_code: Mapped[str | None] = mapped_column(sa.Text)
    error_message: Mapped[str | None] = mapped_column(sa.Text)

    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )
    started_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["organization_id", "project_id"],
            ["projects.organization_id", "projects.id"],
            name="fk_jobs_organization_id_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_jobs_organization_id_id"),
        sa.UniqueConstraint(
            "organization_id",
            "client_reference",
            name="uq_jobs_organization_id_client_reference",
        ),
        sa.CheckConstraint(f"status IN ({_STATUS_LIST})", name="status_valid"),
        sa.CheckConstraint(
            "workflow_kind IN ('audio_description', 'video_investigation')",
            name="workflow_kind_valid",
        ),
        sa.CheckConstraint("progress >= 0 AND progress <= 100", name="progress_range"),
        sa.CheckConstraint("enqueue_attempt_count >= 0", name="enqueue_attempts_nonneg"),
        sa.CheckConstraint(
            "client_reference IS NULL OR length(client_reference) BETWEEN 1 AND 255",
            name="client_reference_valid",
        ),
        sa.Index("ix_jobs_organization_id_created_at", "organization_id", "created_at"),
        sa.Index("ix_jobs_status_created_at", "status", "created_at"),
        sa.Index("ix_jobs_updated_at", "updated_at"),
        sa.Index("ix_jobs_project_id_created_at", "project_id", "created_at"),
        # Queued work is bounded by organization counters. Only provider work
        # gets a hard database mutex: one PROCESSING job per organization.
        sa.Index(
            "uq_jobs_one_compute_active",
            "organization_id",
            unique=True,
            postgresql_where=sa.text("status = 'PROCESSING'"),
        ),
        sa.Index(
            "ix_jobs_organization_id_status_created_at",
            "organization_id",
            "status",
            "created_at",
        ),
    )
