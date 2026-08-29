"""Durable fail-closed organization member invitations.

Revision ID: 0009_owner_invitations
Revises: 0008_job_capacity_enforcement
Create Date: 2026-08-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_owner_invitations"
down_revision = "0008_job_capacity_enforcement"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "organization_invitations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("invited_by_principal_id", sa.Uuid(), nullable=True),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False),
        sa.Column(
            "state", sa.String(length=24), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("cognito_username", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("activated_at", sa.TIMESTAMP(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_organization_invitations_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["invited_by_principal_id"],
            ["principals.id"],
            name="fk_organization_invitations_invited_by_principal_id_principals",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "principal_id"],
            [
                "organization_memberships.organization_id",
                "organization_memberships.principal_id",
            ],
            name="fk_organization_invitations_membership",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_organization_invitations"),
        sa.UniqueConstraint("email", name="uq_organization_invitations_email"),
        sa.UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_organization_invitations_organization_id_idempotency_key",
        ),
        sa.UniqueConstraint(
            "principal_id",
            name="uq_organization_invitations_principal_id",
        ),
    )
    op.create_index(
        "ix_organization_invitations_organization_id_state",
        "organization_invitations",
        ["organization_id", "state"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_organization_invitations_organization_id_state",
        table_name="organization_invitations",
    )
    op.drop_table("organization_invitations")
