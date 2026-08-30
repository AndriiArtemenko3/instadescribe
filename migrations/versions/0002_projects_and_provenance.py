"""G2.5: durable projects, processing-job FK, pipeline provenance (ADR-0008).

Forward-only relative to 0001 (which is never amended). Safely backfills a
populated database: one project per existing job reusing the job UUID (a
deterministic, collision-safe mapping since job ids are unique), copies
name/starred/timestamps, sets the honest sentinel 'unknown-pre-g3' for legacy
pipeline_revision values, then removes the authoritative name/starred columns
from jobs. Downgrade restores a valid 0001 shape with values copied back.

Revision ID: 0002_projects_and_provenance
Revises: 0001_core_tables
Create Date: 2026-08-06
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_projects_and_provenance"
down_revision = "0001_core_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("starred", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_projects"),
    )
    op.create_index("ix_projects_updated_at", "projects", ["updated_at"])

    op.add_column("jobs", sa.Column("project_id", sa.Uuid(), nullable=True))
    op.add_column("jobs", sa.Column("pipeline_revision", sa.String(length=120), nullable=True))

    # Backfill: one project per existing job, reusing the job UUID.
    op.execute(
        "INSERT INTO projects (id, name, starred, version, created_at, updated_at) "
        "SELECT id, name, starred, 1, created_at, updated_at FROM jobs"
    )
    op.execute("UPDATE jobs SET project_id = id, pipeline_revision = 'unknown-pre-g3'")

    op.alter_column("jobs", "project_id", nullable=False)
    op.alter_column("jobs", "pipeline_revision", nullable=False)
    op.create_foreign_key(
        "fk_jobs_project_id_projects",
        "jobs",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_jobs_project_id_created_at", "jobs", ["project_id", "created_at"])

    op.drop_column("jobs", "starred")
    op.drop_column("jobs", "name")


def downgrade() -> None:
    op.add_column("jobs", sa.Column("name", sa.String(length=200), nullable=True))
    op.add_column(
        "jobs", sa.Column("starred", sa.Boolean(), server_default=sa.text("false"), nullable=True)
    )
    op.execute(
        "UPDATE jobs SET name = p.name, starred = p.starred "
        "FROM projects p WHERE jobs.project_id = p.id"
    )
    op.alter_column("jobs", "name", nullable=False)
    op.alter_column("jobs", "starred", nullable=False)

    op.drop_index("ix_jobs_project_id_created_at", table_name="jobs")
    op.drop_constraint("fk_jobs_project_id_projects", "jobs", type_="foreignkey")
    op.drop_column("jobs", "pipeline_revision")
    op.drop_column("jobs", "project_id")
    op.drop_index("ix_projects_updated_at", table_name="projects")
    op.drop_table("projects")
