"""Organization tenancy, service credentials and idempotency foundation.

Every pre-existing project is assigned to the deterministic portfolio
organization. The server default deliberately remains in place so legacy v1
writers and maintenance tools keep their historical single-owner behavior.

Revision ID: 0006_organization_tenancy
Revises: 0005_review_states
Create Date: 2026-08-28
"""

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006_organization_tenancy"
down_revision = "0005_review_states"
branch_labels = None
depends_on = None

PORTFOLIO_ORGANIZATION_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
PORTFOLIO_PRINCIPAL_ID = uuid.UUID("00000000-0000-4000-8000-000000000002")


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'", name="slug_valid"),
        sa.PrimaryKeyConstraint("id", name="pk_organizations"),
        sa.UniqueConstraint("slug", name="uq_organizations_slug"),
    )
    op.create_table(
        "principals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("external_subject", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "kind IN ('human', 'service_account', 'legacy')",
            name="kind_valid",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_principals"),
        sa.UniqueConstraint("external_subject", name="uq_principals_external_subject"),
    )
    op.create_table(
        "organization_memberships",
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "role IN ('owner', 'editor', 'reviewer', 'viewer', 'service')",
            name="role_valid",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_organization_memberships_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["principal_id"],
            ["principals.id"],
            name="fk_organization_memberships_principal_id_principals",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "organization_id", "principal_id", name="pk_organization_memberships"
        ),
    )
    op.create_index(
        "ix_organization_memberships_principal_id",
        "organization_memberships",
        ["principal_id"],
    )
    op.create_table(
        "service_accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "principal_id"],
            [
                "organization_memberships.organization_id",
                "organization_memberships.principal_id",
            ],
            name="fk_service_accounts_membership",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_service_accounts"),
        sa.UniqueConstraint("principal_id", name="uq_service_accounts_principal_id"),
        sa.UniqueConstraint(
            "organization_id", "name", name="uq_service_accounts_organization_id_name"
        ),
    )
    op.create_index("ix_service_accounts_organization_id", "service_accounts", ["organization_id"])
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("service_account_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("key_prefix", sa.String(length=24), nullable=False),
        sa.Column("digest_version", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("secret_digest", sa.String(length=64), nullable=False),
        sa.Column("scopes", JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "digest_version = 1",
            name="digest_version_valid",
        ),
        sa.CheckConstraint(
            "secret_digest ~ '^[a-f0-9]{64}$'",
            name="secret_digest_valid",
        ),
        sa.ForeignKeyConstraint(
            ["service_account_id"],
            ["service_accounts.id"],
            name="fk_api_keys_service_account_id_service_accounts",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_api_keys"),
        sa.UniqueConstraint("key_prefix", name="uq_api_keys_key_prefix"),
    )
    op.create_index("ix_api_keys_service_account_id", "api_keys", ["service_account_id"])
    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("method", sa.String(length=12), nullable=False),
        sa.Column("path", sa.String(length=500), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "state", sa.String(length=20), server_default=sa.text("'processing'"), nullable=False
        ),
        sa.Column("response_status", sa.SmallInteger(), nullable=True),
        sa.Column("response_body", JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('processing', 'completed')",
            name="state_valid",
        ),
        sa.CheckConstraint(
            "((state = 'processing' AND response_status IS NULL AND response_body IS NULL) "
            "OR (state = 'completed' AND response_status BETWEEN 200 AND 599 "
            "AND response_body IS NOT NULL))",
            name="response_consistent",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_idempotency_records_organization_id_organizations",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_idempotency_records"),
        sa.UniqueConstraint(
            "organization_id",
            "method",
            "path",
            "key",
            name="uq_idempotency_records_organization_id_method_path_key",
        ),
    )
    op.create_index("ix_idempotency_records_expires_at", "idempotency_records", ["expires_at"])

    op.execute(
        sa.text(
            "INSERT INTO organizations (id, slug, name) "
            "VALUES (:id, 'portfolio', 'InstaDescribe Portfolio')"
        ).bindparams(sa.bindparam("id", value=PORTFOLIO_ORGANIZATION_ID, type_=sa.Uuid()))
    )
    op.execute(
        sa.text(
            "INSERT INTO principals (id, kind, display_name) "
            "VALUES (:id, 'legacy', 'Portfolio token')"
        ).bindparams(sa.bindparam("id", value=PORTFOLIO_PRINCIPAL_ID, type_=sa.Uuid()))
    )
    op.execute(
        sa.text(
            "INSERT INTO organization_memberships "
            "(organization_id, principal_id, role) "
            "VALUES (:organization_id, :principal_id, 'owner')"
        ).bindparams(
            sa.bindparam("organization_id", value=PORTFOLIO_ORGANIZATION_ID, type_=sa.Uuid()),
            sa.bindparam("principal_id", value=PORTFOLIO_PRINCIPAL_ID, type_=sa.Uuid()),
        )
    )

    portfolio_default = sa.text(f"'{PORTFOLIO_ORGANIZATION_ID}'::uuid")
    op.add_column(
        "projects",
        sa.Column(
            "organization_id",
            sa.Uuid(),
            server_default=portfolio_default,
            nullable=True,
        ),
    )
    op.execute(
        sa.text("UPDATE projects SET organization_id = :organization_id").bindparams(
            sa.bindparam("organization_id", value=PORTFOLIO_ORGANIZATION_ID, type_=sa.Uuid())
        )
    )
    op.alter_column(
        "projects",
        "organization_id",
        existing_type=sa.Uuid(),
        nullable=False,
        server_default=portfolio_default,
    )
    op.create_foreign_key(
        "fk_projects_organization_id_organizations",
        "projects",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_projects_organization_id_updated_at",
        "projects",
        ["organization_id", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_projects_organization_id_updated_at", table_name="projects")
    op.drop_constraint("fk_projects_organization_id_organizations", "projects", type_="foreignkey")
    op.drop_column("projects", "organization_id")
    op.drop_index("ix_idempotency_records_expires_at", table_name="idempotency_records")
    op.drop_table("idempotency_records")
    op.drop_index("ix_api_keys_service_account_id", table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_index("ix_service_accounts_organization_id", table_name="service_accounts")
    op.drop_table("service_accounts")
    op.drop_index("ix_organization_memberships_principal_id", table_name="organization_memberships")
    op.drop_table("organization_memberships")
    op.drop_table("principals")
    op.drop_table("organizations")
