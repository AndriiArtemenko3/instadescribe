"""G6: complete the scene-override schema (ADR-0002, G6 Gate 1).

Adds the `locked` flag and makes the database — not only Pydantic — enforce
the override invariants: version floor, bounded playback speed, canonical
scene identifiers. Forward-only; 0001–0003 are unamended.

Revision ID: 0004_override_fields (alembic_version is VARCHAR(32) — keep IDs short)
Revises: 0003_source_enqueue
Create Date: 2026-08-07
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_override_fields"
down_revision = "0003_source_enqueue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scene_overrides",
        sa.Column("locked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    # Base names: the naming convention expands them (ck_scene_overrides_*).
    op.create_check_constraint("version_min", "scene_overrides", "version >= 1")
    op.create_check_constraint(
        "speed_range", "scene_overrides", "speed IS NULL OR (speed >= 0.50 AND speed <= 2.50)"
    )
    op.create_check_constraint(
        "scene_id_canonical", "scene_overrides", "scene_id ~ '^scene_[1-9][0-9]*$'"
    )


def downgrade() -> None:
    op.drop_constraint("scene_id_canonical", "scene_overrides", type_="check")
    op.drop_constraint("speed_range", "scene_overrides", type_="check")
    op.drop_constraint("version_min", "scene_overrides", type_="check")
    op.drop_column("scene_overrides", "locked")
