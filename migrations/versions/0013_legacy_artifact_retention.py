"""Normalize exact-version retention for legacy analysis artifacts.

Revision ID: 0013_legacy_artifact_retention
Revises: 0012_tts_previews
Create Date: 2026-08-28
"""

import uuid

import sqlalchemy as sa
from alembic import op

revision = "0013_legacy_artifact_retention"
down_revision = "0012_tts_previews"
branch_labels = None
depends_on = None

PORTFOLIO_ORGANIZATION_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")


def upgrade() -> None:
    portfolio_default = sa.text(f"'{PORTFOLIO_ORGANIZATION_ID}'::uuid")
    op.add_column(
        "artifacts",
        sa.Column(
            "organization_id",
            sa.Uuid(),
            server_default=portfolio_default,
            nullable=True,
        ),
    )
    op.add_column("artifacts", sa.Column("version_id", sa.Text(), nullable=True))
    op.add_column(
        "artifacts",
        sa.Column(
            "retention_state",
            sa.String(length=16),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
    )
    op.add_column(
        "artifacts",
        sa.Column("purged_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "artifacts",
        sa.Column("purge_after", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    # The Job row is the tenant authority.  For source_video it also carries
    # the independently verified S3 version and therefore recovers old rows
    # even when their JSON metadata predates that duplicate field.  Generated
    # rows are recoverable only from a bounded JSON string; every other shape
    # deliberately remains NULL and is surfaced as an unsafe blocker.
    op.execute(
        "UPDATE artifacts AS a SET "
        "organization_id = j.organization_id, "
        "version_id = CASE "
        "WHEN a.artifact_type = 'source_video' "
        "AND a.object_key = j.input_object_key "
        "AND j.source_version_id IS NOT NULL "
        "AND octet_length(j.source_version_id) BETWEEN 1 AND 1024 "
        "AND btrim(j.source_version_id) <> '' "
        "THEN j.source_version_id "
        "WHEN a.artifact_type <> 'source_video' "
        "AND jsonb_typeof(a.metadata -> 'version_id') = 'string' "
        "AND octet_length(a.metadata ->> 'version_id') BETWEEN 1 AND 1024 "
        "AND btrim(a.metadata ->> 'version_id') <> '' "
        "THEN a.metadata ->> 'version_id' "
        "ELSE NULL END, "
        "purge_after = a.created_at + interval '30 days' "
        "FROM jobs AS j WHERE j.id = a.job_id"
    )
    op.execute("UPDATE artifacts SET retention_state = 'unrecoverable' WHERE version_id IS NULL")
    op.alter_column(
        "artifacts",
        "organization_id",
        existing_type=sa.Uuid(),
        nullable=False,
        server_default=portfolio_default,
    )
    op.alter_column(
        "artifacts",
        "purge_after",
        existing_type=sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("now() + interval '30 days'"),
    )

    op.drop_constraint("fk_artifacts_job_id_jobs", "artifacts", type_="foreignkey")
    op.create_foreign_key(
        "fk_artifacts_organization_id_job_id_jobs",
        "artifacts",
        "jobs",
        ["organization_id", "job_id"],
        ["organization_id", "id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_artifacts_organization_id_id",
        "artifacts",
        ["organization_id", "id"],
    )
    op.create_check_constraint(
        "version_id_valid",
        "artifacts",
        "version_id IS NULL OR (octet_length(version_id) BETWEEN 1 AND 1024 "
        "AND btrim(version_id) <> '')",
    )
    op.create_check_constraint(
        "retention_state_consistent",
        "artifacts",
        "((retention_state = 'active' AND purged_at IS NULL) OR "
        "(retention_state = 'unrecoverable' AND purged_at IS NULL) OR "
        "(retention_state = 'purged' AND version_id IS NOT NULL "
        "AND purged_at IS NOT NULL AND purged_at >= created_at))",
    )
    op.create_check_constraint(
        "retention_valid",
        "artifacts",
        "purge_after > created_at",
    )
    op.create_index(
        "ix_artifacts_organization_id_job_id",
        "artifacts",
        ["organization_id", "job_id"],
    )
    op.create_index("ix_artifacts_purge_after", "artifacts", ["purge_after"])


def downgrade() -> None:
    # Never resurface a row whose exact S3 version was already deleted: the
    # pre-0013 API does not understand retention_state.  Removing only these
    # proven tombstones makes its manifest fail closed.
    op.execute("DELETE FROM artifacts WHERE retention_state IN ('purged', 'unrecoverable')")
    op.drop_index("ix_artifacts_purge_after", table_name="artifacts")
    op.drop_index("ix_artifacts_organization_id_job_id", table_name="artifacts")
    op.drop_constraint("retention_valid", "artifacts", type_="check")
    op.drop_constraint("retention_state_consistent", "artifacts", type_="check")
    op.drop_constraint("version_id_valid", "artifacts", type_="check")
    op.drop_constraint("uq_artifacts_organization_id_id", "artifacts", type_="unique")
    op.drop_constraint(
        "fk_artifacts_organization_id_job_id_jobs",
        "artifacts",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_artifacts_job_id_jobs",
        "artifacts",
        "jobs",
        ["job_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_column("artifacts", "purge_after")
    op.drop_column("artifacts", "purged_at")
    op.drop_column("artifacts", "retention_state")
    op.drop_column("artifacts", "version_id")
    op.drop_column("artifacts", "organization_id")
