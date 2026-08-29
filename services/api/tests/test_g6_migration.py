"""Migration 0004: populated upgrade, value-preserving downgrade, and
DATABASE-level enforcement of the override invariants (G6 Gate 1)."""

import os
import uuid

import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.skipif(
    not os.environ.get("INSTADESCRIBE_TEST_DATABASE_URL"),
    reason="INSTADESCRIBE_TEST_DATABASE_URL not set (use `make cloud-test` or CI)",
)


def _seed_override(conn, jid: str, oid: str) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO scene_overrides (id, job_id, scene_id, text, active, voice, speed) "
            "VALUES (:oid, :jid, 'scene_3', 'edited AD', false, 'nova', 1.25)"
        ),
        {"oid": oid, "jid": jid},
    )


def test_populated_0003_round_trip_preserves_overrides_and_adds_locked(migrated_db, alembic_config):
    from alembic import command

    engine = sa.create_engine(migrated_db)
    pid, jid, oid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    try:
        command.downgrade(alembic_config, "0003_source_enqueue")
        with engine.begin() as conn:
            conn.execute(
                sa.text("INSERT INTO projects (id, name) VALUES (:pid, 'g6-mig')"), {"pid": pid}
            )
            conn.execute(
                sa.text(
                    "INSERT INTO jobs (id, project_id, pipeline_revision, status, settings) "
                    "VALUES (:jid, :pid, 'rev', 'READY_FOR_REVIEW', '{}'::jsonb)"
                ),
                {"jid": jid, "pid": pid},
            )
            _seed_override(conn, jid, oid)

        command.upgrade(alembic_config, "head")
        with engine.begin() as conn:
            row = conn.execute(
                sa.text(
                    "SELECT scene_id, text, active, locked, voice, speed, version "
                    "FROM scene_overrides WHERE id = :oid"
                ),
                {"oid": oid},
            ).one()
            # Pre-existing values survive; locked takes the new false default.
            assert (row.scene_id, row.text, row.active) == ("scene_3", "edited AD", False)
            assert (row.voice, float(row.speed), row.version) == ("nova", 1.25, 1)
            assert row.locked is False

        # Value-preserving downgrade back to 0003.
        command.downgrade(alembic_config, "0003_source_enqueue")
        with engine.begin() as conn:
            row = conn.execute(
                sa.text(
                    "SELECT scene_id, text, active, voice, speed, version "
                    "FROM scene_overrides WHERE id = :oid"
                ),
                {"oid": oid},
            ).one()
            assert (row.scene_id, row.text, row.active) == ("scene_3", "edited AD", False)
            assert (row.voice, float(row.speed), row.version) == ("nova", 1.25, 1)
            cols = {
                r.column_name
                for r in conn.execute(
                    sa.text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'scene_overrides'"
                    )
                )
            }
            assert "locked" not in cols

        # Re-upgrade succeeds.
        command.upgrade(alembic_config, "head")
    finally:
        command.upgrade(alembic_config, "head")
        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM projects WHERE id = :pid"), {"pid": pid})
        engine.dispose()


@pytest.mark.parametrize(
    "statement",
    [
        # Non-canonical scene identifiers.
        "INSERT INTO scene_overrides (id, job_id, scene_id, review_status) "
        "VALUES (:oid, :jid, 'scene_01', 'edited')",
        "INSERT INTO scene_overrides (id, job_id, scene_id, review_status) "
        "VALUES (:oid, :jid, 'shot_1', 'edited')",
        "INSERT INTO scene_overrides (id, job_id, scene_id, review_status) "
        "VALUES (:oid, :jid, 'scene_0', 'edited')",
        "INSERT INTO scene_overrides (id, job_id, scene_id, review_status) "
        "VALUES (:oid, :jid, '', 'edited')",
        # Speed outside [0.50, 2.50].
        "INSERT INTO scene_overrides (id, job_id, scene_id, speed, review_status) "
        "VALUES (:oid, :jid, 'scene_1', 0.49, 'edited')",
        "INSERT INTO scene_overrides (id, job_id, scene_id, speed, review_status) "
        "VALUES (:oid, :jid, 'scene_1', 2.51, 'edited')",
        # Version below the floor.
        "INSERT INTO scene_overrides (id, job_id, scene_id, version, review_status) "
        "VALUES (:oid, :jid, 'scene_1', 0, 'edited')",
    ],
)
def test_postgres_rejects_invalid_override_rows(db_engine, statement):
    """G6 Gate 1: PostgreSQL — not only Pydantic — rejects invalid rows."""
    pid, jid = str(uuid.uuid4()), str(uuid.uuid4())
    with db_engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO projects (id, name) VALUES (:pid, 'g6-ck')"), {"pid": pid}
        )
        conn.execute(
            sa.text(
                "INSERT INTO jobs (id, project_id, pipeline_revision, status, settings) "
                "VALUES (:jid, :pid, 'rev', 'READY_FOR_REVIEW', '{}'::jsonb)"
            ),
            {"jid": jid, "pid": pid},
        )
    with pytest.raises(sa.exc.DBAPIError):
        with db_engine.begin() as conn:
            conn.execute(sa.text(statement), {"oid": str(uuid.uuid4()), "jid": jid})


def test_valid_boundary_rows_are_accepted(db_engine):
    pid, jid = str(uuid.uuid4()), str(uuid.uuid4())
    with db_engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO projects (id, name) VALUES (:pid, 'g6-ok')"), {"pid": pid}
        )
        conn.execute(
            sa.text(
                "INSERT INTO jobs (id, project_id, pipeline_revision, status, settings) "
                "VALUES (:jid, :pid, 'rev', 'READY_FOR_REVIEW', '{}'::jsonb)"
            ),
            {"jid": jid, "pid": pid},
        )
        for scene, speed in (("scene_1", 0.50), ("scene_9", 2.50), ("scene_10", None)):
            conn.execute(
                sa.text(
                    "INSERT INTO scene_overrides "
                    "(id, job_id, scene_id, speed, review_status) "
                    "VALUES (:oid, :jid, :scene, :speed, 'edited')"
                ),
                {"oid": str(uuid.uuid4()), "jid": jid, "scene": scene, "speed": speed},
            )
        locked_default = conn.execute(
            sa.text("SELECT bool_or(locked) FROM scene_overrides WHERE job_id = :jid"),
            {"jid": jid},
        ).scalar_one()
        assert locked_default is False
