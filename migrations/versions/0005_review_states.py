"""v0.2: explicit scene review state and optimistic-version invariants.

Existing override rows are, by definition, human edits to the immutable
generated scenes artifact, so the populated upgrade backfills them as
``edited``. ``generated`` exists only as the absence of an override row; it
is used transiently while adding the non-null column, then the default is
dropped and the persisted-state constraint excludes it.

Revision ID: 0005_review_states
Revises: 0004_override_fields
Create Date: 2026-08-10
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_review_states"
down_revision = "0004_override_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scene_overrides",
        sa.Column(
            "review_status",
            sa.String(length=20),
            server_default=sa.text("'generated'"),
            nullable=False,
        ),
    )
    op.add_column(
        "scene_overrides",
        sa.Column("reviewed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    # Rows that predate explicit review state exist only because a human
    # changed a generated scene, so ``edited`` is the honest backfill.
    op.execute("UPDATE scene_overrides SET review_status = 'edited'")
    op.alter_column("scene_overrides", "review_status", server_default=None)
    op.create_check_constraint(
        "review_status_valid",
        "scene_overrides",
        "review_status IN ('edited', 'approved', 'rejected')",
    )
    op.create_check_constraint(
        "review_timestamp_consistent",
        "scene_overrides",
        "((review_status IN ('approved', 'rejected') AND reviewed_at IS NOT NULL) "
        "OR (review_status = 'edited' AND reviewed_at IS NULL))",
    )
    op.create_check_constraint("version_min", "projects", "version >= 1")


def downgrade() -> None:
    op.drop_constraint("version_min", "projects", type_="check")
    op.drop_constraint("review_timestamp_consistent", "scene_overrides", type_="check")
    op.drop_constraint("review_status_valid", "scene_overrides", type_="check")
    op.drop_column("scene_overrides", "reviewed_at")
    op.drop_column("scene_overrides", "review_status")
