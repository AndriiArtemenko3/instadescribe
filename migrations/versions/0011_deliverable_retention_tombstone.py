"""Preserve deliverable metadata after exact-version retention purge.

Revision ID: 0011_deliverable_tombstone
Revises: 0010_render_artifact_journal
Create Date: 2026-08-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0011_deliverable_tombstone"
down_revision = "0010_render_artifact_journal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "deliverables",
        sa.Column("purged_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.drop_constraint("state_valid", "deliverables", type_="check")
    op.drop_constraint("publication_consistent", "deliverables", type_="check")
    op.create_check_constraint(
        "state_valid",
        "deliverables",
        "state IN ('staged', 'published', 'purged')",
    )
    op.create_check_constraint(
        "publication_consistent",
        "deliverables",
        "((state = 'staged' AND published_at IS NULL AND purged_at IS NULL) OR "
        "(state = 'published' AND published_at IS NOT NULL AND purged_at IS NULL) OR "
        "(state = 'purged' AND published_at IS NOT NULL AND purged_at IS NOT NULL "
        "AND purged_at >= published_at))",
    )


def downgrade() -> None:
    # A purged object cannot truthfully become publicly published again.
    # Preserve its metadata as an internal staged row during an explicit
    # rollback of this feature instead of resurfacing a missing S3 version.
    op.execute(
        "UPDATE deliverables SET state = 'staged', published_at = NULL, "
        "purged_at = NULL WHERE state = 'purged'"
    )
    op.drop_constraint("publication_consistent", "deliverables", type_="check")
    op.drop_constraint("state_valid", "deliverables", type_="check")
    op.create_check_constraint(
        "state_valid",
        "deliverables",
        "state IN ('staged', 'published')",
    )
    op.create_check_constraint(
        "publication_consistent",
        "deliverables",
        "((state = 'staged' AND published_at IS NULL) OR "
        "(state = 'published' AND published_at IS NOT NULL))",
    )
    op.drop_column("deliverables", "purged_at")
