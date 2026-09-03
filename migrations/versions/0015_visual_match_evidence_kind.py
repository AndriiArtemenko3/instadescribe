"""Allow the visualMatch evidence kind on evidence_items.

Geometric verification (local feature matching plus a RANSAC homography)
produces evidence of kind ``visualMatch``, which the original allowlist did not
carry. The kind is added alongside the existing values rather than replacing
any of them: ``visual`` keeps its meaning (a visual observation of a frame),
while ``visualMatch`` records a verified correspondence between a query frame
and a reference candidate. Nothing about scoring, correlation or belief
aggregation changes here — this migration only widens what may be stored.

Revision ID: 0015_visual_match_evidence_kind
Revises: 0014_video_investigations
Create Date: 2026-09-03
"""

from alembic import op

revision = "0015_visual_match_evidence_kind"
down_revision = "0014_video_investigations"
branch_labels = None
depends_on = None

_CONSTRAINT = "kind_valid"
_TABLE = "evidence_items"

_KINDS_BEFORE = (
    "keyframe",
    "visual",
    "ocr",
    "audio",
    "metadata",
    "web",
    "geospatial",
    "change",
)
_KINDS_AFTER = (*_KINDS_BEFORE, "visualMatch")


def _kind_check(kinds: tuple[str, ...]) -> str:
    values = ", ".join(f"'{kind}'" for kind in kinds)
    return f"kind IN ({values})"


def upgrade() -> None:
    # Postgres cannot widen a CHECK in place; drop and recreate with the
    # superset so existing rows (all of which use the previous kinds) stay valid.
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _kind_check(_KINDS_AFTER))


def downgrade() -> None:
    # Rows written with the new kind would violate the narrower constraint, so
    # remove them before restoring it; this is the only way the older schema can
    # accept the table back.
    op.execute(f"DELETE FROM {_TABLE} WHERE kind = 'visualMatch'")
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _kind_check(_KINDS_BEFORE))
