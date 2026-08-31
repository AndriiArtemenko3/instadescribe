"""PostgreSQL integration: migration shape, conditional transitions, cascades,
constraints, and the single-compute-active partial index (G2.5 project/job shape)."""

import os

import pytest
import sqlalchemy as sa
from app.domain.states import IllegalTransitionError, JobState
from app.models import Artifact, Job, Project, SceneOverride
from app.repositories.jobs import transition_job
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.skipif(
    not os.environ.get("INSTADESCRIBE_TEST_DATABASE_URL"),
    reason="INSTADESCRIBE_TEST_DATABASE_URL not set (use `make cloud-test` or CI)",
)


def _add_job(s: Session, status: JobState = JobState.AWAITING_UPLOAD, **kw) -> Job:
    """Create a project + one processing job (distinct IDs, as the API does)."""
    project = Project(name=kw.pop("name", "t"))
    s.add(project)
    s.flush()
    job = Job(
        project_id=project.id,
        pipeline_revision=kw.pop("pipeline_revision", "test"),
        status=status.value,
        settings=kw.pop("settings", {}),
        **kw,
    )
    s.add(job)
    return job


def test_upgrade_created_exactly_the_expected_tables(db_engine):
    inspector = sa.inspect(db_engine)
    tables = set(inspector.get_table_names())
    assert tables == {
        "alembic_version",
        "api_keys",
        "analyst_decisions",
        "artifacts",
        "assets",
        "audit_events",
        "belief_snapshots",
        "deliverables",
        "evidence_items",
        "idempotency_records",
        "investigation_steps",
        "investigations",
        "job_events",
        "jobs",
        "organization_job_capacity",
        "organization_invitations",
        "organization_memberships",
        "organization_quotas",
        "organization_usage_periods",
        "organizations",
        "principals",
        "projects",
        "quota_reservations",
        "render_attempt_artifacts",
        "renders",
        "reviews",
        "scene_overrides",
        "service_accounts",
        "source_records",
        "tts_preview_artifacts",
        "tts_previews",
        "webhook_deliveries",
        "webhook_endpoints",
    }
    job_indexes = {ix["name"] for ix in inspector.get_indexes("jobs")}
    assert {
        "ix_jobs_status_created_at",
        "ix_jobs_updated_at",
        "ix_jobs_project_id_created_at",
    } <= job_indexes
    checks = {c["name"] for c in inspector.get_check_constraints("jobs")}
    assert {"ck_jobs_status_valid", "ck_jobs_progress_range"} <= checks
    project_indexes = {ix["name"] for ix in inspector.get_indexes("projects")}
    assert "ix_projects_updated_at" in project_indexes
    assert "ix_projects_organization_id_updated_at" in project_indexes
    job_columns = {c["name"] for c in inspector.get_columns("jobs")}
    assert {"organization_id", "project_id", "pipeline_revision", "client_reference"} <= job_columns
    assert "organization_id" in {c["name"] for c in inspector.get_columns("projects")}
    assert "external_id" in {c["name"] for c in inspector.get_columns("projects")}
    assert not {"name", "starred"} & job_columns  # authority moved to projects


def test_partial_unique_index_exists(db_engine):
    query = "SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_jobs_one_compute_active'"
    with db_engine.connect() as conn:
        row = conn.execute(sa.text(query)).scalar_one()
    assert "UNIQUE" in row
    assert "organization_id" in row
    assert "PROCESSING" in row
    for status in ("QUEUED", "UPLOAD_COMPLETE"):
        assert status not in row
    assert "AWAITING_UPLOAD" not in row
    indexes = {item["name"] for item in sa.inspect(db_engine).get_indexes("jobs")}
    assert "ix_jobs_organization_id_status_created_at" in indexes


def test_conditional_transition_and_lost_race(db_engine):
    with Session(db_engine) as s:
        job = _add_job(s)
        s.commit()
        moved = transition_job(s, job.id, JobState.AWAITING_UPLOAD, JobState.UPLOAD_COMPLETE)
        s.commit()
        assert moved is not None and moved.status == "UPLOAD_COMPLETE"
        again = transition_job(s, job.id, JobState.AWAITING_UPLOAD, JobState.UPLOAD_COMPLETE)
        s.commit()
        assert again is None


def test_illegal_edge_raises_before_touching_the_database(db_engine):
    with Session(db_engine) as s:
        job = _add_job(s)
        s.commit()
        with pytest.raises(IllegalTransitionError):
            transition_job(s, job.id, JobState.AWAITING_UPLOAD, JobState.PROCESSING)
        s.rollback()
        assert s.get(Job, job.id).status == "AWAITING_UPLOAD"


