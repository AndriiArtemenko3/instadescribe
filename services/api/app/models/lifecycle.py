"""Tenant-scoped B2B lifecycle and delivery persistence.

The tables in this module are deliberately persistence primitives rather than
workflow code.  Every customer-owned row carries ``organization_id`` and uses
composite foreign keys so an application bug cannot attach a child resource to
another tenant.  Object rows persist version-pinned S3 identities, never
presigned URLs or signing secrets.
"""

import uuid
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Asset(Base):
    """One job-level source video and optional timed transcript."""

    __tablename__ = "assets"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    asset_type: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        sa.String(24), nullable=False, server_default=sa.text("'awaiting_upload'")
    )
    object_key: Mapped[str] = mapped_column(sa.Text, nullable=False)
    content_type: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    version_id: Mapped[str | None] = mapped_column(sa.Text)
    etag: Mapped[str | None] = mapped_column(sa.Text)
    checksum_sha256: Mapped[str | None] = mapped_column(sa.String(64))
    duration_seconds: Mapped[Decimal | None] = mapped_column(sa.Numeric(12, 3))
    transcript_format: Mapped[str | None] = mapped_column(sa.String(8))
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )
    validated_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    purge_after: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("now() + interval '30 days'"),
    )

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["organization_id", "job_id"],
            ["jobs.organization_id", "jobs.id"],
            name="fk_assets_organization_id_job_id_jobs",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_assets_organization_id_id"),
        sa.UniqueConstraint(
            "organization_id",
            "job_id",
            "asset_type",
            name="uq_assets_organization_id_job_id_asset_type",
        ),
        sa.CheckConstraint(
            "asset_type IN ('source_video', 'source_transcript')",
            name="asset_type_valid",
        ),
        sa.CheckConstraint(
            "octet_length(object_key) BETWEEN 1 AND 1024 AND "
            "position('..' in object_key) = 0 AND "
            "((asset_type = 'source_video' AND object_key LIKE "
            "'uploads/orgs/' || organization_id::text || '/jobs/' || job_id::text || "
            "'/source/%') OR (asset_type = 'source_transcript' AND object_key LIKE "
            "'uploads/orgs/' || organization_id::text || '/jobs/' || job_id::text || "
            "'/transcript/%'))",
            name="object_key_tenant_scoped",
        ),
        sa.CheckConstraint(
            "status IN ('awaiting_upload', 'uploaded', 'validated', 'rejected', 'deleted')",
            name="status_valid",
        ),
        sa.CheckConstraint(
            "size_bytes >= 0 AND "
            "((asset_type = 'source_video' AND size_bytes <= 1073741824) OR "
            "(asset_type = 'source_transcript' AND size_bytes <= 10485760))",
            name="size_within_product_limit",
        ),
        sa.CheckConstraint(
            "checksum_sha256 IS NULL OR checksum_sha256 ~ '^[a-f0-9]{64}$'",
            name="checksum_valid",
        ),
        sa.CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds BETWEEN 0 AND 3600",
            name="duration_within_product_limit",
        ),
        sa.CheckConstraint(
            "((asset_type = 'source_transcript' AND transcript_format IS NOT NULL "
            "AND transcript_format IN ('vtt', 'srt') AND duration_seconds IS NULL) OR "
            "(asset_type = 'source_video' AND transcript_format IS NULL))",
            name="type_metadata_consistent",
        ),
        sa.CheckConstraint(
            "((status = 'validated' AND version_id IS NOT NULL AND etag IS NOT NULL "
            "AND validated_at IS NOT NULL) OR "
            "(status <> 'validated' AND validated_at IS NULL))",
            name="validation_consistent",
        ),
        sa.CheckConstraint("purge_after > created_at", name="retention_valid"),
        sa.Index("ix_assets_organization_id_job_id", "organization_id", "job_id"),
        sa.Index("ix_assets_purge_after", "purge_after"),
    )


