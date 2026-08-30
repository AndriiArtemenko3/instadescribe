import uuid

import pytest
import sqlalchemy as sa
from conftest import requires_db


def _seed_tenant(connection, *, awaiting: int = 2, queued: int = 2, processing: int = 1):
    organization_id = uuid.uuid4()
    project_id = uuid.uuid4()
    connection.execute(
        sa.text("INSERT INTO organizations (id, slug, name) VALUES (:id, :slug, 'Capacity')"),
        {"id": organization_id, "slug": f"capacity-{organization_id.hex[:12]}"},
    )
    connection.execute(
        sa.text(
            "INSERT INTO organization_quotas "
            "(organization_id, max_awaiting_upload_jobs, max_queued_jobs, "
            "max_processing_jobs) VALUES (:id, :awaiting, :queued, :processing)"
        ),
        {
            "id": organization_id,
            "awaiting": awaiting,
            "queued": queued,
            "processing": processing,
        },
    )
    connection.execute(
        sa.text("INSERT INTO organization_job_capacity (organization_id) VALUES (:id)"),
        {"id": organization_id},
    )
    connection.execute(
        sa.text(
            "INSERT INTO projects (id, organization_id, name) "
            "VALUES (:project_id, :organization_id, 'Capacity project')"
        ),
        {"project_id": project_id, "organization_id": organization_id},
    )
    return organization_id, project_id


def _insert_job(connection, organization_id, project_id, status="AWAITING_UPLOAD"):
    job_id = uuid.uuid4()
    connection.execute(
        sa.text(
            "INSERT INTO jobs "
            "(id, organization_id, project_id, pipeline_revision, status, settings) "
            "VALUES (:id, :organization_id, :project_id, 'capacity-test', :status, '{}'::jsonb)"
        ),
        {
            "id": job_id,
            "organization_id": organization_id,
            "project_id": project_id,
            "status": status,
        },
    )
    return job_id


def _capacity(connection, organization_id):
    return connection.execute(
        sa.text(
            "SELECT awaiting_upload_jobs, queued_jobs, processing_jobs "
            "FROM organization_job_capacity WHERE organization_id = :id"
        ),
        {"id": organization_id},
    ).one()


@requires_db
def test_capacity_trigger_enforces_limits_and_tracks_every_state_transition(db_engine):
    with db_engine.begin() as connection:
        organization_id, project_id = _seed_tenant(connection)
        first = _insert_job(connection, organization_id, project_id)
        second = _insert_job(connection, organization_id, project_id)
        assert tuple(_capacity(connection, organization_id)) == (2, 0, 0)

    with pytest.raises(sa.exc.IntegrityError) as awaiting_error:
        with db_engine.begin() as connection:
            _insert_job(connection, organization_id, project_id)
    assert (
        getattr(awaiting_error.value.orig.diag, "constraint_name", None)
        == "organization_job_capacity_limit"
    )

    with db_engine.begin() as connection:
        connection.execute(
            sa.text("UPDATE jobs SET status = 'UPLOAD_COMPLETE' WHERE id = :id"),
            {"id": first},
        )
        connection.execute(
            sa.text("UPDATE jobs SET status = 'QUEUED' WHERE id = :id"),
            {"id": first},
        )
        connection.execute(
            sa.text("UPDATE jobs SET status = 'QUEUED' WHERE id = :id"),
            {"id": second},
        )
        third = _insert_job(connection, organization_id, project_id)
        assert tuple(_capacity(connection, organization_id)) == (1, 2, 0)

    with pytest.raises(sa.exc.IntegrityError) as queued_error:
        with db_engine.begin() as connection:
            connection.execute(
                sa.text("UPDATE jobs SET status = 'UPLOAD_COMPLETE' WHERE id = :id"),
                {"id": third},
            )
    assert (
        getattr(queued_error.value.orig.diag, "constraint_name", None)
        == "organization_job_capacity_limit"
    )

    with db_engine.begin() as connection:
        connection.execute(
            sa.text("UPDATE jobs SET status = 'PROCESSING' WHERE id = :id"),
            {"id": first},
        )
        assert tuple(_capacity(connection, organization_id)) == (1, 1, 1)

    with pytest.raises(sa.exc.IntegrityError):
        with db_engine.begin() as connection:
            connection.execute(
                sa.text("UPDATE jobs SET status = 'PROCESSING' WHERE id = :id"),
                {"id": second},
            )

    with db_engine.begin() as connection:
        connection.execute(
            sa.text("UPDATE jobs SET status = 'READY_FOR_REVIEW' WHERE id = :id"),
            {"id": first},
        )
        connection.execute(
            sa.text("UPDATE jobs SET status = 'PROCESSING' WHERE id = :id"),
            {"id": second},
        )
        connection.execute(sa.text("DELETE FROM jobs WHERE id = :id"), {"id": second})
        assert tuple(_capacity(connection, organization_id)) == (1, 0, 0)


@requires_db
def test_capacity_is_isolated_per_organization(db_engine):
    with db_engine.begin() as connection:
        first_org, first_project = _seed_tenant(connection, awaiting=1, queued=1)
        second_org, second_project = _seed_tenant(connection, awaiting=1, queued=1)
        _insert_job(connection, first_org, first_project, "PROCESSING")
        _insert_job(connection, second_org, second_project, "PROCESSING")
        assert tuple(_capacity(connection, first_org)) == (0, 0, 1)
        assert tuple(_capacity(connection, second_org)) == (0, 0, 1)
