"""Add the tenant-scoped observable video investigation domain.

Revision ID: 0014_video_investigations
Revises: 0013_legacy_artifact_retention
Create Date: 2026-08-30
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0014_video_investigations"
down_revision = "0013_legacy_artifact_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column(
            "workflow_kind",
            sa.String(length=40),
            server_default=sa.text("'audio_description'"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "workflow_kind_valid",
        "jobs",
        "workflow_kind IN ('audio_description', 'video_investigation')",
    )

    op.create_table(
        "investigations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("connectivity_policy", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'awaiting_upload'"),
            nullable=False,
        ),
        sa.Column("trace_id", sa.Uuid(), nullable=True),
        sa.Column(
            "model_provenance",
            JSONB(),
            server_default=sa.text("'{\"executedLocally\": false}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "runtime_provenance",
            JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("final_hypothesis", JSONB(), nullable=True),
        sa.Column("abstained", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("abstention_reason", sa.String(length=500), nullable=True),
        sa.Column("calibrated_confidence", sa.Numeric(precision=6, scale=5), nullable=True),
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
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('geolocate_provenance', 'damage_change')", name="kind_valid"),
        sa.CheckConstraint(
            "connectivity_policy IN ('local', 'text_only', 'approved_crops', 'connected')",
            name="connectivity_policy_valid",
        ),
        sa.CheckConstraint(
            "status IN ('awaiting_upload', 'queued', 'preprocessing', 'investigating', "
            "'needs_review', 'completed', 'failed', 'cancelled')",
            name="status_valid",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(model_provenance) = 'object'", name="model_provenance_object"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(runtime_provenance) = 'object'", name="runtime_provenance_object"
        ),
        sa.CheckConstraint(
            "final_hypothesis IS NULL OR jsonb_typeof(final_hypothesis) = 'object'",
            name="final_hypothesis_object",
        ),
        sa.CheckConstraint(
            "calibrated_confidence IS NULL OR calibrated_confidence BETWEEN 0 AND 1",
            name="confidence_range",
        ),
        sa.CheckConstraint(
            "((status = 'completed' AND completed_at IS NOT NULL) OR "
            "(status <> 'completed' AND completed_at IS NULL))",
            name="completion_timestamp_consistent",
        ),
        sa.CheckConstraint(
            "((abstained AND final_hypothesis IS NULL AND abstention_reason IS NOT NULL) OR "
            "(NOT abstained AND abstention_reason IS NULL))",
            name="abstention_consistent",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "job_id"],
            ["jobs.organization_id", "jobs.id"],
            name="fk_investigations_organization_id_job_id_jobs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_investigations"),
        sa.UniqueConstraint("organization_id", "id", name="uq_investigations_organization_id_id"),
        sa.UniqueConstraint(
            "organization_id", "job_id", name="uq_investigations_organization_id_job_id"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "job_id",
            "id",
            name="uq_investigations_organization_id_job_id_id",
        ),
    )
    op.create_index(
        "ix_investigations_organization_id_created_at",
        "investigations",
        ["organization_id", "created_at"],
    )
    op.create_index(
        "ix_investigations_organization_id_status",
        "investigations",
        ["organization_id", "status"],
    )

    op.create_table(
        "source_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("investigation_id", sa.Uuid(), nullable=False),
        sa.Column("publisher_url", sa.Text(), nullable=True),
        sa.Column("published_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "collected_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("legal_basis", sa.String(length=32), nullable=False),
        sa.Column("license_name", sa.String(length=200), nullable=True),
        sa.Column("media_sha256", sa.String(length=64), nullable=True),
        sa.Column("redistribution_policy", sa.String(length=24), nullable=False),
        sa.Column(
            "retention_days", sa.SmallInteger(), server_default=sa.text("30"), nullable=False
        ),
        sa.Column(
            "purge_after",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now() + interval '30 days'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "publisher_url IS NULL OR publisher_url ~ '^https://[^[:space:]]+$'",
            name="publisher_url_https",
        ),
        sa.CheckConstraint(
            "legal_basis IN ('public_domain', 'licensed', 'consent', 'analyst_authorized')",
            name="legal_basis_valid",
        ),
        sa.CheckConstraint(
            "redistribution_policy IN ('prohibited', 'metadata_only', 'permitted')",
            name="redistribution_policy_valid",
        ),
        sa.CheckConstraint(
            "media_sha256 IS NULL OR media_sha256 ~ '^[a-f0-9]{64}$'",
            name="media_sha256_valid",
        ),
        sa.CheckConstraint("retention_days BETWEEN 1 AND 30", name="retention_days_valid"),
        sa.CheckConstraint("purge_after > collected_at", name="retention_valid"),
        sa.ForeignKeyConstraint(
            ["organization_id", "job_id", "investigation_id"],
            ["investigations.organization_id", "investigations.job_id", "investigations.id"],
            name="fk_source_records_org_job_investigation",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_source_records"),
        sa.UniqueConstraint("organization_id", "id", name="uq_source_records_organization_id_id"),
        sa.UniqueConstraint(
            "organization_id",
            "investigation_id",
            name="uq_source_records_organization_id_investigation_id",
        ),
    )
    op.create_index(
        "ix_source_records_organization_id_investigation_id",
        "source_records",
        ["organization_id", "investigation_id"],
    )
    op.create_index("ix_source_records_purge_after", "source_records", ["purge_after"])

    op.create_table(
        "evidence_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("investigation_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("observation", JSONB(), nullable=False),
        sa.Column("frame_time_ms", sa.BigInteger(), nullable=True),
        sa.Column("bbox", JSONB(), nullable=True),
        sa.Column(
            "polarity", sa.String(length=16), server_default=sa.text("'neutral'"), nullable=False
        ),
        sa.Column("reliability", sa.Numeric(precision=6, scale=5), nullable=False),
        sa.Column(
            "verification_state",
            sa.String(length=16),
            server_default=sa.text("'proposed'"),
            nullable=False,
        ),
        sa.Column("correlation_group", sa.String(length=120), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('keyframe', 'visual', 'ocr', 'audio', 'metadata', 'web', "
            "'geospatial', 'change')",
            name="kind_valid",
        ),
        sa.CheckConstraint("jsonb_typeof(observation) = 'object'", name="observation_object"),
        sa.CheckConstraint("bbox IS NULL OR jsonb_typeof(bbox) = 'object'", name="bbox_object"),
        sa.CheckConstraint("frame_time_ms IS NULL OR frame_time_ms >= 0", name="frame_time_valid"),
        sa.CheckConstraint(
            "polarity IN ('supports', 'contradicts', 'neutral')", name="polarity_valid"
        ),
        sa.CheckConstraint("reliability BETWEEN 0 AND 1", name="reliability_range"),
        sa.CheckConstraint(
            "verification_state IN ('proposed', 'verified', 'rejected')",
            name="verification_state_valid",
        ),
        sa.CheckConstraint(
            "length(btrim(correlation_group)) BETWEEN 1 AND 120",
            name="correlation_group_valid",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "job_id", "investigation_id"],
            ["investigations.organization_id", "investigations.job_id", "investigations.id"],
            name="fk_evidence_items_org_job_investigation",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_evidence_items"),
        sa.UniqueConstraint("organization_id", "id", name="uq_evidence_items_organization_id_id"),
        sa.UniqueConstraint(
            "organization_id",
            "investigation_id",
            "id",
            name="uq_evidence_items_organization_id_investigation_id_id",
        ),
    )
    op.create_index(
        "ix_evidence_items_organization_investigation_created",
        "evidence_items",
        ["organization_id", "investigation_id", "created_at"],
    )

    op.create_table(
        "investigation_steps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("investigation_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("tool", sa.String(length=120), nullable=False),
        sa.Column(
            "state", sa.String(length=20), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column(
            "input_evidence_ids",
            JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "output_evidence_ids",
            JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("model_digest", sa.String(length=64), nullable=True),
        sa.Column("prompt_digest", sa.String(length=64), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("peak_memory_mb", sa.Integer(), nullable=True),
        sa.Column("cost_microunits", sa.BigInteger(), nullable=True),
        sa.Column(
            "policy_decision",
            sa.String(length=16),
            server_default=sa.text("'not_required'"),
            nullable=False,
        ),
        sa.Column("policy_decided_by_principal_id", sa.Uuid(), nullable=True),
        sa.Column("policy_decided_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("entropy_before", sa.Numeric(precision=12, scale=8), nullable=True),
        sa.Column("entropy_after", sa.Numeric(precision=12, scale=8), nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("sequence >= 1", name="sequence_positive"),
        sa.CheckConstraint("length(btrim(kind)) BETWEEN 1 AND 40", name="kind_valid"),
        sa.CheckConstraint("length(btrim(tool)) BETWEEN 1 AND 120", name="tool_valid"),
        sa.CheckConstraint(
            "state IN ('pending', 'running', 'completed', 'failed', 'approved', 'rejected')",
            name="state_valid",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(input_evidence_ids) = 'array' AND "
            "jsonb_typeof(output_evidence_ids) = 'array'",
            name="evidence_ids_arrays",
        ),
        sa.CheckConstraint(
            "model_digest IS NULL OR model_digest ~ '^[a-f0-9]{64}$'",
            name="model_digest_valid",
        ),
        sa.CheckConstraint(
            "prompt_digest IS NULL OR prompt_digest ~ '^[a-f0-9]{64}$'",
            name="prompt_digest_valid",
        ),
        sa.CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="latency_nonnegative"),
        sa.CheckConstraint(
            "peak_memory_mb IS NULL OR peak_memory_mb >= 0", name="memory_nonnegative"
        ),
        sa.CheckConstraint(
            "cost_microunits IS NULL OR cost_microunits >= 0", name="cost_nonnegative"
        ),
        sa.CheckConstraint(
            "policy_decision IN ('pending', 'approved', 'rejected', 'not_required')",
            name="policy_decision_valid",
        ),
        sa.CheckConstraint(
            "((policy_decision IN ('approved', 'rejected') AND "
            "policy_decided_by_principal_id IS NOT NULL AND policy_decided_at IS NOT NULL) OR "
            "(policy_decision IN ('pending', 'not_required') AND "
            "policy_decided_by_principal_id IS NULL AND policy_decided_at IS NULL))",
            name="policy_decision_consistent",
        ),
        sa.CheckConstraint(
            "entropy_before IS NULL OR entropy_before >= 0", name="entropy_before_nonnegative"
        ),
        sa.CheckConstraint(
            "entropy_after IS NULL OR entropy_after >= 0", name="entropy_after_nonnegative"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "job_id", "investigation_id"],
            ["investigations.organization_id", "investigations.job_id", "investigations.id"],
            name="fk_investigation_steps_org_job_investigation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "policy_decided_by_principal_id"],
            [
                "organization_memberships.organization_id",
                "organization_memberships.principal_id",
            ],
            name="fk_investigation_steps_policy_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_investigation_steps"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_investigation_steps_organization_id_id"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "investigation_id",
            "sequence",
            name="uq_investigation_steps_org_investigation_sequence",
        ),
    )
    op.create_index(
        "ix_investigation_steps_organization_investigation_sequence",
        "investigation_steps",
        ["organization_id", "investigation_id", "sequence"],
    )

    op.create_table(
        "belief_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("investigation_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("candidates", JSONB(), nullable=False),
        sa.Column("entropy", sa.Numeric(precision=12, scale=8), nullable=False),
        sa.Column("abstained", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("sequence >= 1", name="sequence_positive"),
        sa.CheckConstraint("jsonb_typeof(candidates) = 'array'", name="candidates_array"),
        sa.CheckConstraint("entropy >= 0", name="entropy_nonnegative"),
        sa.ForeignKeyConstraint(
            ["organization_id", "job_id", "investigation_id"],
            ["investigations.organization_id", "investigations.job_id", "investigations.id"],
            name="fk_belief_snapshots_org_job_investigation",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_belief_snapshots"),
        sa.UniqueConstraint("organization_id", "id", name="uq_belief_snapshots_organization_id_id"),
        sa.UniqueConstraint(
            "organization_id",
            "investigation_id",
            "sequence",
            name="uq_belief_snapshots_org_investigation_sequence",
        ),
    )
    op.create_index(
        "ix_belief_snapshots_organization_investigation_sequence",
        "belief_snapshots",
        ["organization_id", "investigation_id", "sequence"],
    )

    op.create_table(
        "analyst_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("investigation_id", sa.Uuid(), nullable=False),
        sa.Column("decided_by_principal_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status", sa.String(length=16), server_default=sa.text("'final'"), nullable=False
        ),
        sa.Column("evidence_decisions", JSONB(), nullable=False),
        sa.Column("final_hypothesis", JSONB(), nullable=True),
        sa.Column("abstained", sa.Boolean(), nullable=False),
        sa.Column("abstention_reason", sa.String(length=500), nullable=True),
        sa.Column("notes", sa.String(length=2000), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("status = 'final'", name="status_valid"),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_decisions) = 'array'", name="evidence_decisions_array"
        ),
        sa.CheckConstraint(
            "final_hypothesis IS NULL OR jsonb_typeof(final_hypothesis) = 'object'",
            name="final_hypothesis_object",
        ),
        sa.CheckConstraint(
            "((abstained AND final_hypothesis IS NULL AND abstention_reason IS NOT NULL) OR "
            "(NOT abstained AND final_hypothesis IS NOT NULL AND abstention_reason IS NULL))",
            name="outcome_consistent",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "job_id", "investigation_id"],
            ["investigations.organization_id", "investigations.job_id", "investigations.id"],
            name="fk_analyst_decisions_org_job_investigation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "decided_by_principal_id"],
            [
                "organization_memberships.organization_id",
                "organization_memberships.principal_id",
            ],
            name="fk_analyst_decisions_decider_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_analyst_decisions"),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_analyst_decisions_organization_id_id"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "investigation_id",
            name="uq_analyst_decisions_organization_id_investigation_id",
        ),
    )
    op.create_index(
        "ix_analyst_decisions_organization_investigation",
        "analyst_decisions",
        ["organization_id", "investigation_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_analyst_decisions_organization_investigation", table_name="analyst_decisions")
    op.drop_table("analyst_decisions")
    op.drop_index(
        "ix_belief_snapshots_organization_investigation_sequence",
        table_name="belief_snapshots",
    )
    op.drop_table("belief_snapshots")
    op.drop_index(
        "ix_investigation_steps_organization_investigation_sequence",
        table_name="investigation_steps",
    )
    op.drop_table("investigation_steps")
    op.drop_index(
        "ix_evidence_items_organization_investigation_created", table_name="evidence_items"
    )
    op.drop_table("evidence_items")
    op.drop_index("ix_source_records_purge_after", table_name="source_records")
    op.drop_index("ix_source_records_organization_id_investigation_id", table_name="source_records")
    op.drop_table("source_records")
    op.drop_index("ix_investigations_organization_id_status", table_name="investigations")
    op.drop_index("ix_investigations_organization_id_created_at", table_name="investigations")
    op.drop_table("investigations")
    op.drop_constraint("workflow_kind_valid", "jobs", type_="check")
    op.drop_column("jobs", "workflow_kind")
