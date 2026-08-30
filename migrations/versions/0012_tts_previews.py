"""Add durable, fenced per-scene TTS preview requests.

Revision ID: 0012_tts_previews
Revises: 0011_deliverable_tombstone
Create Date: 2026-08-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0012_tts_previews"
down_revision = "0011_deliverable_tombstone"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tts_previews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by_principal_id", sa.Uuid(), nullable=False),
        sa.Column("scene_id", sa.String(length=120), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("voice", sa.String(length=24), nullable=False),
        sa.Column("speed", sa.Numeric(precision=4, scale=2), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "state",
            sa.String(length=20),
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        sa.Column("fence_token", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("attempt_count", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("worker_id", sa.String(length=255), nullable=True),
        sa.Column("lease_expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("object_key", sa.Text(), nullable=True),
        sa.Column("version_id", sa.Text(), nullable=True),
        sa.Column("content_type", sa.String(length=64), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "expires_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now() + interval '24 hours'"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('queued', 'rendering', 'completed', 'failed', 'cancelled')",
            name="state_valid",
        ),
        sa.CheckConstraint("scene_id ~ '^scene_[1-9][0-9]*$'", name="scene_id_canonical"),
        sa.CheckConstraint("char_length(text) BETWEEN 1 AND 2000", name="text_length_valid"),
        sa.CheckConstraint(
            "voice IN ('onyx', 'nova', 'alloy', 'shimmer', 'echo', 'fable')",
            name="voice_valid",
        ),
        sa.CheckConstraint("speed BETWEEN 0.50 AND 2.50", name="speed_valid"),
        sa.CheckConstraint("request_hash ~ '^[a-f0-9]{64}$'", name="request_hash_valid"),
        sa.CheckConstraint("fence_token >= 0", name="fence_token_nonnegative"),
        sa.CheckConstraint("attempt_count BETWEEN 0 AND 3", name="attempt_count_bounded"),
        sa.CheckConstraint("expires_at > created_at", name="expiry_valid"),
        sa.CheckConstraint(
            "((state = 'queued' AND worker_id IS NULL AND lease_expires_at IS NULL "
            "AND started_at IS NULL AND finished_at IS NULL AND object_key IS NULL "
            "AND version_id IS NULL AND content_type IS NULL AND size_bytes IS NULL "
            "AND checksum_sha256 IS NULL AND error_code IS NULL AND error_message IS NULL) OR "
            "(state = 'rendering' AND worker_id IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND started_at IS NOT NULL AND finished_at IS NULL AND object_key IS NULL "
            "AND version_id IS NULL AND content_type IS NULL AND size_bytes IS NULL "
            "AND checksum_sha256 IS NULL AND error_code IS NULL AND error_message IS NULL) OR "
            "(state = 'completed' AND worker_id IS NULL AND lease_expires_at IS NULL "
            "AND started_at IS NOT NULL AND finished_at IS NOT NULL "
            "AND finished_at >= started_at AND object_key IS NOT NULL AND version_id IS NOT NULL "
            "AND content_type = 'audio/mpeg' AND size_bytes > 0 "
            "AND checksum_sha256 IS NOT NULL AND error_code IS NULL AND error_message IS NULL) OR "
            "(state = 'failed' AND worker_id IS NULL AND lease_expires_at IS NULL "
            "AND finished_at IS NOT NULL AND object_key IS NULL AND version_id IS NULL "
            "AND content_type IS NULL AND size_bytes IS NULL AND checksum_sha256 IS NULL "
            "AND error_code IS NOT NULL AND error_message IS NOT NULL) OR "
            "(state = 'cancelled' AND worker_id IS NULL AND lease_expires_at IS NULL "
            "AND finished_at IS NOT NULL AND object_key IS NULL AND version_id IS NULL "
            "AND content_type IS NULL AND size_bytes IS NULL AND checksum_sha256 IS NULL "
            "AND error_code IS NULL AND error_message IS NULL))",
            name="state_payload_consistent",
        ),
        sa.CheckConstraint(
            "checksum_sha256 IS NULL OR checksum_sha256 ~ '^[a-f0-9]{64}$'",
            name="checksum_valid",
        ),
        sa.CheckConstraint(
            "version_id IS NULL OR octet_length(version_id) BETWEEN 1 AND 1024",
            name="version_id_valid",
        ),
        sa.CheckConstraint(
            "object_key IS NULL OR (octet_length(object_key) BETWEEN 1 AND 1024 "
            "AND position('..' in object_key) = 0 AND object_key = "
            "'previews/orgs/' || organization_id::text || '/jobs/' || job_id::text || "
            "'/requests/' || id::text || '/attempts/' || fence_token::text || "
            "'/narration.mp3')",
            name="object_key_exact_identity",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "job_id"],
            ["jobs.organization_id", "jobs.id"],
            name="fk_tts_previews_organization_id_job_id_jobs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_principal_id"],
            ["principals.id"],
            name="fk_tts_previews_requested_by_principal_id_principals",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tts_previews"),
        sa.UniqueConstraint("organization_id", "id", name="uq_tts_previews_organization_id_id"),
        sa.UniqueConstraint(
            "organization_id",
            "job_id",
            "id",
            name="uq_tts_previews_organization_id_job_id_id",
        ),
    )
    op.create_index(
        "ix_tts_previews_organization_state_created",
        "tts_previews",
        ["organization_id", "state", "created_at"],
    )
    op.create_index("ix_tts_previews_expires_at", "tts_previews", ["expires_at"])
    op.create_index(
        "uq_tts_previews_active_scene",
        "tts_previews",
        ["organization_id", "job_id", "scene_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('queued', 'rendering')"),
    )

    op.create_table(
        "tts_preview_artifacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("preview_id", sa.Uuid(), nullable=False),
        sa.Column("fence_token", sa.BigInteger(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("version_id", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("fence_token >= 1", name="fence_token_positive"),
        sa.CheckConstraint(
            "octet_length(version_id) BETWEEN 1 AND 1024",
            name="version_id_valid",
        ),
        sa.CheckConstraint(
            "octet_length(object_key) BETWEEN 1 AND 1024 "
            "AND position('..' in object_key) = 0 AND object_key = "
            "'previews/orgs/' || organization_id::text || '/jobs/' || job_id::text || "
            "'/requests/' || preview_id::text || '/attempts/' || fence_token::text || "
            "'/narration.mp3'",
            name="object_key_exact_attempt_identity",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "job_id", "preview_id"],
            ["tts_previews.organization_id", "tts_previews.job_id", "tts_previews.id"],
            name="fk_tts_preview_artifacts_org_job_preview",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tts_preview_artifacts"),
        sa.UniqueConstraint(
            "organization_id",
            "preview_id",
            "fence_token",
            name="uq_tts_preview_artifacts_preview_fence",
        ),
    )
    op.create_index(
        "ix_tts_preview_artifacts_created_at",
        "tts_preview_artifacts",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_tts_preview_artifacts_created_at", table_name="tts_preview_artifacts")
    op.drop_table("tts_preview_artifacts")
    op.drop_index("uq_tts_previews_active_scene", table_name="tts_previews")
    op.drop_index("ix_tts_previews_expires_at", table_name="tts_previews")
    op.drop_index("ix_tts_previews_organization_state_created", table_name="tts_previews")
    op.drop_table("tts_previews")
