"""Add exact-version render-attempt cleanup journal.

Revision ID: 0010_render_artifact_journal
Revises: 0009_owner_invitations
Create Date: 2026-08-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0010_render_artifact_journal"
down_revision = "0009_owner_invitations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "render_attempt_artifacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("render_id", sa.Uuid(), nullable=False),
        sa.Column("fence_token", sa.BigInteger(), nullable=False),
        sa.Column("format", sa.String(length=12), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("version_id", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
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
        sa.ForeignKeyConstraint(
            ["organization_id", "job_id", "render_id"],
            ["renders.organization_id", "renders.job_id", "renders.id"],
            name="fk_render_attempt_artifacts_org_job_render",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_render_attempt_artifacts"),
        sa.UniqueConstraint(
            "organization_id",
            "render_id",
            "fence_token",
            "format",
            name="uq_render_attempt_artifacts_render_fence_format",
        ),
    )
    op.create_index(
        "ix_render_attempt_artifacts_render_fence",
        "render_attempt_artifacts",
        ["organization_id", "render_id", "fence_token"],
    )
    op.create_index(
        "ix_render_attempt_artifacts_created_at",
        "render_attempt_artifacts",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_render_attempt_artifacts_created_at",
        table_name="render_attempt_artifacts",
    )
    op.drop_index(
        "ix_render_attempt_artifacts_render_fence",
        table_name="render_attempt_artifacts",
    )
    op.drop_table("render_attempt_artifacts")
