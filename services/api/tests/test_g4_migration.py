"""Migration 0003: populated upgrade, value-preserving downgrade, constraints."""

import os
import uuid

import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.skipif(
    not os.environ.get("INSTADESCRIBE_TEST_DATABASE_URL"),
    reason="INSTADESCRIBE_TEST_DATABASE_URL not set (use `make cloud-test` or CI)",
)


def test_populated_0002_upgrade_defaults_and_value_preserving_downgrade(
    migrated_db, alembic_config
):
    from alembic import command

    engine = sa.create_engine(migrated_db)
    pid, jid = str(uuid.uuid4()), str(uuid.uuid4())
    try:
        command.downgrade(alembic_config, "0002_projects_and_provenance")
        with engine.begin() as conn:
            conn.execute(
                sa.text("INSERT INTO projects (id, name) VALUES (:pid, 'g4-mig')"), {"pid": pid}
            )
            conn.execute(
                sa.text(
                    "INSERT INTO jobs (id, project_id, pipeline_revision, status, settings) "
                    "VALUES (:jid, :pid, 'rev', 'READY_FOR_REVIEW', '{}'::jsonb)"
                ),
                {"jid": jid, "pid": pid},
            )

        command.upgrade(alembic_config, "head")
        with engine.begin() as conn:
            row = conn.execute(
                sa.text(
                    "SELECT source_etag, enqueue_message_id, enqueue_attempt_count "
                    "FROM jobs WHERE id = :jid"
                ),
                {"jid": jid},
            ).one()
            assert row.source_etag is None
            assert row.enqueue_message_id is None
            assert row.enqueue_attempt_count == 0  # server default applied to existing rows
            # Non-negative check constraint is enforced.
            with pytest.raises(sa.exc.DBAPIError):
                conn.execute(
                    sa.text("UPDATE jobs SET enqueue_attempt_count = -1 WHERE id = :jid"),
                    {"jid": jid},
                )

        # Value-preserving downgrade: pre-existing 0002 data survives.
        command.downgrade(alembic_config, "0002_projects_and_provenance")
        with engine.begin() as conn:
            survived = conn.execute(
                sa.text("SELECT count(*) FROM jobs WHERE id = :jid AND pipeline_revision = 'rev'"),
                {"jid": jid},
            ).scalar_one()
            assert survived == 1
            cols = {
                r.column_name
                for r in conn.execute(
                    sa.text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'jobs'"
                    )
                )
            }
            assert not {"source_etag", "enqueue_message_id", "enqueued_at"} & cols

        command.upgrade(alembic_config, "head")
    finally:
        command.upgrade(alembic_config, "head")
        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM projects WHERE id = :pid"), {"pid": pid})
        engine.dispose()
