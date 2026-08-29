"""Tenant-safe B2B lifecycle, outbox, retention and quota records.

The populated upgrade derives every existing job organization from its
project before making the column non-null.  A composite project/job foreign
key then makes cross-organization linkage impossible.  The original global
compute-slot index is replaced in-place by the same named per-organization
PROCESSING mutex; queued and awaiting-upload capacity use counters.

Revision ID: 0007_b2b_lifecycle
Revises: 0006_organization_tenancy
Create Date: 2026-08-28
"""

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0007_b2b_lifecycle"
down_revision = "0006_organization_tenancy"
branch_labels = None
depends_on = None

PORTFOLIO_ORGANIZATION_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
_ACTIVE = "'PROCESSING', 'QUEUED', 'UPLOAD_COMPLETE'"


def upgrade() -> None:
    portfolio_default = sa.text(f"'{PORTFOLIO_ORGANIZATION_ID}'::uuid")

    op.add_column("projects", sa.Column("external_id", sa.String(length=255), nullable=True))
    op.create_unique_constraint(
        "uq_projects_organization_id_id", "projects", ["organization_id", "id"]
    )
    op.create_unique_constraint(
        "uq_projects_organization_id_external_id",
        "projects",
        ["organization_id", "external_id"],
    )
    op.create_check_constraint(
        "external_id_valid",
        "projects",
        "external_id IS NULL OR length(external_id) BETWEEN 1 AND 255",
    )

    op.add_column(
        "jobs",
        sa.Column(
            "organization_id",
            sa.Uuid(),
            server_default=portfolio_default,
            nullable=True,
        ),
    )
    op.execute(
        "UPDATE jobs AS j SET organization_id = p.organization_id "
        "FROM projects AS p WHERE p.id = j.project_id"
    )
    op.alter_column(
        "jobs",
        "organization_id",
        existing_type=sa.Uuid(),
        nullable=False,
        server_default=portfolio_default,
    )
    op.add_column("jobs", sa.Column("client_reference", sa.String(length=255), nullable=True))
    op.drop_index("uq_jobs_one_compute_active", table_name="jobs")
    op.drop_constraint("fk_jobs_project_id_projects", "jobs", type_="foreignkey")
    op.create_unique_constraint("uq_jobs_organization_id_id", "jobs", ["organization_id", "id"])
    op.create_unique_constraint(
        "uq_jobs_organization_id_client_reference",
        "jobs",
        ["organization_id", "client_reference"],
    )
    op.create_check_constraint(
        "client_reference_valid",
        "jobs",
        "client_reference IS NULL OR length(client_reference) BETWEEN 1 AND 255",
    )
    op.create_foreign_key(
        "fk_jobs_organization_id_project_id_projects",
        "jobs",
        "projects",
        ["organization_id", "project_id"],
        ["organization_id", "id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_jobs_organization_id_created_at",
        "jobs",
        ["organization_id", "created_at"],
    )
    op.create_index(
        "uq_jobs_one_compute_active",
        "jobs",
        ["organization_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PROCESSING'"),
    )
    op.create_index(
        "ix_jobs_organization_id_status_created_at",
        "jobs",
        ["organization_id", "status", "created_at"],
    )

    op.create_table(
        "organization_quotas",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column(
            "monthly_media_seconds",
            sa.Numeric(precision=14, scale=3),
            server_default=sa.text("36000"),
            nullable=False,
        ),
        sa.Column(
            "max_processing_jobs", sa.SmallInteger(), server_default=sa.text("1"), nullable=False
        ),
        sa.Column(
            "max_awaiting_upload_jobs",
            sa.SmallInteger(),
            server_default=sa.text("5"),
            nullable=False,
        ),
        sa.Column(
            "max_queued_jobs", sa.SmallInteger(), server_default=sa.text("10"), nullable=False
        ),
        sa.Column(
            "source_retention_days",
            sa.SmallInteger(),
            server_default=sa.text("30"),
            nullable=False,
        ),
        sa.Column(
            "deliverable_retention_days",
            sa.SmallInteger(),
            server_default=sa.text("90"),
            nullable=False,
        ),
        sa.Column(
            "metadata_retention_days",
            sa.SmallInteger(),
            server_default=sa.text("365"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
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
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_organization_quotas_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("organization_id", name="pk_organization_quotas"),
    )
    op.execute(
        "INSERT INTO organization_quotas (organization_id) "
        "SELECT id FROM organizations ON CONFLICT (organization_id) DO NOTHING"
    )

    op.create_table(
        "organization_job_capacity",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column(
            "awaiting_upload_jobs", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("queued_jobs", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("processing_jobs", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("awaiting_upload_jobs >= 0", name="awaiting_nonnegative"),
        sa.CheckConstraint("queued_jobs >= 0", name="queued_nonnegative"),
        sa.CheckConstraint("processing_jobs >= 0", name="processing_nonnegative"),
        sa.CheckConstraint("version >= 1", name="version_min"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_organization_job_capacity_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("organization_id", name="pk_organization_job_capacity"),
    )
    op.execute(
        "INSERT INTO organization_job_capacity "
        "(organization_id, awaiting_upload_jobs, queued_jobs, processing_jobs) "
        "SELECT o.id, "
        "count(j.id) FILTER (WHERE j.status = 'AWAITING_UPLOAD')::integer, "
        "count(j.id) FILTER (WHERE j.status IN ('UPLOAD_COMPLETE', 'QUEUED'))::integer, "
        "count(j.id) FILTER (WHERE j.status = 'PROCESSING')::integer "
        "FROM organizations AS o LEFT JOIN jobs AS j ON j.organization_id = o.id "
        "GROUP BY o.id ON CONFLICT (organization_id) DO NOTHING"
    )

    op.create_table(
        "assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("asset_type", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default=sa.text("'awaiting_upload'"),
            nullable=False,
        ),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("version_id", sa.Text(), nullable=True),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
        sa.Column("duration_seconds", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column("transcript_format", sa.String(length=8), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("validated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "purge_after",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now() + interval '30 days'"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "asset_type IN ('source_video', 'source_transcript')", name="asset_type_valid"
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
        sa.ForeignKeyConstraint(
            ["organization_id", "job_id"],
            ["jobs.organization_id", "jobs.id"],
            name="fk_assets_organization_id_job_id_jobs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_assets"),
        sa.UniqueConstraint("organization_id", "id", name="uq_assets_organization_id_id"),
        sa.UniqueConstraint(
            "organization_id",
            "job_id",
            "asset_type",
            name="uq_assets_organization_id_job_id_asset_type",
        ),
    )
    op.create_index("ix_assets_organization_id_job_id", "assets", ["organization_id", "job_id"])
    op.create_index("ix_assets_purge_after", "assets", ["purge_after"])

    op.create_table(
        "reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(length=20), server_default=sa.text("'open'"), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("scene_count", sa.Integer(), nullable=True),
        sa.Column("approved_scene_count", sa.Integer(), nullable=True),
        sa.Column("rejected_scene_count", sa.Integer(), nullable=True),
        sa.Column("locked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_by_principal_id", sa.Uuid(), nullable=True),
        sa.Column("zero_ad_confirmed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "inactivity_expires_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now() + interval '30 days'"),
            nullable=False,
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
        sa.ForeignKeyConstraint(
            ["completed_by_principal_id"],
            ["principals.id"],
            name="fk_reviews_completed_by_principal_id_principals",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "job_id"],
            ["jobs.organization_id", "jobs.id"],
            name="fk_reviews_organization_id_job_id_jobs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_reviews"),
        sa.UniqueConstraint("organization_id", "id", name="uq_reviews_organization_id_id"),
        sa.UniqueConstraint("organization_id", "job_id", name="uq_reviews_organization_id_job_id"),
        sa.UniqueConstraint(
            "organization_id",
            "job_id",
            "id",
            name="uq_reviews_organization_id_job_id_id",
        ),
    )
    op.create_index("ix_reviews_organization_id_state", "reviews", ["organization_id", "state"])
    op.create_index("ix_reviews_inactivity_expires_at", "reviews", ["inactivity_expires_at"])

    op.create_table(
        "renders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("review_id", sa.Uuid(), nullable=False),
        sa.Column(
            "state", sa.String(length=20), server_default=sa.text("'queued'"), nullable=False
        ),
        sa.Column("fence_token", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("worker_id", sa.String(length=255), nullable=True),
        sa.Column("lease_expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "integrity_manifest", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_renders"),
        sa.UniqueConstraint("organization_id", "id", name="uq_renders_organization_id_id"),
        sa.UniqueConstraint("organization_id", "job_id", name="uq_renders_organization_id_job_id"),
        sa.UniqueConstraint(
            "organization_id", "job_id", "id", name="uq_renders_organization_id_job_id_id"
        ),
        sa.UniqueConstraint(
            "organization_id", "review_id", name="uq_renders_organization_id_review_id"
        ),
    )
    op.create_index("ix_renders_organization_id_state", "renders", ["organization_id", "state"])
    op.create_index("ix_renders_lease_expires_at", "renders", ["lease_expires_at"])

    op.create_table(
        "deliverables",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("render_id", sa.Uuid(), nullable=False),
        sa.Column("format", sa.String(length=12), nullable=False),
        sa.Column(
            "state", sa.String(length=16), server_default=sa.text("'staged'"), nullable=False
        ),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("version_id", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("published_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "purge_after",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now() + interval '90 days'"),
            nullable=False,
        ),
        sa.CheckConstraint("format IN ('mp4', 'mp3', 'srt', 'csv', 'docx')", name="format_valid"),
        sa.CheckConstraint("state IN ('staged', 'published')", name="state_valid"),
        sa.CheckConstraint("size_bytes >= 0", name="size_nonnegative"),
        sa.CheckConstraint("checksum_sha256 ~ '^[a-f0-9]{64}$'", name="checksum_valid"),
        sa.CheckConstraint(
            "((state = 'staged' AND published_at IS NULL) OR "
            "(state = 'published' AND published_at IS NOT NULL))",
            name="publication_consistent",
        ),
        sa.CheckConstraint("purge_after > created_at", name="retention_valid"),
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
        sa.PrimaryKeyConstraint("id", name="pk_deliverables"),
        sa.UniqueConstraint("organization_id", "id", name="uq_deliverables_organization_id_id"),
        sa.UniqueConstraint(
            "organization_id",
            "render_id",
            "format",
            name="uq_deliverables_organization_id_render_id_format",
        ),
    )
    op.create_index(
        "ix_deliverables_organization_id_job_id",
        "deliverables",
        ["organization_id", "job_id"],
    )
    op.create_index("ix_deliverables_purge_after", "deliverables", ["purge_after"])

    op.create_table(
        "job_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("job_version", sa.Integer(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column(
            "occurred_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "available_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("dispatched_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "purge_after",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now() + interval '365 days'"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_type IN ('job.needs_review', 'job.completed', 'job.failed', 'job.cancelled')",
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
        sa.ForeignKeyConstraint(
            ["organization_id", "job_id"],
            ["jobs.organization_id", "jobs.id"],
            name="fk_job_events_organization_id_job_id_jobs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_job_events"),
        sa.UniqueConstraint("organization_id", "id", name="uq_job_events_organization_id_id"),
        sa.UniqueConstraint(
            "organization_id",
            "job_id",
            "event_type",
            name="uq_job_events_organization_id_job_id_event_type",
        ),
    )
    op.create_index(
        "ix_job_events_pending",
        "job_events",
        ["available_at"],
        postgresql_where=sa.text("dispatched_at IS NULL"),
    )
    op.create_index(
        "ix_job_events_organization_id_occurred_at",
        "job_events",
        ["organization_id", "occurred_at"],
    )

    op.create_table(
        "webhook_endpoints",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("endpoint_url", sa.Text(), nullable=False),
        sa.Column("signing_secret_ref", sa.String(length=500), nullable=False),
        sa.Column("secret_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("disabled_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("endpoint_url ~ '^https://[^[:space:]]+$'", name="endpoint_url_https"),
        sa.CheckConstraint("length(signing_secret_ref) > 0", name="secret_ref_nonempty"),
        sa.CheckConstraint("secret_version >= 1", name="secret_version_min"),
        sa.CheckConstraint(
            "((is_active AND disabled_at IS NULL) OR (NOT is_active AND disabled_at IS NOT NULL))",
            name="activation_consistent",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_webhook_endpoints_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_webhook_endpoints"),
        sa.UniqueConstraint("organization_id", name="uq_webhook_endpoints_organization_id"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_webhook_endpoints_organization_id_id"
        ),
    )

    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("endpoint_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column(
            "state", sa.String(length=24), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("lease_expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_status_code", sa.SmallInteger(), nullable=True),
        sa.Column("last_error_code", sa.String(length=80), nullable=True),
        sa.Column("delivered_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
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
        sa.PrimaryKeyConstraint("id", name="pk_webhook_deliveries"),
        sa.UniqueConstraint(
            "organization_id",
            "endpoint_id",
            "event_id",
            name="uq_webhook_deliveries_endpoint_event",
        ),
    )
    op.create_index(
        "ix_webhook_deliveries_due",
        "webhook_deliveries",
        ["next_attempt_at"],
        postgresql_where=sa.text("state IN ('pending', 'retry_scheduled')"),
    )
    op.create_index(
        "ix_webhook_deliveries_organization_id_event_id",
        "webhook_deliveries",
        ["organization_id", "event_id"],
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("actor_principal_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("resource_type", sa.String(length=80), nullable=False),
        sa.Column("resource_id", sa.String(length=200), nullable=True),
        sa.Column("request_id", sa.String(length=200), nullable=True),
        sa.Column("details", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column(
            "occurred_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "purge_after",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now() + interval '365 days'"),
            nullable=False,
        ),
        sa.CheckConstraint("length(action) > 0", name="action_nonempty"),
        sa.CheckConstraint("length(resource_type) > 0", name="resource_type_nonempty"),
        sa.CheckConstraint("jsonb_typeof(details) = 'object'", name="details_object"),
        sa.CheckConstraint("purge_after > occurred_at", name="retention_valid"),
        sa.ForeignKeyConstraint(
            ["actor_principal_id"],
            ["principals.id"],
            name="fk_audit_events_actor_principal_id_principals",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_audit_events_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_events"),
    )
    op.create_index(
        "ix_audit_events_organization_id_occurred_at",
        "audit_events",
        ["organization_id", "occurred_at"],
    )
    op.create_index("ix_audit_events_purge_after", "audit_events", ["purge_after"])

    op.create_table(
        "organization_usage_periods",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column(
            "reserved_media_seconds",
            sa.Numeric(precision=14, scale=3),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "consumed_media_seconds",
            sa.Numeric(precision=14, scale=3),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("period_end > period_start", name="period_valid"),
        sa.CheckConstraint("reserved_media_seconds >= 0", name="reserved_nonnegative"),
        sa.CheckConstraint("consumed_media_seconds >= 0", name="consumed_nonnegative"),
        sa.CheckConstraint("version >= 1", name="version_min"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_organization_usage_periods_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_organization_usage_periods"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_organization_usage_periods_organization_id_id"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "period_start",
            name="uq_organization_usage_periods_org_start",
        ),
    )
    op.create_index(
        "ix_organization_usage_periods_period_end",
        "organization_usage_periods",
        ["period_end"],
    )

    op.create_table(
        "quota_reservations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("usage_period_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column(
            "state", sa.String(length=20), server_default=sa.text("'reserved'"), nullable=False
        ),
        sa.Column("reserved_seconds", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("actual_seconds", sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("finalized_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
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
        sa.PrimaryKeyConstraint("id", name="pk_quota_reservations"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_quota_reservations_organization_id_id"
        ),
        sa.UniqueConstraint(
            "organization_id", "job_id", name="uq_quota_reservations_organization_id_job_id"
        ),
    )
    op.create_index(
        "ix_quota_reservations_organization_id_state",
        "quota_reservations",
        ["organization_id", "state"],
    )
    op.create_index("ix_quota_reservations_expires_at", "quota_reservations", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_quota_reservations_expires_at", table_name="quota_reservations")
    op.drop_index("ix_quota_reservations_organization_id_state", table_name="quota_reservations")
    op.drop_table("quota_reservations")
    op.drop_index(
        "ix_organization_usage_periods_period_end", table_name="organization_usage_periods"
    )
    op.drop_table("organization_usage_periods")
    op.drop_index("ix_audit_events_purge_after", table_name="audit_events")
    op.drop_index("ix_audit_events_organization_id_occurred_at", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_webhook_deliveries_organization_id_event_id", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_due", table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")
    op.drop_table("webhook_endpoints")
    op.drop_index("ix_job_events_organization_id_occurred_at", table_name="job_events")
    op.drop_index("ix_job_events_pending", table_name="job_events")
    op.drop_table("job_events")
    op.drop_index("ix_deliverables_purge_after", table_name="deliverables")
    op.drop_index("ix_deliverables_organization_id_job_id", table_name="deliverables")
    op.drop_table("deliverables")
    op.drop_index("ix_renders_lease_expires_at", table_name="renders")
    op.drop_index("ix_renders_organization_id_state", table_name="renders")
    op.drop_table("renders")
    op.drop_index("ix_reviews_inactivity_expires_at", table_name="reviews")
    op.drop_index("ix_reviews_organization_id_state", table_name="reviews")
    op.drop_table("reviews")
    op.drop_index("ix_assets_purge_after", table_name="assets")
    op.drop_index("ix_assets_organization_id_job_id", table_name="assets")
    op.drop_table("assets")
    op.drop_table("organization_job_capacity")
    op.drop_table("organization_quotas")

    # v0006 had one global active slot. A populated multi-tenant beta may have
    # several active rows that cannot fit that legacy invariant. Preserve the
    # deterministic oldest portfolio job (or oldest job overall) and cancel
    # the rest before recreating the old index; failing halfway through a
    # rollback would be less safe than a durable, explicit cancellation.
    op.execute(
        sa.text(
            "WITH ranked AS ("
            " SELECT id, row_number() OVER ("
            "  ORDER BY (organization_id = :portfolio_id) DESC, created_at, id"
            " ) AS position"
            " FROM jobs WHERE status IN ('PROCESSING', 'QUEUED', 'UPLOAD_COMPLETE')"
            ") UPDATE jobs SET status = 'CANCELLED', "
            "completed_at = coalesce(completed_at, now()), lease_expires_at = NULL, "
            "worker_id = NULL, updated_at = now(), "
            "error_code = 'ROLLBACK_CAPACITY_NORMALIZED', "
            "error_message = 'cancelled during rollback to the legacy global capacity model' "
            "WHERE id IN (SELECT id FROM ranked WHERE position > 1)"
        ).bindparams(portfolio_id=PORTFOLIO_ORGANIZATION_ID)
    )
    op.drop_index("uq_jobs_one_compute_active", table_name="jobs")
    op.drop_index("ix_jobs_organization_id_status_created_at", table_name="jobs")
    op.drop_index("ix_jobs_organization_id_created_at", table_name="jobs")
    op.drop_constraint("fk_jobs_organization_id_project_id_projects", "jobs", type_="foreignkey")
    op.drop_constraint("client_reference_valid", "jobs", type_="check")
    op.drop_constraint("uq_jobs_organization_id_client_reference", "jobs", type_="unique")
    op.drop_constraint("uq_jobs_organization_id_id", "jobs", type_="unique")
    op.drop_column("jobs", "client_reference")
    op.drop_column("jobs", "organization_id")
    op.create_foreign_key(
        "fk_jobs_project_id_projects",
        "jobs",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "uq_jobs_one_compute_active",
        "jobs",
        [sa.text("(true)")],
        unique=True,
        postgresql_where=sa.text(f"status IN ({_ACTIVE})"),
    )

    op.drop_constraint("external_id_valid", "projects", type_="check")
    op.drop_constraint("uq_projects_organization_id_external_id", "projects", type_="unique")
    op.drop_constraint("uq_projects_organization_id_id", "projects", type_="unique")
    op.drop_column("projects", "external_id")
