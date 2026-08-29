"""Organization, principal, membership, service-account and API-key models."""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(sa.String(80), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )

    __table_args__ = (
        sa.CheckConstraint(
            "slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'",
            name="slug_valid",
        ),
    )


class Principal(Base):
    __tablename__ = "principals"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(sa.String(24), nullable=False)
    display_name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    external_subject: Mapped[str | None] = mapped_column(sa.String(255), unique=True)
    is_active: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )

    __table_args__ = (
        sa.CheckConstraint(
            "kind IN ('human', 'service_account', 'legacy')",
            name="kind_valid",
        ),
    )


class OrganizationMembership(Base):
    __tablename__ = "organization_memberships"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("organizations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    principal_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("principals.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Mapped[str] = mapped_column(sa.String(24), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )

    __table_args__ = (
        sa.CheckConstraint(
            "role IN ('owner', 'editor', 'reviewer', 'viewer', 'service')",
            name="role_valid",
        ),
        sa.Index("ix_organization_memberships_principal_id", "principal_id"),
    )


class OrganizationInvitation(Base):
    """Durable, fail-closed provisioning record for one human membership."""

    __tablename__ = "organization_invitations"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    principal_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False, unique=True)
    invited_by_principal_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("principals.id", ondelete="SET NULL"),
    )
    email: Mapped[str] = mapped_column(sa.String(254), nullable=False)
    role: Mapped[str] = mapped_column(sa.String(24), nullable=False)
    state: Mapped[str] = mapped_column(
        sa.String(24), nullable=False, server_default=sa.text("'pending'")
    )
    idempotency_key: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    cognito_username: Mapped[str | None] = mapped_column(sa.String(255))
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )
    activated_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["organization_id", "principal_id"],
            [
                "organization_memberships.organization_id",
                "organization_memberships.principal_id",
            ],
            name="fk_organization_invitations_membership",
            ondelete="CASCADE",
        ),
        # Browser assertions intentionally select no organization. The beta
        # therefore permits one canonical invited email globally, preserving
        # the single-active-membership invariant enforced by browser auth.
        sa.UniqueConstraint("email"),
        sa.UniqueConstraint("organization_id", "idempotency_key"),
        sa.CheckConstraint(
            "role IN ('editor', 'reviewer', 'viewer')",
            name="role_valid",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'active', 'provider_conflict', 'revoked')",
            name="state_valid",
        ),
        sa.CheckConstraint(
            "length(email) BETWEEN 3 AND 254 AND email = lower(btrim(email))",
            name="email_canonical",
        ),
        sa.CheckConstraint(
            "length(idempotency_key) BETWEEN 1 AND 255",
            name="idempotency_key_valid",
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[a-f0-9]{64}$'",
            name="request_hash_valid",
        ),
        sa.CheckConstraint(
            "((state = 'active' AND activated_at IS NOT NULL AND "
            "cognito_username IS NOT NULL) OR "
            "(state <> 'active' AND activated_at IS NULL))",
            name="activation_consistent",
        ),
        sa.Index("ix_organization_invitations_organization_id_state", "organization_id", "state"),
    )


class ServiceAccount(Base):
    __tablename__ = "service_accounts"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False)
    principal_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
    )

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["organization_id", "principal_id"],
            [
                "organization_memberships.organization_id",
                "organization_memberships.principal_id",
            ],
            name="fk_service_accounts_membership",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("organization_id", "name"),
        sa.Index("ix_service_accounts_organization_id", "organization_id"),
    )


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    service_account_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        sa.ForeignKey("service_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    key_prefix: Mapped[str] = mapped_column(sa.String(24), nullable=False, unique=True)
    digest_version: Mapped[int] = mapped_column(
        sa.SmallInteger, nullable=False, server_default=sa.text("1")
    )
    secret_digest: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True))

    __table_args__ = (
        sa.CheckConstraint("digest_version = 1", name="digest_version_valid"),
        sa.CheckConstraint(
            "secret_digest ~ '^[a-f0-9]{64}$'",
            name="secret_digest_valid",
        ),
        sa.Index("ix_api_keys_service_account_id", "service_account_id"),
    )
