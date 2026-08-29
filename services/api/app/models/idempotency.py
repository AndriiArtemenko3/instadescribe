"""Durable organization-scoped idempotency records for integration writes."""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    key: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    method: Mapped[str] = mapped_column(sa.String(12), nullable=False)
    path: Mapped[str] = mapped_column(sa.String(500), nullable=False)
    request_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    state: Mapped[str] = mapped_column(
        sa.String(20), nullable=False, server_default=sa.text("'processing'")
    )
    response_status: Mapped[int | None] = mapped_column(sa.SmallInteger)
    response_body: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(sa.TIMESTAMP(timezone=True), nullable=False)

    __table_args__ = (
        sa.UniqueConstraint("organization_id", "method", "path", "key"),
        sa.CheckConstraint("state IN ('processing', 'completed')", name="state_valid"),
        sa.CheckConstraint(
            "((state = 'processing' AND response_status IS NULL AND response_body IS NULL) "
            "OR (state = 'completed' AND response_status BETWEEN 200 AND 599 "
            "AND response_body IS NOT NULL))",
            name="response_consistent",
        ),
        sa.Index("ix_idempotency_records_expires_at", "expires_at"),
    )
