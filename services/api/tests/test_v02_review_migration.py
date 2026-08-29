"""Migration 0005: review-state semantics and optimistic-version guards."""

import os
import uuid

import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.skipif(
    not os.environ.get("INSTADESCRIBE_TEST_DATABASE_URL"),
    reason="INSTADESCRIBE_TEST_DATABASE_URL not set (use `make cloud-test` or CI)",
)


def test_populated_0004_round_trip_is_value_preserving_and_honest(migrated_db, alembic_config):
    from alembic import command

    engine = sa.create_engine(migrated_db)
    pid, jid, oid = (str(uuid.uuid4()) for _ in range(3))
    try:
        command.downgrade(alembic_config, "0004_override_fields")
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO projects (id, name, starred, version) "
                    "VALUES (:pid, 'existing project', true, 4)"
                ),
                {"pid": pid},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO jobs (id, project_id, pipeline_revision, status, settings) "
                    "VALUES (:jid, :pid, 'rev', 'READY_FOR_REVIEW', '{}'::jsonb)"
                ),
                {"jid": jid, "pid": pid},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO scene_overrides "
                    "(id, job_id, scene_id, text, active, locked, voice, speed, version) "
                    "VALUES (:oid, :jid, 'scene_3', 'human edit', false, true, 'nova', 1.25, 7)"
                ),
                {"oid": oid, "jid": jid},
            )

        command.upgrade(alembic_config, "head")
        with engine.begin() as conn:
            row = conn.execute(
                sa.text(
                    "SELECT text, active, locked, voice, speed, version, "
                    "review_status, reviewed_at FROM scene_overrides WHERE id = :oid"
                ),
                {"oid": oid},
            ).one()
            assert (row.text, row.active, row.locked, row.voice) == (
                "human edit",
                False,
                True,
                "nova",
            )
            assert (float(row.speed), row.version) == (1.25, 7)
            assert row.review_status == "edited"
            assert row.reviewed_at is None
            default = conn.execute(
                sa.text(
                    "SELECT column_default FROM information_schema.columns "
                    "WHERE table_name = 'scene_overrides' AND column_name = 'review_status'"
                )
            ).scalar_one()
            assert default is None
            project = conn.execute(
                sa.text("SELECT name, starred, version FROM projects WHERE id = :pid"),
                {"pid": pid},
            ).one()
            assert tuple(project) == ("existing project", True, 4)

        command.downgrade(alembic_config, "0004_override_fields")
        with engine.begin() as conn:
            row = conn.execute(
                sa.text(
                    "SELECT text, active, locked, voice, speed, version "
                    "FROM scene_overrides WHERE id = :oid"
                ),
                {"oid": oid},
            ).one()
            assert (row.text, row.active, row.locked, row.voice) == (
                "human edit",
                False,
                True,
                "nova",
            )
            assert (float(row.speed), row.version) == (1.25, 7)
            columns = {
                item.column_name
                for item in conn.execute(
                    sa.text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'scene_overrides'"
                    )
                )
            }
            assert "review_status" not in columns
            assert "reviewed_at" not in columns

        command.upgrade(alembic_config, "head")
    finally:
        command.upgrade(alembic_config, "head")
        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM projects WHERE id = :pid"), {"pid": pid})
        engine.dispose()


@pytest.mark.parametrize(
    ("review_status", "reviewed_at"),
    [
        ("unknown", None),
        ("generated", None),
        ("approved", None),
        ("rejected", None),
        ("edited", "now()"),
    ],
)
def test_postgres_rejects_invalid_review_state_timestamp_pairs(
    db_engine, review_status, reviewed_at
):
    pid, jid = str(uuid.uuid4()), str(uuid.uuid4())
    with db_engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO projects (id, name) VALUES (:pid, 'review-ck')"), {"pid": pid}
        )
        conn.execute(
            sa.text(
                "INSERT INTO jobs (id, project_id, pipeline_revision, status, settings) "
                "VALUES (:jid, :pid, 'rev', 'READY_FOR_REVIEW', '{}'::jsonb)"
            ),
            {"jid": jid, "pid": pid},
        )
    reviewed_sql = "NULL" if reviewed_at is None else reviewed_at
    statement = sa.text(
        "INSERT INTO scene_overrides "
        "(id, job_id, scene_id, review_status, reviewed_at) "
        f"VALUES (:oid, :jid, 'scene_1', :status, {reviewed_sql})"
    )
    with pytest.raises(sa.exc.DBAPIError):
        with db_engine.begin() as conn:
            conn.execute(
                statement,
                {"oid": str(uuid.uuid4()), "jid": jid, "status": review_status},
            )


@pytest.mark.parametrize(
    ("review_status", "has_timestamp"),
    [("edited", False), ("approved", True), ("rejected", True)],
)
def test_postgres_accepts_each_valid_review_state(db_engine, review_status, has_timestamp):
    pid, jid = str(uuid.uuid4()), str(uuid.uuid4())
    with db_engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO projects (id, name) VALUES (:pid, 'review-ok')"), {"pid": pid}
        )
        conn.execute(
            sa.text(
                "INSERT INTO jobs (id, project_id, pipeline_revision, status, settings) "
                "VALUES (:jid, :pid, 'rev', 'READY_FOR_REVIEW', '{}'::jsonb)"
            ),
            {"jid": jid, "pid": pid},
        )
        reviewed_sql = "now()" if has_timestamp else "NULL"
        conn.execute(
            sa.text(
                "INSERT INTO scene_overrides "
                "(id, job_id, scene_id, review_status, reviewed_at) "
                f"VALUES (:oid, :jid, 'scene_1', :status, {reviewed_sql})"
            ),
            {"oid": str(uuid.uuid4()), "jid": jid, "status": review_status},
        )


def test_postgres_rejects_project_version_below_one(db_engine):
    with pytest.raises(sa.exc.DBAPIError):
        with db_engine.begin() as conn:
            conn.execute(
                sa.text("INSERT INTO projects (id, name, version) VALUES (:id, 'bad', 0)"),
                {"id": str(uuid.uuid4())},
            )


def test_postgres_requires_an_explicit_persisted_review_state(db_engine):
    pid, jid = str(uuid.uuid4()), str(uuid.uuid4())
    with db_engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO projects (id, name) VALUES (:pid, 'review-required')"),
            {"pid": pid},
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
            conn.execute(
                sa.text(
                    "INSERT INTO scene_overrides (id, job_id, scene_id) "
                    "VALUES (:oid, :jid, 'scene_1')"
                ),
                {"oid": str(uuid.uuid4()), "jid": jid},
            )
