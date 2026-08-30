"""G2 core tables: jobs, artifacts, scene_overrides.

Revision ID: 0001_core_tables
Revises: -
Create Date: 2026-08-06
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001_core_tables"
down_revision = None
branch_labels = None
depends_on = None

_STATUSES = (
    "'AWAITING_UPLOAD', 'UPLOAD_COMPLETE', 'QUEUED', 'PROCESSING', 'READY_FOR_REVIEW', "
    "'EXPORT_QUEUED', 'EXPORTING', 'COMPLETED', 'FAILED', 'CANCELLED'"
)
_ACTIVE = "'PROCESSING', 'QUEUED', 'UPLOAD_COMPLETE'"


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("stage", sa.String(length=80), nullable=True),
        sa.Column("progress", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("settings", JSONB(), nullable=False),
        sa.Column("input_object_key", sa.Text(), nullable=True),
        sa.Column("input_content_type", sa.Text(), nullable=True),
        sa.Column("input_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("duration_secs", sa.Numeric(), nullable=True),
        sa.Column("provider", sa.String(length=40), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("3"), nullable=False),
        sa.Column("enqueue_failed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("enqueue_error", sa.Text(), nullable=True),
        sa.Column("lease_expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("worker_id", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("starred", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        # Base names: the metadata naming convention expands these to
        # ck_jobs_status_valid / ck_jobs_progress_range (matching the ORM).
        sa.CheckConstraint(f"status IN ({_STATUSES})", name="status_valid"),
        sa.CheckConstraint("progress >= 0 AND progress <= 100", name="progress_range"),
        sa.PrimaryKeyConstraint("id", name="pk_jobs"),
    )
    op.create_index("ix_jobs_status_created_at", "jobs", ["status", "created_at"])
    op.create_index("ix_jobs_updated_at", "jobs", ["updated_at"])
    op.create_index(
        "uq_jobs_one_compute_active",
        "jobs",
        [sa.text("(true)")],
        unique=True,
        postgresql_where=sa.text(f"status IN ({_ACTIVE})"),
    )

    op.create_table(
        "artifacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_type", sa.String(length=60), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("checksum_sha256", sa.Text(), nullable=True),
        sa.Column("metadata", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name="fk_artifacts_job_id_jobs", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_artifacts"),
        sa.UniqueConstraint("job_id", "artifact_type", name="uq_artifacts_job_id_artifact_type"),
    )
    op.create_index("ix_artifacts_job_id", "artifacts", ["job_id"])

    op.create_table(
        "scene_overrides",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("scene_id", sa.String(length=120), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("voice", sa.String(length=80), nullable=True),
        sa.Column("speed", sa.Numeric(precision=4, scale=2), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name="fk_scene_overrides_job_id_jobs", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_scene_overrides"),
        sa.UniqueConstraint("job_id", "scene_id", name="uq_scene_overrides_job_id_scene_id"),
    )
    op.create_index("ix_scene_overrides_job_id", "scene_overrides", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_scene_overrides_job_id", table_name="scene_overrides")
    op.drop_table("scene_overrides")
    op.drop_index("ix_artifacts_job_id", table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_index("uq_jobs_one_compute_active", table_name="jobs")
    op.drop_index("ix_jobs_updated_at", table_name="jobs")
    op.drop_index("ix_jobs_status_created_at", table_name="jobs")
    op.drop_table("jobs")
