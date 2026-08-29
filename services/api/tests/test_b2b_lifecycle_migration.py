"""PostgreSQL proofs for the tenant-safe B2B lifecycle schema."""

import os
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from app.core.tenancy import PORTFOLIO_ORGANIZATION_ID
from app.db.base import Base
from app.domain.states import JobState
from app.models import (
    Asset,
    Deliverable,
    Job,
    JobEvent,
    Organization,
    OrganizationJobCapacity,
    OrganizationMembership,
    OrganizationQuota,
    OrganizationUsagePeriod,
    Principal,
    Project,
    QuotaReservation,
    Render,
    Review,
    WebhookDelivery,
    WebhookEndpoint,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.skipif(
    not os.environ.get("INSTADESCRIBE_TEST_DATABASE_URL"),
    reason="INSTADESCRIBE_TEST_DATABASE_URL not set (use `make cloud-test` or CI)",
)


def _organization(session: Session, label: str) -> Organization:
    organization = Organization(slug=f"org-{label}-{uuid.uuid4().hex[:8]}", name=label)
    session.add(organization)
    session.flush()
    return organization


def _job(
    session: Session,
    organization: Organization,
    *,
    status: JobState = JobState.AWAITING_UPLOAD,
) -> tuple[Project, Job]:
    project = Project(organization_id=organization.id, name="Lifecycle source")
    session.add(project)
    session.flush()
    job = Job(
        organization_id=organization.id,
        project_id=project.id,
        pipeline_revision="test",
        status=status.value,
        settings={},
    )
    session.add(job)
    session.flush()
    return project, job


def _reviewer(session: Session, organization: Organization) -> Principal:
    principal = Principal(kind="human", display_name="Review Owner")
    session.add(principal)
    session.flush()
    session.add(
        OrganizationMembership(
            organization_id=organization.id,
            principal_id=principal.id,
            role="owner",
        )
    )
    session.flush()
    return principal


def test_populated_job_tenancy_backfill_and_0007_round_trip(migrated_db, alembic_config):
    from alembic import command

    engine = sa.create_engine(migrated_db)
    organization_id = uuid.uuid4()
    project_id = uuid.uuid4()
    job_id = uuid.uuid4()
    try:
        command.downgrade(alembic_config, "0006_organization_tenancy")
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO organizations (id, slug, name) "
                    "VALUES (:id, :slug, 'Migration tenant')"
                ),
                {"id": organization_id, "slug": f"migration-{organization_id.hex[:8]}"},
            )
            connection.execute(
                sa.text(
                    "INSERT INTO projects (id, organization_id, name) "
                    "VALUES (:id, :organization_id, 'Existing source')"
                ),
                {"id": project_id, "organization_id": organization_id},
            )
            connection.execute(
                sa.text(
                    "INSERT INTO jobs "
                    "(id, project_id, pipeline_revision, status, settings) "
                    "VALUES (:id, :project_id, 'pre-0007', 'AWAITING_UPLOAD', '{}'::jsonb)"
                ),
                {"id": job_id, "project_id": project_id},
            )

        command.upgrade(alembic_config, "head")
        with engine.connect() as connection:
            assert (
                connection.execute(
                    sa.text("SELECT organization_id FROM jobs WHERE id = :id"), {"id": job_id}
                ).scalar_one()
                == organization_id
            )
            assert (
                connection.execute(
                    sa.text("SELECT count(*) FROM organization_quotas WHERE organization_id = :id"),
                    {"id": organization_id},
                ).scalar_one()
                == 1
            )
            assert connection.execute(
                sa.text(
                    "SELECT awaiting_upload_jobs, queued_jobs, processing_jobs "
                    "FROM organization_job_capacity WHERE organization_id = :id"
                ),
                {"id": organization_id},
            ).one() == (1, 0, 0)
            index_definition = connection.execute(
                sa.text(
                    "SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_jobs_one_compute_active'"
                )
            ).scalar_one()
            assert "(organization_id)" in index_definition
            assert "PROCESSING" in index_definition
            assert "QUEUED" not in index_definition

        command.downgrade(alembic_config, "0006_organization_tenancy")
        inspector = sa.inspect(engine)
        assert "organization_id" not in {column["name"] for column in inspector.get_columns("jobs")}
        assert "external_id" not in {column["name"] for column in inspector.get_columns("projects")}
        assert "reviews" not in inspector.get_table_names()
        with engine.connect() as connection:
            index_definition = connection.execute(
                sa.text(
                    "SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_jobs_one_compute_active'"
                )
            ).scalar_one()
            assert "((true))" in index_definition
    finally:
        command.upgrade(alembic_config, "head")
        with engine.begin() as connection:
            connection.execute(sa.text("DELETE FROM projects WHERE id = :id"), {"id": project_id})
            connection.execute(
                sa.text("DELETE FROM organizations WHERE id = :id"), {"id": organization_id}
            )
        engine.dispose()