class Review(Base):
    """Atomic review-completion snapshot for a job's immutable scene set."""

    __tablename__ = "reviews"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    state: Mapped[str] = mapped_column(
        sa.String(20), nullable=False, server_default=sa.text("'open'")
    )
    version: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("1"))
    scene_count: Mapped[int | None] = mapped_column(sa.Integer)
    approved_scene_count: Mapped[int | None] = mapped_column(sa.Integer)
    rejected_scene_count: Mapped[int | None] = mapped_column(sa.Integer)
    locked_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    completed_by_principal_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("principals.id", ondelete="RESTRICT")
    )
    zero_ad_confirmed_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )
    inactivity_expires_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("now() + interval '30 days'"),
    )

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["organization_id", "job_id"],
            ["jobs.organization_id", "jobs.id"],
            name="fk_reviews_organization_id_job_id_jobs",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_reviews_organization_id_id"),
        sa.UniqueConstraint("organization_id", "job_id", name="uq_reviews_organization_id_job_id"),
        sa.UniqueConstraint(
            "organization_id",
            "job_id",
            "id",
            name="uq_reviews_organization_id_job_id_id",
        ),
        sa.CheckConstraint("state IN ('open', 'completed', 'expired')", name="state_valid"),
        sa.CheckConstraint("version >= 1", name="version_min"),
        sa.CheckConstraint("inactivity_expires_at > created_at", name="inactivity_expiry_valid"),
        sa.CheckConstraint(
            "(state = 'open' AND locked_at IS NULL AND completed_at IS NULL "
            "AND completed_by_principal_id IS NULL AND zero_ad_confirmed_at IS NULL "
            "AND scene_count IS NULL AND approved_scene_count IS NULL "
            "AND rejected_scene_count IS NULL) OR "
            "(state = 'expired' AND locked_at IS NOT NULL AND completed_at IS NULL "
            "AND completed_by_principal_id IS NULL AND zero_ad_confirmed_at IS NULL "
            "AND scene_count IS NULL AND approved_scene_count IS NULL "
            "AND rejected_scene_count IS NULL) OR "
            "(state = 'completed' AND locked_at IS NOT NULL AND completed_at IS NOT NULL "
            "AND completed_at >= locked_at AND completed_by_principal_id IS NOT NULL "
            "AND scene_count IS NOT NULL AND approved_scene_count IS NOT NULL "
            "AND rejected_scene_count IS NOT NULL AND scene_count >= 0 "
            "AND approved_scene_count >= 0 AND rejected_scene_count >= 0 "
            "AND approved_scene_count + rejected_scene_count = scene_count "
            "AND ((approved_scene_count = 0 AND zero_ad_confirmed_at IS NOT NULL) OR "
            "(approved_scene_count > 0 AND zero_ad_confirmed_at IS NULL)))",
            name="completion_consistent",
        ),
        sa.Index("ix_reviews_organization_id_state", "organization_id", "state"),
        sa.Index("ix_reviews_inactivity_expires_at", "inactivity_expires_at"),
    )


class Render(Base):
    """One fenced five-format render attempt per completed review."""

    __tablename__ = "renders"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    review_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    state: Mapped[str] = mapped_column(
        sa.String(20), nullable=False, server_default=sa.text("'queued'")
    )
    fence_token: Mapped[int] = mapped_column(
        sa.BigInteger, nullable=False, server_default=sa.text("1")
    )
    attempt_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    worker_id: Mapped[str | None] = mapped_column(sa.String(255))
    lease_expires_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    integrity_manifest: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    error_code: Mapped[str | None] = mapped_column(sa.String(80))
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
            ["organization_id", "job_id"],
            ["jobs.organization_id", "jobs.id"],
            name="fk_renders_organization_id_job_id_jobs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "job_id", "review_id"],
            ["reviews.organization_id", "reviews.job_id", "reviews.id"],
            name="fk_renders_organization_id_job_id_review_id_reviews",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_renders_organization_id_id"),
        sa.UniqueConstraint("organization_id", "job_id", name="uq_renders_organization_id_job_id"),
        sa.UniqueConstraint(
            "organization_id",
            "job_id",
            "id",
            name="uq_renders_organization_id_job_id_id",
        ),
        sa.UniqueConstraint(
            "organization_id", "review_id", name="uq_renders_organization_id_review_id"
        ),
        sa.CheckConstraint(
            "state IN ('queued', 'rendering', 'completed', 'failed', 'cancelled')",
            name="state_valid",
        ),
        sa.CheckConstraint("fence_token >= 1", name="fence_token_min"),
        sa.CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        sa.CheckConstraint("jsonb_typeof(integrity_manifest) = 'object'", name="manifest_object"),
        sa.CheckConstraint(
            "(state = 'queued' AND started_at IS NULL AND completed_at IS NULL) OR "
            "(state = 'rendering' AND started_at IS NOT NULL AND completed_at IS NULL) OR "
            "(state IN ('completed', 'failed', 'cancelled') AND completed_at IS NOT NULL "
            "AND (started_at IS NULL OR completed_at >= started_at))",
            name="timestamps_consistent",
        ),
        sa.Index("ix_renders_organization_id_state", "organization_id", "state"),
        sa.Index("ix_renders_lease_expires_at", "lease_expires_at"),
    )


