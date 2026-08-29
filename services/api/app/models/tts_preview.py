"""Durable, tenant-scoped per-scene TTS preview persistence."""

import uuid
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TtsPreview(Base):
    """One bounded asynchronous preview request and its published S3 version."""

    __tablename__ = "tts_previews"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    requested_by_principal_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("principals.id", ondelete="RESTRICT"),
        nullable=False,
    )
    scene_id: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    voice: Mapped[str] = mapped_column(sa.String(24), nullable=False)
    speed: Mapped[Decimal] = mapped_column(sa.Numeric(4, 2), nullable=False)
    request_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        sa.String(20), nullable=False, server_default=sa.text("'queued'")
    )
    fence_token: Mapped[int] = mapped_column(
        sa.BigInteger, nullable=False, server_default=sa.text("0")
    )
    attempt_count: Mapped[int] = mapped_column(
        sa.SmallInteger, nullable=False, server_default=sa.text("0")
    )
    worker_id: Mapped[str | None] = mapped_column(sa.String(255))
    lease_expires_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    object_key: Mapped[str | None] = mapped_column(sa.Text)
    version_id: Mapped[str | None] = mapped_column(sa.Text)
    content_type: Mapped[str | None] = mapped_column(sa.String(64))
    size_bytes: Mapped[int | None] = mapped_column(sa.BigInteger)
    checksum_sha256: Mapped[str | None] = mapped_column(sa.String(64))
    error_code: Mapped[str | None] = mapped_column(sa.String(80))
    error_message: Mapped[str | None] = mapped_column(sa.String(200))
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )
    started_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("now() + interval '24 hours'"),
    )

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["organization_id", "job_id"],
            ["jobs.organization_id", "jobs.id"],
            name="fk_tts_previews_organization_id_job_id_jobs",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_tts_previews_organization_id_id"),
        sa.UniqueConstraint(
            "organization_id",
            "job_id",
            "id",
            name="uq_tts_previews_organization_id_job_id_id",
        ),
        sa.CheckConstraint(
            "state IN ('queued', 'rendering', 'completed', 'failed', 'cancelled')",
            name="state_valid",
        ),
        sa.CheckConstraint("scene_id ~ '^scene_[1-9][0-9]*$'", name="scene_id_canonical"),
        sa.CheckConstraint("char_length(text) BETWEEN 1 AND 2000", name="text_length_valid"),
        sa.CheckConstraint(
            "voice IN ('onyx', 'nova', 'alloy', 'shimmer', 'echo', 'fable')",
            name="voice_valid",
        ),
        sa.CheckConstraint("speed BETWEEN 0.50 AND 2.50", name="speed_valid"),
        sa.CheckConstraint("request_hash ~ '^[a-f0-9]{64}$'", name="request_hash_valid"),
        sa.CheckConstraint("fence_token >= 0", name="fence_token_nonnegative"),
        sa.CheckConstraint("attempt_count BETWEEN 0 AND 3", name="attempt_count_bounded"),
        sa.CheckConstraint("expires_at > created_at", name="expiry_valid"),
        sa.CheckConstraint(
            "((state = 'queued' AND worker_id IS NULL AND lease_expires_at IS NULL "
            "AND started_at IS NULL AND finished_at IS NULL AND object_key IS NULL "
            "AND version_id IS NULL AND content_type IS NULL AND size_bytes IS NULL "
            "AND checksum_sha256 IS NULL AND error_code IS NULL AND error_message IS NULL) OR "
            "(state = 'rendering' AND worker_id IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND started_at IS NOT NULL AND finished_at IS NULL AND object_key IS NULL "
            "AND version_id IS NULL AND content_type IS NULL AND size_bytes IS NULL "
            "AND checksum_sha256 IS NULL AND error_code IS NULL AND error_message IS NULL) OR "
            "(state = 'completed' AND worker_id IS NULL AND lease_expires_at IS NULL "
            "AND started_at IS NOT NULL AND finished_at IS NOT NULL "
            "AND finished_at >= started_at AND object_key IS NOT NULL AND version_id IS NOT NULL "
            "AND content_type = 'audio/mpeg' AND size_bytes > 0 "
            "AND checksum_sha256 IS NOT NULL AND error_code IS NULL AND error_message IS NULL) OR "
            "(state = 'failed' AND worker_id IS NULL AND lease_expires_at IS NULL "
            "AND finished_at IS NOT NULL AND object_key IS NULL AND version_id IS NULL "
            "AND content_type IS NULL AND size_bytes IS NULL AND checksum_sha256 IS NULL "
            "AND error_code IS NOT NULL AND error_message IS NOT NULL) OR "
            "(state = 'cancelled' AND worker_id IS NULL AND lease_expires_at IS NULL "
            "AND finished_at IS NOT NULL AND object_key IS NULL AND version_id IS NULL "
            "AND content_type IS NULL AND size_bytes IS NULL AND checksum_sha256 IS NULL "
            "AND error_code IS NULL AND error_message IS NULL))",
            name="state_payload_consistent",
        ),
        sa.CheckConstraint(
            "checksum_sha256 IS NULL OR checksum_sha256 ~ '^[a-f0-9]{64}$'",
            name="checksum_valid",
        ),
        sa.CheckConstraint(
            "version_id IS NULL OR octet_length(version_id) BETWEEN 1 AND 1024",
            name="version_id_valid",
        ),
        sa.CheckConstraint(
            "object_key IS NULL OR (octet_length(object_key) BETWEEN 1 AND 1024 "
            "AND position('..' in object_key) = 0 AND object_key = "
            "'previews/orgs/' || organization_id::text || '/jobs/' || job_id::text || "
            "'/requests/' || id::text || '/attempts/' || fence_token::text || "
            "'/narration.mp3')",
            name="object_key_exact_identity",
        ),
        sa.Index(
            "ix_tts_previews_organization_state_created",
            "organization_id",
            "state",
            "created_at",
        ),
        sa.Index("ix_tts_previews_expires_at", "expires_at"),
        sa.Index(
            "uq_tts_previews_active_scene",
            "organization_id",
            "job_id",
            "scene_id",
            unique=True,
            postgresql_where=sa.text("state IN ('queued', 'rendering')"),
        ),
    )


class TtsPreviewArtifact(Base):
    """Exact-version cleanup journal for one preview render attempt."""

    __tablename__ = "tts_preview_artifacts"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    preview_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    fence_token: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)
    object_key: Mapped[str] = mapped_column(sa.Text, nullable=False)
    version_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["organization_id", "job_id", "preview_id"],
            ["tts_previews.organization_id", "tts_previews.job_id", "tts_previews.id"],
            name="fk_tts_preview_artifacts_org_job_preview",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "preview_id",
            "fence_token",
            name="uq_tts_preview_artifacts_preview_fence",
        ),
        sa.CheckConstraint("fence_token >= 1", name="fence_token_positive"),
        sa.CheckConstraint("octet_length(version_id) BETWEEN 1 AND 1024", name="version_id_valid"),
        sa.CheckConstraint(
            "octet_length(object_key) BETWEEN 1 AND 1024 "
            "AND position('..' in object_key) = 0 AND object_key = "
            "'previews/orgs/' || organization_id::text || '/jobs/' || job_id::text || "
            "'/requests/' || preview_id::text || '/attempts/' || fence_token::text || "
            "'/narration.mp3'",
            name="object_key_exact_attempt_identity",
        ),
        sa.Index("ix_tts_preview_artifacts_created_at", "created_at"),
    )