def test_composite_project_job_fk_and_per_organization_active_slot(db_engine):
    with Session(db_engine) as session:
        first = _organization(session, "first")
        second = _organization(session, "second")
        project, _ = _job(session, first, status=JobState.PROCESSING)
        _job(session, second, status=JobState.PROCESSING)
        session.commit()

        session.add(
            Job(
                organization_id=second.id,
                project_id=project.id,
                pipeline_revision="test",
                status=JobState.AWAITING_UPLOAD.value,
                settings={},
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        with pytest.raises(IntegrityError):
            _job(session, first, status=JobState.PROCESSING)


def test_reconciliation_ids_are_unique_only_within_an_organization(db_engine):
    with Session(db_engine) as session:
        first = _organization(session, "reconcile-a")
        second = _organization(session, "reconcile-b")
        p1 = Project(organization_id=first.id, name="A", external_id="cms-42")
        p2 = Project(organization_id=second.id, name="B", external_id="cms-42")
        session.add_all([p1, p2])
        session.flush()
        session.add_all(
            [
                Job(
                    organization_id=first.id,
                    project_id=p1.id,
                    pipeline_revision="test",
                    status=JobState.AWAITING_UPLOAD.value,
                    settings={},
                    client_reference="batch-7",
                ),
                Job(
                    organization_id=second.id,
                    project_id=p2.id,
                    pipeline_revision="test",
                    status=JobState.AWAITING_UPLOAD.value,
                    settings={},
                    client_reference="batch-7",
                ),
            ]
        )
        session.commit()
        session.add(Project(organization_id=first.id, name="duplicate", external_id="cms-42"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        p3 = Project(organization_id=first.id, name="another source", external_id="cms-43")
        session.add(p3)
        session.flush()
        session.add(
            Job(
                organization_id=first.id,
                project_id=p3.id,
                pipeline_revision="test",
                status=JobState.AWAITING_UPLOAD.value,
                settings={},
                client_reference="batch-7",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_asset_is_job_scoped_version_pinned_and_product_bounded(db_engine):
    with Session(db_engine) as session:
        first = _organization(session, "asset-a")
        second = _organization(session, "asset-b")
        _, job = _job(session, first)
        session.commit()

        session.add(
            Asset(
                organization_id=second.id,
                job_id=job.id,
                asset_type="source_transcript",
                status="validated",
                object_key="uploads/wrong/transcript/captions.vtt",
                content_type="text/vtt",
                size_bytes=20,
                version_id="v1",
                etag="etag",
                transcript_format="vtt",
                validated_at=datetime.now(UTC),
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        session.add(
            Asset(
                organization_id=first.id,
                job_id=job.id,
                asset_type="source_transcript",
                object_key=f"uploads/orgs/{first.id}/jobs/{job.id}/transcript/missing-format.vtt",
                content_type="text/vtt",
                size_bytes=20,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        session.add(
            Asset(
                organization_id=first.id,
                job_id=job.id,
                asset_type="source_transcript",
                status="validated",
                object_key=f"uploads/orgs/{first.id}/jobs/{job.id}/transcript/captions.vtt",
                content_type="text/vtt",
                size_bytes=10 * 1024 * 1024,
                version_id="version-1",
                etag="opaque-etag",
                checksum_sha256="a" * 64,
                transcript_format="vtt",
                validated_at=datetime.now(UTC),
            )
        )
        session.commit()

        session.add(
            Asset(
                organization_id=first.id,
                job_id=job.id,
                asset_type="source_video",
                object_key=f"uploads/orgs/{first.id}/jobs/{job.id}/source/too-large.mp4",
                content_type="video/mp4",
                size_bytes=1024**3 + 1,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_zero_ad_review_render_and_deliverable_linkage(db_engine):
    now = datetime.now(UTC)
    with Session(db_engine) as session:
        organization = _organization(session, "review")
        principal = _reviewer(session, organization)
        _, job = _job(session, organization, status=JobState.READY_FOR_REVIEW)
        session.commit()

        session.add(
            Review(
                organization_id=organization.id,
                job_id=job.id,
                state="completed",
                scene_count=2,
                approved_scene_count=0,
                rejected_scene_count=2,
                locked_at=now,
                completed_at=now,
                completed_by_principal_id=principal.id,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        review = Review(
            organization_id=organization.id,
            job_id=job.id,
            state="completed",
            scene_count=2,
            approved_scene_count=0,
            rejected_scene_count=2,
            locked_at=now,
            completed_at=now,
            completed_by_principal_id=principal.id,
            zero_ad_confirmed_at=now,
        )
        session.add(review)
        session.flush()
        render = Render(
            organization_id=organization.id,
            job_id=job.id,
            review_id=review.id,
        )
        session.add(render)
        session.flush()

        for extension in ("mp4", "mp3", "srt", "csv", "docx"):
            session.add(
                Deliverable(
                    organization_id=organization.id,
                    job_id=job.id,
                    render_id=render.id,
                    format=extension,
                    object_key=f"deliverables/{render.id}/bundle.{extension}",
                    version_id=f"version-{extension}",
                    content_type="application/octet-stream",
                    size_bytes=0,
                    checksum_sha256="0" * 64,
                )
            )
        session.commit()
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(Deliverable)
                .where(Deliverable.render_id == render.id)
            )
            == 5
        )


def test_outbox_webhook_delivery_and_endpoint_tenant_constraints(db_engine):
    with Session(db_engine) as session:
        first = _organization(session, "hooks-a")
        second = _organization(session, "hooks-b")
        _, job = _job(session, first)
        endpoint = WebhookEndpoint(
            organization_id=first.id,
            endpoint_url="https://customer.example/webhooks/instadescribe",
            signing_secret_ref="arn:aws:secretsmanager:eu-west-2:123:secret:hook",
        )
        event = JobEvent(
            organization_id=first.id,
            job_id=job.id,
            event_type="job.needs_review",
            job_version=1,
            payload={"jobId": str(job.id), "state": "needs_review"},
        )
        session.add_all([endpoint, event])
        session.flush()
        session.add(
            WebhookDelivery(
                organization_id=first.id,
                endpoint_id=endpoint.id,
                event_id=event.id,
            )
        )
        session.commit()

        session.add(
            WebhookDelivery(
                organization_id=second.id,
                endpoint_id=endpoint.id,
                event_id=event.id,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        session.add(
            WebhookEndpoint(
                organization_id=second.id,
                endpoint_url="http://169.254.169.254/latest/meta-data",
                signing_secret_ref="ref",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_quota_defaults_retention_caps_and_reservation_state(db_engine):
    with Session(db_engine) as session:
        organization = _organization(session, "quota")
        _, job = _job(session, organization)
        # The 0008 job trigger atomically onboards quota/capacity rows with
        # the first job so no writer can bypass the state caps.
        quota = session.get(OrganizationQuota, organization.id)
        capacity = session.get(OrganizationJobCapacity, organization.id)
        assert quota is not None
        assert capacity is not None
        period = OrganizationUsagePeriod(
            organization_id=organization.id,
            period_start=date(2026, 8, 1),
            period_end=date(2026, 9, 1),
        )
        session.add(period)
        session.flush()
        reservation = QuotaReservation(
            organization_id=organization.id,
            usage_period_id=period.id,
            job_id=job.id,
            reserved_seconds=60,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        session.add(reservation)
        session.commit()
        session.refresh(quota)
        assert quota.monthly_media_seconds == 36_000
        assert (
            quota.max_processing_jobs,
            quota.max_awaiting_upload_jobs,
            quota.max_queued_jobs,
        ) == (1, 5, 10)
        assert (
            quota.source_retention_days,
            quota.deliverable_retention_days,
            quota.metadata_retention_days,
        ) == (30, 90, 365)
        assert (
            capacity.awaiting_upload_jobs,
            capacity.queued_jobs,
            capacity.processing_jobs,
        ) == (1, 0, 0)

        quota.source_retention_days = 31
        with pytest.raises(IntegrityError):
            session.commit()


def test_migration_and_sqlalchemy_metadata_are_drift_free(db_engine):
    with db_engine.connect() as connection:
        context = MigrationContext.configure(connection)
        assert compare_metadata(context, Base.metadata) == []


def test_legacy_defaults_remain_portfolio_scoped(db_engine):
    with Session(db_engine) as session:
        project = Project(name="Legacy-compatible")
        session.add(project)
        session.flush()
        job = Job(
            project_id=project.id,
            pipeline_revision="test",
            status=JobState.AWAITING_UPLOAD.value,
            settings={},
        )
        session.add(job)
        session.commit()
        assert project.organization_id == PORTFOLIO_ORGANIZATION_ID
        assert job.organization_id == PORTFOLIO_ORGANIZATION_ID
