"""Organization quota configuration, monthly counters and reservations."""

import uuid
from datetime import date, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OrganizationQuota(Base):
    __tablename__ = "organization_quotas"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("organizations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    monthly_media_seconds: Mapped[Decimal] = mapped_column(
        sa.Numeric(14, 3), nullable=False, server_default=sa.text("36000")
    )
    max_processing_jobs: Mapped[int] = mapped_column(
        sa.SmallInteger, nullable=False, server_default=sa.text("1")
    )
    max_awaiting_upload_jobs: Mapped[int] = mapped_column(
        sa.SmallInteger, nullable=False, server_default=sa.text("5")
    )
    max_queued_jobs: Mapped[int] = mapped_column(
        sa.SmallInteger, nullable=False, server_default=sa.text("10")
    )
    source_retention_days: Mapped[int] = mapped_column(
        sa.SmallInteger, nullable=False, server_default=sa.text("30")
    )
    deliverable_retention_days: Mapped[int] = mapped_column(
        sa.SmallInteger, nullable=False, server_default=sa.text("90")
    )
    metadata_retention_days: Mapped[int] = mapped_column(
        sa.SmallInteger, nullable=False, server_default=sa.text("365")
    )
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
        sa.CheckConstraint("monthly_media_seconds >= 0", name="media_limit_nonnegative"),
        sa.CheckConstraint("max_processing_jobs BETWEEN 1 AND 100", name="processing_limit_valid"),
        sa.CheckConstraint(
            "max_awaiting_upload_jobs BETWEEN 1 AND 1000", name="awaiting_limit_valid"
        ),
        sa.CheckConstraint("max_queued_jobs BETWEEN 1 AND 1000", name="queued_limit_valid"),
        sa.CheckConstraint("source_retention_days BETWEEN 1 AND 30", name="source_retention_valid"),
        sa.CheckConstraint(
            "deliverable_retention_days BETWEEN 1 AND 90",
            name="deliverable_retention_valid",
        ),
        sa.CheckConstraint(
            "metadata_retention_days BETWEEN 1 AND 365", name="metadata_retention_valid"
        ),
    )


class OrganizationJobCapacity(Base):
    """Transactionally updated counters for the organization's job-state caps."""

    __tablename__ = "organization_job_capacity"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("organizations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    awaiting_upload_jobs: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    queued_jobs: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    processing_jobs: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
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
        sa.CheckConstraint("awaiting_upload_jobs >= 0", name="awaiting_nonnegative"),
        sa.CheckConstraint("queued_jobs >= 0", name="queued_nonnegative"),
        sa.CheckConstraint("processing_jobs >= 0", name="processing_nonnegative"),
        sa.CheckConstraint("version >= 1", name="version_min"),
    )


class OrganizationUsagePeriod(Base):
    __tablename__ = "organization_usage_periods"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    period_start: Mapped[date] = mapped_column(sa.Date, nullable=False)
    period_end: Mapped[date] = mapped_column(sa.Date, nullable=False)
    reserved_media_seconds: Mapped[Decimal] = mapped_column(
        sa.Numeric(14, 3), nullable=False, server_default=sa.text("0")
    )
    consumed_media_seconds: Mapped[Decimal] = mapped_column(
        sa.Numeric(14, 3), nullable=False, server_default=sa.text("0")
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
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_organization_usage_periods_organization_id_id"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "period_start",
            name="uq_organization_usage_periods_org_start",
        ),
        sa.CheckConstraint("period_end > period_start", name="period_valid"),
        sa.CheckConstraint("reserved_media_seconds >= 0", name="reserved_nonnegative"),
        sa.CheckConstraint("consumed_media_seconds >= 0", name="consumed_nonnegative"),
        sa.CheckConstraint("version >= 1", name="version_min"),
        sa.Index("ix_organization_usage_periods_period_end", "period_end"),
    )


class QuotaReservation(Base):
    __tablename__ = "quota_reservations"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    usage_period_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    state: Mapped[str] = mapped_column(
        sa.String(20), nullable=False, server_default=sa.text("'reserved'")
    )
    reserved_seconds: Mapped[Decimal] = mapped_column(sa.Numeric(12, 3), nullable=False)
    actual_seconds: Mapped[Decimal | None] = mapped_column(sa.Numeric(12, 3))
    expires_at: Mapped[datetime] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=False)
    finalized_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
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
        sa.ForeignKeyConstraint(
            ["organization_id", "usage_period_id"],
            ["organization_usage_periods.organization_id", "organization_usage_periods.id"],
            name="fk_quota_reservations_org_usage_period",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "job_id"],
            ["jobs.organization_id", "jobs.id"],
            name="fk_quota_reservations_organization_id_job_id_jobs",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_quota_reservations_organization_id_id"
        ),
        sa.UniqueConstraint(
            "organization_id", "job_id", name="uq_quota_reservations_organization_id_job_id"
        ),
        sa.CheckConstraint(
            "state IN ('reserved', 'consumed', 'released', 'expired')", name="state_valid"
        ),
        sa.CheckConstraint("reserved_seconds > 0", name="reserved_positive"),
        sa.CheckConstraint(
            "actual_seconds IS NULL OR actual_seconds >= 0", name="actual_nonnegative"
        ),
        sa.CheckConstraint("expires_at > created_at", name="expiry_valid"),
        sa.CheckConstraint(
            "(state = 'reserved' AND finalized_at IS NULL AND actual_seconds IS NULL) OR "
            "(state = 'consumed' AND finalized_at IS NOT NULL AND actual_seconds IS NOT NULL) OR "
            "(state IN ('released', 'expired') AND finalized_at IS NOT NULL "
            "AND actual_seconds IS NULL)",
            name="finalization_consistent",
        ),
        sa.Index("ix_quota_reservations_organization_id_state", "organization_id", "state"),
        sa.Index("ix_quota_reservations_expires_at", "expires_at"),
    )
