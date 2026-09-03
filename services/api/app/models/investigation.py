"""Tenant-scoped persistence for observable video investigations.

The investigation workflow is deliberately parallel to audio description: it
shares the durable project/job/upload substrate, but owns its evidence,
belief, analyst-decision and provenance records.  Every customer-owned child
contains the organization and job identifiers so PostgreSQL composite foreign
keys prevent cross-tenant attachment even if an application selector is
wrong.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Investigation(Base):
    __tablename__ = "investigations"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    kind: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    connectivity_policy: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        sa.String(32), nullable=False, server_default=sa.text("'awaiting_upload'")
    )
    trace_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
    model_provenance: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=sa.text("'{\"executedLocally\": false}'::jsonb"),
    )
    runtime_provenance: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    final_hypothesis: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    abstained: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("false")
    )
    abstention_reason: Mapped[str | None] = mapped_column(sa.String(500))
    calibrated_confidence: Mapped[Decimal | None] = mapped_column(sa.Numeric(6, 5))
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["organization_id", "job_id"],
            ["jobs.organization_id", "jobs.id"],
            name="fk_investigations_organization_id_job_id_jobs",
            ondelete="CASCADE",
        ),
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
        sa.Index(
            "ix_investigations_organization_id_created_at",
            "organization_id",
            "created_at",
        ),
        sa.Index("ix_investigations_organization_id_status", "organization_id", "status"),
    )


class SourceRecord(Base):
    __tablename__ = "source_records"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    investigation_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    publisher_url: Mapped[str | None] = mapped_column(sa.Text)
    published_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    collected_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )
    legal_basis: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    license_name: Mapped[str | None] = mapped_column(sa.String(200))
    media_sha256: Mapped[str | None] = mapped_column(sa.String(64))
    redistribution_policy: Mapped[str] = mapped_column(sa.String(24), nullable=False)
    retention_days: Mapped[int] = mapped_column(
        sa.SmallInteger, nullable=False, server_default=sa.text("30")
    )
    purge_after: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("now() + interval '30 days'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["organization_id", "job_id", "investigation_id"],
            ["investigations.organization_id", "investigations.job_id", "investigations.id"],
            name="fk_source_records_org_job_investigation",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_source_records_organization_id_id"),
        sa.UniqueConstraint(
            "organization_id",
            "investigation_id",
            name="uq_source_records_organization_id_investigation_id",
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
        sa.Index(
            "ix_source_records_organization_id_investigation_id",
            "organization_id",
            "investigation_id",
        ),
        sa.Index("ix_source_records_purge_after", "purge_after"),
    )


class EvidenceItem(Base):
    __tablename__ = "evidence_items"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    investigation_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    kind: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    observation: Mapped[dict] = mapped_column(JSONB, nullable=False)
    frame_time_ms: Mapped[int | None] = mapped_column(sa.BigInteger)
    bbox: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    polarity: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, server_default=sa.text("'neutral'")
    )
    reliability: Mapped[Decimal] = mapped_column(sa.Numeric(6, 5), nullable=False)
    verification_state: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, server_default=sa.text("'proposed'")
    )
    correlation_group: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["organization_id", "job_id", "investigation_id"],
            ["investigations.organization_id", "investigations.job_id", "investigations.id"],
            name="fk_evidence_items_org_job_investigation",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_evidence_items_organization_id_id"),
        sa.UniqueConstraint(
            "organization_id",
            "investigation_id",
            "id",
            name="uq_evidence_items_organization_id_investigation_id_id",
        ),
        sa.CheckConstraint(
            "kind IN ('keyframe', 'visual', 'ocr', 'audio', 'metadata', 'web', "
            "'geospatial', 'change', 'visualMatch')",
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
        sa.Index(
            "ix_evidence_items_organization_investigation_created",
            "organization_id",
            "investigation_id",
            "created_at",
        ),
    )


class InvestigationStep(Base):
    __tablename__ = "investigation_steps"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    investigation_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    sequence: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    kind: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    tool: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    state: Mapped[str] = mapped_column(
        sa.String(20), nullable=False, server_default=sa.text("'pending'")
    )
    input_evidence_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
    )
    output_evidence_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
    )
    model_digest: Mapped[str | None] = mapped_column(sa.String(64))
    prompt_digest: Mapped[str | None] = mapped_column(sa.String(64))
    latency_ms: Mapped[int | None] = mapped_column(sa.Integer)
    peak_memory_mb: Mapped[int | None] = mapped_column(sa.Integer)
    cost_microunits: Mapped[int | None] = mapped_column(sa.BigInteger)
    policy_decision: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, server_default=sa.text("'not_required'")
    )
    policy_decided_by_principal_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
    policy_decided_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    entropy_before: Mapped[Decimal | None] = mapped_column(sa.Numeric(12, 8))
    entropy_after: Mapped[Decimal | None] = mapped_column(sa.Numeric(12, 8))
    started_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )

    __table_args__ = (
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
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_investigation_steps_organization_id_id"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "investigation_id",
            "sequence",
            name="uq_investigation_steps_org_investigation_sequence",
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
        sa.Index(
            "ix_investigation_steps_organization_investigation_sequence",
            "organization_id",
            "investigation_id",
            "sequence",
        ),
    )


class BeliefSnapshot(Base):
    __tablename__ = "belief_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    investigation_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    sequence: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    candidates: Mapped[list] = mapped_column(JSONB, nullable=False)
    entropy: Mapped[Decimal] = mapped_column(sa.Numeric(12, 8), nullable=False)
    abstained: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["organization_id", "job_id", "investigation_id"],
            ["investigations.organization_id", "investigations.job_id", "investigations.id"],
            name="fk_belief_snapshots_org_job_investigation",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_belief_snapshots_organization_id_id"),
        sa.UniqueConstraint(
            "organization_id",
            "investigation_id",
            "sequence",
            name="uq_belief_snapshots_org_investigation_sequence",
        ),
        sa.CheckConstraint("sequence >= 1", name="sequence_positive"),
        sa.CheckConstraint("jsonb_typeof(candidates) = 'array'", name="candidates_array"),
        sa.CheckConstraint("entropy >= 0", name="entropy_nonnegative"),
        sa.Index(
            "ix_belief_snapshots_organization_investigation_sequence",
            "organization_id",
            "investigation_id",
            "sequence",
        ),
    )


class AnalystDecision(Base):
    __tablename__ = "analyst_decisions"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    investigation_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    decided_by_principal_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    status: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, server_default=sa.text("'final'")
    )
    evidence_decisions: Mapped[list] = mapped_column(JSONB, nullable=False)
    final_hypothesis: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    abstained: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)
    abstention_reason: Mapped[str | None] = mapped_column(sa.String(500))
    notes: Mapped[str | None] = mapped_column(sa.String(2000))
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )

    __table_args__ = (
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
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_analyst_decisions_organization_id_id"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "investigation_id",
            name="uq_analyst_decisions_organization_id_investigation_id",
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
        sa.Index(
            "ix_analyst_decisions_organization_investigation",
            "organization_id",
            "investigation_id",
        ),
    )
