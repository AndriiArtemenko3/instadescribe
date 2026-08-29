"""Populated portfolio backfill and reversible organization migration."""

import os
import uuid

import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.skipif(
    not os.environ.get("INSTADESCRIBE_TEST_DATABASE_URL"),
    reason="INSTADESCRIBE_TEST_DATABASE_URL not set (use `make cloud-test` or CI)",
)

PORTFOLIO_ORGANIZATION_ID = "00000000-0000-4000-8000-000000000001"
PORTFOLIO_PRINCIPAL_ID = "00000000-0000-4000-8000-000000000002"


def test_populated_projects_backfill_to_seeded_portfolio_organization(migrated_db, alembic_config):
    from alembic import command

    engine = sa.create_engine(migrated_db)
    project_id = str(uuid.uuid4())
    try:
        command.downgrade(alembic_config, "0005_review_states")
        with engine.begin() as connection:
            connection.execute(
                sa.text("INSERT INTO projects (id, name) VALUES (:id, 'Pre-tenant project')"),
                {"id": project_id},
            )

        command.upgrade(alembic_config, "head")
        with engine.connect() as connection:
            organization = connection.execute(
                sa.text("SELECT slug, name FROM organizations WHERE id = :id"),
                {"id": PORTFOLIO_ORGANIZATION_ID},
            ).one()
            assert organization.slug == "portfolio"
            assert organization.name == "InstaDescribe Portfolio"
            assert (
                str(
                    connection.execute(
                        sa.text("SELECT organization_id FROM projects WHERE id = :id"),
                        {"id": project_id},
                    ).scalar_one()
                )
                == PORTFOLIO_ORGANIZATION_ID
            )
            membership = connection.execute(
                sa.text(
                    "SELECT role FROM organization_memberships "
                    "WHERE organization_id = :organization_id AND principal_id = :principal_id"
                ),
                {
                    "organization_id": PORTFOLIO_ORGANIZATION_ID,
                    "principal_id": PORTFOLIO_PRINCIPAL_ID,
                },
            ).scalar_one()
            assert membership == "owner"

        command.downgrade(alembic_config, "0005_review_states")
        inspector = sa.inspect(engine)
        assert "organizations" not in inspector.get_table_names()
        assert "organization_id" not in {
            column["name"] for column in inspector.get_columns("projects")
        }
    finally:
        command.upgrade(alembic_config, "head")
        with engine.begin() as connection:
            connection.execute(sa.text("DELETE FROM projects WHERE id = :id"), {"id": project_id})
        engine.dispose()