class RenderAttemptArtifact(Base):
    """Internal exact-version cleanup journal for one render attempt.

    These rows are never exposed as deliverables.  A row is inserted only
    after S3 returned a VersionId and is removed either in the successful
    publication transaction or after that exact version was deleted.  This
    gives crash recovery a durable identity without ever falling back to a
    key-only or prefix-wide delete.
    """

    __tablename__ = "render_attempt_artifacts"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    render_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    fence_token: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    format: Mapped[str] = mapped_column(sa.String(12), nullable=False)
    object_key: Mapped[str] = mapped_column(sa.Text, nullable=False)
    version_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["organization_id", "job_id", "render_id"],
            ["renders.organization_id", "renders.job_id", "renders.id"],
            name="fk_render_attempt_artifacts_org_job_render",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "render_id",
            "fence_token",
            "format",
            name="uq_render_attempt_artifacts_render_fence_format",
        ),
        sa.CheckConstraint(
            "format IN ('mp4', 'mp3', 'srt', 'csv', 'docx')",
            name="format_valid",
        ),
        sa.CheckConstraint("fence_token >= 1", name="fence_token_min"),
        sa.CheckConstraint(
            "octet_length(version_id) BETWEEN 1 AND 1024",
            name="version_id_valid",
        ),
        sa.CheckConstraint(
            "octet_length(object_key) BETWEEN 1 AND 1024 AND "
            "position('..' in object_key) = 0 AND object_key = "
            "'deliverables/orgs/' || organization_id::text || '/jobs/' || "
            "job_id::text || '/attempts/' || fence_token::text || '/' || "
            "CASE format "
            "WHEN 'mp4' THEN 'described_video.mp4' "
            "WHEN 'mp3' THEN 'audio_description.mp3' "
            "WHEN 'srt' THEN 'audio_description.srt' "
            "WHEN 'csv' THEN 'audio_description.csv' "
            "WHEN 'docx' THEN 'audio_description.docx' END",
            name="object_key_exact_attempt_identity",
        ),
        sa.Index(
            "ix_render_attempt_artifacts_render_fence",
            "organization_id",
            "render_id",
            "fence_token",
        ),
        sa.Index("ix_render_attempt_artifacts_created_at", "created_at"),
    )


class Deliverable(Base):
    """A staged or atomically published version-pinned output object."""

    __tablename__ = "deliverables"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    render_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    format: Mapped[str] = mapped_column(sa.String(12), nullable=False)
    state: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, server_default=sa.text("'staged'")
    )
    object_key: Mapped[str] = mapped_column(sa.Text, nullable=False)
    version_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    content_type: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    purged_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    purge_after: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("now() + interval '90 days'"),
    )

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["organization_id", "job_id"],
            ["jobs.organization_id", "jobs.id"],
            name="fk_deliverables_organization_id_job_id_jobs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "job_id", "render_id"],
            ["renders.organization_id", "renders.job_id", "renders.id"],
            name="fk_deliverables_org_job_render",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_deliverables_organization_id_id"),
        sa.UniqueConstraint(
            "organization_id",
            "render_id",
            "format",
            name="uq_deliverables_organization_id_render_id_format",
        ),
        sa.CheckConstraint("format IN ('mp4', 'mp3', 'srt', 'csv', 'docx')", name="format_valid"),
        sa.CheckConstraint("state IN ('staged', 'published', 'purged')", name="state_valid"),
        sa.CheckConstraint("size_bytes >= 0", name="size_nonnegative"),
        sa.CheckConstraint("checksum_sha256 ~ '^[a-f0-9]{64}$'", name="checksum_valid"),
        sa.CheckConstraint(
            "((state = 'staged' AND published_at IS NULL AND purged_at IS NULL) OR "
            "(state = 'published' AND published_at IS NOT NULL AND purged_at IS NULL) OR "
            "(state = 'purged' AND published_at IS NOT NULL AND purged_at IS NOT NULL "
            "AND purged_at >= published_at))",
            name="publication_consistent",
        ),
        sa.CheckConstraint("purge_after > created_at", name="retention_valid"),
        sa.Index("ix_deliverables_organization_id_job_id", "organization_id", "job_id"),
        sa.Index("ix_deliverables_purge_after", "purge_after"),
    )


