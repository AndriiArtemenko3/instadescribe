"""G2.5 backfill: 0002 must safely upgrade a POPULATED 0001 database and
downgrade back to a valid 0001 shape without losing name/starred values."""

import os
import uuid

import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.skipif(
    not os.environ.get("INSTADESCRIBE_TEST_DATABASE_URL"),
    reason="INSTADESCRIBE_TEST_DATABASE_URL not set (use `make cloud-test` or CI)",
)


def test_populated_0001_upgrade_backfill_and_downgrade(migrated_db, alembic_config):
    from alembic import command

    engine = sa.create_engine(migrated_db)
    legacy_id = str(uuid.uuid4())
    try:
        # Down to the 0001 shape and plant a legacy job that still owns
        # name/starred and has no project/provenance columns.
        command.downgrade(alembic_config, "0001_core_tables")
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO jobs (id, name, status, settings, starred) "
                    "VALUES (:id, 'legacy-name', 'READY_FOR_REVIEW', '{}'::jsonb, true)"
                ),
                {"id": legacy_id},
            )

        # Upgrade: backfill must create one project per job, reusing the job
        # UUID, copying name/starred, and stamping the honest sentinel.
        command.upgrade(alembic_config, "head")
        with engine.connect() as conn:
            project = conn.execute(
                sa.text("SELECT id, name, starred FROM projects WHERE id = :id"),
                {"id": legacy_id},
            ).one()
            assert project.name == "legacy-name"
            assert project.starred is True
            job = conn.execute(
                sa.text("SELECT project_id, pipeline_revision FROM jobs WHERE id = :id"),
                {"id": legacy_id},
            ).one()
            assert str(job.project_id) == legacy_id
            assert job.pipeline_revision == "unknown-pre-g3"
            job_columns = {
                r.column_name
                for r in conn.execute(
                    sa.text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'jobs'"
                    )
                )
            }
            assert not {"name", "starred"} & job_columns

        # Downgrade restores a valid 0001 shape with the values copied back.
        command.downgrade(alembic_config, "0001_core_tables")
        with engine.connect() as conn:
            row = conn.execute(
                sa.text("SELECT name, starred FROM jobs WHERE id = :id"), {"id": legacy_id}
            ).one()
            assert row.name == "legacy-name"
            assert row.starred is True
            tables = {
                r.table_name
                for r in conn.execute(
                    sa.text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public'"
                    )
                )
            }
            assert "projects" not in tables
    finally:
        # Leave the database at head with clean tables for the other suites.
        command.upgrade(alembic_config, "head")
        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM scene_overrides"))
            conn.execute(sa.text("DELETE FROM artifacts"))
            conn.execute(sa.text("DELETE FROM jobs"))
            conn.execute(sa.text("DELETE FROM projects"))
        engine.dispose()