def test_tuple_claim_supports_publication_recovery(db_engine):
    with Session(db_engine) as s:
        job = _add_job(s, JobState.UPLOAD_COMPLETE)
        s.commit()
        claimed = transition_job(
            s,
            job.id,
            (JobState.QUEUED, JobState.UPLOAD_COMPLETE),
            JobState.PROCESSING,
            values={"worker_id": "w-test", "attempt_count": Job.attempt_count + 1},
        )
        s.commit()
        assert claimed is not None
        assert claimed.status == "PROCESSING"
        assert claimed.worker_id == "w-test"
        assert claimed.attempt_count == 1


def test_distinct_ids_and_join_behavior(db_engine):
    with Session(db_engine) as s:
        _add_job(s, name="joined-project")
        s.commit()
        row = s.execute(sa.select(Job, Project).join(Project, Job.project_id == Project.id)).one()
        assert row.Job.id != row.Project.id  # new writes use distinct UUIDs
        assert row.Project.name == "joined-project"


def test_job_cascade_delete_removes_children(db_engine):
    with Session(db_engine) as s:
        job = _add_job(s, JobState.READY_FOR_REVIEW)
        s.flush()
        s.add(
            Artifact(
                job_id=job.id, artifact_type="scenes_json", object_key="k", content_type="a/json"
            )
        )
        s.add(SceneOverride(job_id=job.id, scene_id="scene_1", text="x", review_status="edited"))
        s.commit()
        s.execute(sa.delete(Job).where(Job.id == job.id))
        s.commit()
        assert s.execute(sa.select(sa.func.count()).select_from(Artifact)).scalar_one() == 0
        assert s.execute(sa.select(sa.func.count()).select_from(SceneOverride)).scalar_one() == 0


def test_project_cascade_delete_removes_jobs_and_grandchildren(db_engine):
    with Session(db_engine) as s:
        job = _add_job(s, JobState.READY_FOR_REVIEW)
        s.flush()
        s.add(
            Artifact(job_id=job.id, artifact_type="scenes_json", object_key="k", content_type="j")
        )
        s.commit()
        s.execute(sa.delete(Project).where(Project.id == job.project_id))
        s.commit()
        assert s.execute(sa.select(sa.func.count()).select_from(Job)).scalar_one() == 0
        assert s.execute(sa.select(sa.func.count()).select_from(Artifact)).scalar_one() == 0


def test_job_requires_project_fk(db_engine):
    import uuid

    with Session(db_engine) as s:
        s.add(
            Job(
                project_id=uuid.uuid4(),  # no such project
                pipeline_revision="test",
                status="AWAITING_UPLOAD",
                settings={},
            )
        )
        with pytest.raises(IntegrityError):
            s.commit()


def test_artifact_uniqueness_per_job_and_type(db_engine):
    with Session(db_engine) as s:
        job = _add_job(s)
        s.flush()
        s.add(
            Artifact(job_id=job.id, artifact_type="scenes_json", object_key="a", content_type="j")
        )
        s.commit()
        s.add(
            Artifact(job_id=job.id, artifact_type="scenes_json", object_key="b", content_type="j")
        )
        with pytest.raises(IntegrityError):
            s.commit()


def test_scene_override_uniqueness(db_engine):
    with Session(db_engine) as s:
        job = _add_job(s)
        s.flush()
        s.add(SceneOverride(job_id=job.id, scene_id="scene_1", review_status="edited"))
        s.commit()
        s.add(SceneOverride(job_id=job.id, scene_id="scene_1", review_status="edited"))
        with pytest.raises(IntegrityError):
            s.commit()


def test_progress_and_status_check_constraints(db_engine):
    with Session(db_engine) as s:
        _add_job(s, progress=150)
        with pytest.raises(IntegrityError):
            s.commit()
        s.rollback()
        project = Project(name="t")
        s.add(project)
        s.flush()
        s.add(Job(project_id=project.id, pipeline_revision="test", status="BOGUS", settings={}))
        with pytest.raises(IntegrityError):
            s.commit()


def test_processing_slot_is_unique_while_multiple_jobs_can_queue(db_engine):
    with Session(db_engine) as s:
        _add_job(s, JobState.QUEUED)
        s.commit()
        _add_job(s, JobState.QUEUED)
        s.commit()
        _add_job(s, JobState.PROCESSING)
        s.commit()
        _add_job(s, JobState.PROCESSING)
        with pytest.raises(IntegrityError):
            s.commit()


def test_downgrade_removes_schema_and_reupgrade_succeeds(db_engine, alembic_config):
    from alembic import command

    command.downgrade(alembic_config, "base")
    inspector = sa.inspect(db_engine)
    assert not {"projects", "jobs", "artifacts", "scene_overrides"} & set(
        inspector.get_table_names()
    )
    command.upgrade(alembic_config, "head")
    inspector = sa.inspect(db_engine)
    assert {"projects", "jobs", "artifacts", "scene_overrides"} <= set(inspector.get_table_names())