class JobEvent(Base):
    """Immutable domain event and transactional-outbox record."""

    __tablename__ = "job_events"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    event_type: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    job_version: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )
    available_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    purge_after: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("now() + interval '365 days'"),
    )

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["organization_id", "job_id"],
            ["jobs.organization_id", "jobs.id"],
            name="fk_job_events_organization_id_job_id_jobs",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_job_events_organization_id_id"),
        sa.UniqueConstraint(
            "organization_id",
            "job_id",
            "event_type",
            name="uq_job_events_organization_id_job_id_event_type",
        ),
        sa.CheckConstraint(
            "event_type IN ('job.needs_review', 'job.completed', 'job.failed', "
            "'job.cancelled', 'render.requested')",
            name="event_type_valid",
        ),
        sa.CheckConstraint("job_version >= 1", name="job_version_min"),
        sa.CheckConstraint("jsonb_typeof(payload) = 'object'", name="payload_object"),
        sa.CheckConstraint(
            "available_at >= occurred_at AND "
            "(dispatched_at IS NULL OR dispatched_at >= occurred_at)",
            name="timestamps_consistent",
        ),
        sa.CheckConstraint("purge_after > occurred_at", name="retention_valid"),
        sa.Index(
            "ix_job_events_pending",
            "available_at",
            postgresql_where=sa.text("dispatched_at IS NULL"),
        ),
        sa.Index("ix_job_events_organization_id_occurred_at", "organization_id", "occurred_at"),
    )


class WebhookEndpoint(Base):
    """Operator-managed organization webhook; signing material stays external."""

    __tablename__ = "webhook_endpoints"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    endpoint_url: Mapped[str] = mapped_column(sa.Text, nullable=False)
    signing_secret_ref: Mapped[str] = mapped_column(sa.String(500), nullable=False)
    secret_version: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("1")
    )
    is_active: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("true")
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
    disabled_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))

    __table_args__ = (
        sa.UniqueConstraint("organization_id", name="uq_webhook_endpoints_organization_id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_webhook_endpoints_organization_id_id"
        ),
        sa.CheckConstraint("endpoint_url ~ '^https://[^[:space:]]+$'", name="endpoint_url_https"),
        sa.CheckConstraint("length(signing_secret_ref) > 0", name="secret_ref_nonempty"),
        sa.CheckConstraint("secret_version >= 1", name="secret_version_min"),
        sa.CheckConstraint(
            "((is_active AND disabled_at IS NULL) OR (NOT is_active AND disabled_at IS NOT NULL))",
            name="activation_consistent",
        ),
    )


class WebhookDelivery(Base):
    """At-least-once delivery state; retries retain the immutable event id."""

    __tablename__ = "webhook_deliveries"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    endpoint_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    state: Mapped[str] = mapped_column(
        sa.String(24), nullable=False, server_default=sa.text("'pending'")
    )
    attempt_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    last_attempt_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    last_status_code: Mapped[int | None] = mapped_column(sa.SmallInteger)
    last_error_code: Mapped[str | None] = mapped_column(sa.String(80))
    delivered_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
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
            ["organization_id", "endpoint_id"],
            ["webhook_endpoints.organization_id", "webhook_endpoints.id"],
            name="fk_webhook_deliveries_org_endpoint",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "event_id"],
            ["job_events.organization_id", "job_events.id"],
            name="fk_webhook_deliveries_org_event",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "endpoint_id",
            "event_id",
            name="uq_webhook_deliveries_endpoint_event",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'in_flight', 'retry_scheduled', 'succeeded', 'exhausted')",
            name="state_valid",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        sa.CheckConstraint(
            "last_status_code IS NULL OR last_status_code BETWEEN 100 AND 599",
            name="status_code_valid",
        ),
        sa.CheckConstraint(
            "((state = 'in_flight' AND lease_expires_at IS NOT NULL) OR "
            "(state <> 'in_flight' AND lease_expires_at IS NULL))",
            name="lease_consistent",
        ),
        sa.CheckConstraint(
            "((state = 'succeeded' AND delivered_at IS NOT NULL) OR "
            "(state <> 'succeeded' AND delivered_at IS NULL))",
            name="delivery_consistent",
        ),
        sa.Index(
            "ix_webhook_deliveries_due",
            "next_attempt_at",
            postgresql_where=sa.text("state IN ('pending', 'retry_scheduled')"),
        ),
        sa.Index("ix_webhook_deliveries_organization_id_event_id", "organization_id", "event_id"),
    )


class AuditEvent(Base):
    """Append-only, sanitized organization audit record."""

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    actor_principal_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("principals.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    resource_type: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(sa.String(200))
    request_id: Mapped[str | None] = mapped_column(sa.String(200))
    details: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    occurred_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )
    purge_after: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("now() + interval '365 days'"),
    )

    __table_args__ = (
        sa.CheckConstraint("length(action) > 0", name="action_nonempty"),
        sa.CheckConstraint("length(resource_type) > 0", name="resource_type_nonempty"),
        sa.CheckConstraint("jsonb_typeof(details) = 'object'", name="details_object"),
        sa.CheckConstraint("purge_after > occurred_at", name="retention_valid"),
        sa.Index("ix_audit_events_organization_id_occurred_at", "organization_id", "occurred_at"),
        sa.Index("ix_audit_events_purge_after", "purge_after"),
    )
