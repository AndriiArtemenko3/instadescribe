"""G4: durable source verification and recoverable-enqueue fields.

source_etag is an opaque object validator (never a checksum);
source_checksum_sha256 is stored only when S3 genuinely supplies one.
enqueue_message_id stays stable across retries for one job — the identity of
the single logical queue message. Forward-only; 0001/0002 are unamended.

Revision ID: 0003_source_enqueue (alembic_version is VARCHAR(32) — keep IDs short)
Revises: 0002_projects_and_provenance
Create Date: 2026-08-06
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_source_enqueue"
down_revision = "0002_projects_and_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("source_etag", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("source_version_id", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("source_checksum_sha256", sa.Text(), nullable=True))
    op.add_column(
        "jobs", sa.Column("upload_verified_at", sa.TIMESTAMP(timezone=True), nullable=True)
    )
    op.add_column("jobs", sa.Column("enqueue_message_id", sa.Uuid(), nullable=True))
    op.add_column(
        "jobs", sa.Column("enqueue_requested_at", sa.TIMESTAMP(timezone=True), nullable=True)
    )
    op.add_column(
        "jobs",
        sa.Column(
            "enqueue_attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
    )
    op.add_column("jobs", sa.Column("enqueued_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.create_check_constraint("enqueue_attempts_nonneg", "jobs", "enqueue_attempt_count >= 0")


def downgrade() -> None:
    # Base name: the naming convention expands it (ck_jobs_enqueue_attempts_nonneg).
    op.drop_constraint("enqueue_attempts_nonneg", "jobs", type_="check")
    op.drop_column("jobs", "enqueued_at")
    op.drop_column("jobs", "enqueue_attempt_count")
    op.drop_column("jobs", "enqueue_requested_at")
    op.drop_column("jobs", "enqueue_message_id")
    op.drop_column("jobs", "upload_verified_at")
    op.drop_column("jobs", "source_checksum_sha256")
    op.drop_column("jobs", "source_version_id")
    op.drop_column("jobs", "source_etag")
