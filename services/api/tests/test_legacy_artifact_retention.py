"""Migration and tenant proofs for normalized legacy Artifact identities."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from app.core.tenancy import PORTFOLIO_ORGANIZATION_ID
from app.models import Artifact, Job, Organization, Project
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def test_0013_backfills_only_recoverable_versions_and_round_trips(
    migrated_db,
    alembic_config,
):
    from alembic import command

    engine = sa.create_engine(migrated_db)
    project_id = uuid.uuid4()
    job_id = uuid.uuid4()
    source_id = uuid.uuid4()
    generated_id = uuid.uuid4()
    unsafe_id = uuid.uuid4()
    created = datetime.now(UTC) - timedelta(days=40)
    try:
        command.downgrade(alembic_config, "0012_tts_previews")
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO projects (id, organization_id, name) "
                    "VALUES (:id, :organization_id, 'Artifact retention migration')"
                ),
                {"id": project_id, "organization_id": PORTFOLIO_ORGANIZATION_ID},
            )
            connection.execute(
                sa.text(
                    "INSERT INTO jobs "
                    "(id, organization_id, project_id, pipeline_revision, status, settings, "
                    "input_object_key, source_version_id, created_at, updated_at) "
                    "VALUES (:id, :organization_id, :project_id, 'migration-test', 'COMPLETED', "
                    "'{}'::jsonb, :source_key, 'source-v7', :created, :created)"
                ),
                {
                    "id": job_id,
                    "organization_id": PORTFOLIO_ORGANIZATION_ID,
                    "project_id": project_id,
                    "source_key": f"uploads/{job_id}/source/video.mp4",
                    "created": created,
                },
            )
            connection.execute(
                sa.text(
                    "INSERT INTO artifacts "
                    "(id, job_id, artifact_type, object_key, content_type, metadata, created_at) "
                    "VALUES "
                    "(:source_id, :job_id, 'source_video', :source_key, 'video/mp4', "
                    "'{}'::jsonb, :created), "
                    "(:generated_id, :job_id, 'scenes_json', :generated_key, "
                    "'application/json', '{\"version_id\": \"generated-v3\"}'::jsonb, :created), "
                    "(:unsafe_id, :job_id, 'entities_json', :unsafe_key, "
                    "'application/json', '{\"version_id\": 42}'::jsonb, :created)"
                ),
                {
                    "source_id": source_id,
                    "generated_id": generated_id,
                    "unsafe_id": unsafe_id,
                    "job_id": job_id,
                    "source_key": f"uploads/{job_id}/source/video.mp4",
                    "generated_key": f"jobs/{job_id}/attempts/1/analysis/scenes.json",
                    "unsafe_key": f"jobs/{job_id}/attempts/1/analysis/entities.json",
                    "created": created,
                },
            )

        command.upgrade(alembic_config, "head")
        with engine.connect() as connection:
            rows = {
                row.id: row
                for row in connection.execute(
                    sa.text(
                        "SELECT id, organization_id, version_id, retention_state, "
                        "purged_at, purge_after FROM artifacts WHERE job_id = :job_id"
                    ),
                    {"job_id": job_id},
                )
            }
        assert rows[source_id].organization_id == PORTFOLIO_ORGANIZATION_ID
        assert rows[source_id].version_id == "source-v7"
        assert rows[generated_id].version_id == "generated-v3"
        assert rows[unsafe_id].version_id is None
        assert rows[source_id].retention_state == "active"
        assert rows[generated_id].retention_state == "active"
        assert rows[unsafe_id].retention_state == "unrecoverable"
        assert all(row.purged_at is None for row in rows.values())
        assert all(row.purge_after == created + timedelta(days=30) for row in rows.values())

        command.downgrade(alembic_config, "0012_tts_previews")
        columns = {column["name"] for column in sa.inspect(engine).get_columns("artifacts")}
        assert (
            not {
                "organization_id",
                "version_id",
                "retention_state",
                "purged_at",
                "purge_after",
            }
            & columns
        )
    finally:
        command.upgrade(alembic_config, "head")
        with engine.begin() as connection:
            connection.execute(sa.text("DELETE FROM projects WHERE id = :id"), {"id": project_id})
        engine.dispose()


def test_artifact_composite_tenant_fk_masks_cross_tenant_link(db_engine):
    with Session(db_engine) as session:
        foreign = Organization(slug=f"artifact-{uuid.uuid4().hex[:8]}", name="Foreign")
        session.add(foreign)
        session.flush()
        project = Project(organization_id=PORTFOLIO_ORGANIZATION_ID, name="Owned")
        session.add(project)
        session.flush()
        job = Job(
            organization_id=PORTFOLIO_ORGANIZATION_ID,
            project_id=project.id,
            pipeline_revision="artifact-tenant-test",
            status="CANCELLED",
            settings={},
            completed_at=datetime.now(UTC),
        )
        session.add(job)
        session.flush()
        session.add(
            Artifact(
                organization_id=foreign.id,
                job_id=job.id,
                artifact_type="scenes_json",
                object_key=f"jobs/{job.id}/attempts/1/analysis/scenes.json",
                version_id="v1",
                content_type="application/json",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
