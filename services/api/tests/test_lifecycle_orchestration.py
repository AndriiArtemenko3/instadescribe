"""Review-to-delivery orchestration, atomicity and tenant non-disclosure."""

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from app.api.browser.auth import BrowserPrincipal, require_browser_review_principal
from app.api.integrations.auth import authenticate_integration_principal
from app.api.integrations.lifecycle import router as lifecycle_router
from app.api.integrations.problems import IntegrationProblem, problem_response
from app.core.tenancy import (
    PORTFOLIO_ORGANIZATION_ID,
    PORTFOLIO_PRINCIPAL_ID,
    PrincipalContext,
)
from app.db.session import get_db
from app.domain.states import JobState
from app.models import (
    Artifact,
    AuditEvent,
    Deliverable,
    IdempotencyRecord,
    Job,
    JobEvent,
    Organization,
    Project,
    Render,
    Review,
    SceneOverride,
)
from app.services.lifecycle import (
    DELIVERABLE_FILE_NAMES,
    LifecycleConflict,
    LifecycleInvariantError,
    claim_render,
    finish_review,
    publish_staged_deliverables,
)
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from instadescribe_contracts.provider import TTS_BETA_MAX_APPROVED_SCENES_PER_REVIEW
from sqlalchemy.orm import Session

requires_db = pytest.mark.skipif(
    not os.environ.get("INSTADESCRIBE_TEST_DATABASE_URL"),
    reason="INSTADESCRIBE_TEST_DATABASE_URL not set (use `make cloud-test` or CI)",
)

PRINCIPAL = PrincipalContext(
    organization_id=PORTFOLIO_ORGANIZATION_ID,
    principal_id=PORTFOLIO_PRINCIPAL_ID,
    principal_type="human",
    scopes=frozenset({"jobs:read", "deliverables:read"}),
)
CONTENT_TYPES = {
    "mp4": "video/mp4",
    "mp3": "audio/mpeg",
    "srt": "application/x-subrip",
    "csv": "text/csv",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _job(
    session: Session,
    *,
    organization_id: uuid.UUID = PORTFOLIO_ORGANIZATION_ID,
    status: JobState = JobState.READY_FOR_REVIEW,
) -> Job:
    project = Project(
        organization_id=organization_id,
        name=f"Lifecycle {uuid.uuid4()}",
    )
    session.add(project)
    session.flush()
    job = Job(
        organization_id=organization_id,
        project_id=project.id,
        pipeline_revision="test",
        status=status.value,
        settings={},
    )
    session.add(job)
    session.flush()
    return job


def _open_review(
    session: Session,
    job: Job,
    decisions: tuple[str, ...] = ("approved", "rejected"),
) -> Review:
    scene_ids = [f"scene_{index}" for index in range(1, len(decisions) + 1)]
    session.add(
        Artifact(
            organization_id=job.organization_id,
            job_id=job.id,
            artifact_type="scenes_json",
            object_key=f"jobs/{job.id}/attempts/1/analysis/scenes.json",
            content_type="application/json",
            size_bytes=100,
            checksum_sha256="a" * 64,
            meta={"scene_ids": scene_ids, "scene_count": len(scene_ids)},
        )
    )
    review = Review(organization_id=job.organization_id, job_id=job.id, state="open")
    session.add(review)
    now = datetime.now(UTC)
    for scene_id, decision in zip(scene_ids, decisions, strict=True):
        session.add(
            SceneOverride(
                job_id=job.id,
                scene_id=scene_id,
                review_status=decision,
                reviewed_at=now if decision in {"approved", "rejected"} else None,
            )
        )
    session.commit()
    return review


def _stage(
    session: Session,
    job: Job,
    render: Render,
    formats: tuple[str, ...] = ("mp4", "mp3", "srt", "csv", "docx"),
) -> list[Deliverable]:
    rows = []
    for index, format_name in enumerate(formats, start=1):
        row = Deliverable(
            organization_id=job.organization_id,
            job_id=job.id,
            render_id=render.id,
            format=format_name,
            state="staged",
            object_key=f"deliverables/orgs/{job.organization_id}/jobs/{job.id}/{format_name}",
            version_id=f"version-{format_name}",
            content_type=CONTENT_TYPES[format_name],
            size_bytes=index * 100,
            checksum_sha256=format(index, "064x"),
        )
        session.add(row)
        rows.append(row)
    session.commit()
    return rows


@pytest.fixture()
def lifecycle_client(db_engine):
    application = FastAPI()
    application.include_router(lifecycle_router)

    @application.exception_handler(IntegrationProblem)
    async def integration_problem_handler(
        request: Request,
        exc: IntegrationProblem,
    ):
        return problem_response(request, exc)

    def database():
        with Session(db_engine) as session:
            yield session

    application.dependency_overrides[authenticate_integration_principal] = lambda: PRINCIPAL
    application.dependency_overrides[get_db] = database
    yield TestClient(application)


def test_lifecycle_router_keeps_locked_sdk_operation_ids():
    application = FastAPI()
    application.include_router(lifecycle_router)
    document = application.openapi()
    listed = document["paths"]["/api/integrations/v1/jobs/{jobId}/deliverables"]["get"]
    content = document["paths"]["/api/integrations/v1/deliverables/{deliverableId}/content"]["get"]
    assert (listed["operationId"], listed["x-sdk-public"]) == ("listDeliverables", True)
    assert (content["operationId"], content["x-sdk-public"]) == (
        "getDeliverableContent",
        True,
    )
    assert listed["parameters"][0]["schema"]["format"] == "uuid"
    assert content["parameters"][0]["schema"]["format"] == "uuid"
    assert content["responses"]["303"]["headers"]["Location"]["required"] is True


@requires_db
def test_finish_review_is_atomic_complete_and_naturally_idempotent(db_engine):
    with Session(db_engine) as session:
        job = _job(session)
        review = _open_review(session, job)
        result = finish_review(session, PRINCIPAL, job.id, zero_ad_confirmed=False)

        assert result.idempotent is False
        assert result.review.id == review.id
        assert (
            result.review.state,
            result.review.scene_count,
            result.review.approved_scene_count,
            result.review.rejected_scene_count,
        ) == ("completed", 2, 1, 1)
        assert result.review.locked_at == result.review.completed_at
        assert result.review.zero_ad_confirmed_at is None
        assert result.render.state == "queued"
        assert result.event is not None and result.event.event_type == "render.requested"
        assert result.event.payload["renderId"] == str(result.render.id)

        session.expire_all()
        stored_job = session.get(Job, job.id)
        assert (stored_job.status, stored_job.stage, stored_job.progress) == (
            "EXPORT_QUEUED",
            "render_queued",
            0,
        )
        assert stored_job.completed_at is None
        assert finish_review(
            session,
            PRINCIPAL,
            job.id,
            zero_ad_confirmed=False,
        ).idempotent
        assert (
            session.execute(
                sa.select(sa.func.count())
                .select_from(JobEvent)
                .where(JobEvent.job_id == job.id, JobEvent.event_type == "render.requested")
            ).scalar_one()
            == 1
        )


@requires_db
def test_finish_review_requires_all_decisions_and_explicit_zero_ad(db_engine):
    with Session(db_engine) as session:
        job = _job(session)
        review = _open_review(session, job, ("rejected", "edited"))
        with pytest.raises(LifecycleConflict, match="scene_decisions_incomplete"):
            finish_review(session, PRINCIPAL, job.id, zero_ad_confirmed=True)
        session.expire_all()
        assert session.get(Review, review.id).state == "open"
        assert session.get(Job, job.id).status == "READY_FOR_REVIEW"
        assert (
            session.execute(sa.select(Render).where(Render.job_id == job.id)).scalar_one_or_none()
            is None
        )

        override = session.execute(
            sa.select(SceneOverride).where(
                SceneOverride.job_id == job.id,
                SceneOverride.scene_id == "scene_2",
            )
        ).scalar_one()
        override.review_status = "rejected"
        override.reviewed_at = datetime.now(UTC)
        session.commit()

        with pytest.raises(LifecycleConflict, match="zero_ad_confirmation_required"):
            finish_review(session, PRINCIPAL, job.id, zero_ad_confirmed=False)
        result = finish_review(session, PRINCIPAL, job.id, zero_ad_confirmed=True)
        assert result.review.approved_scene_count == 0
        assert result.review.rejected_scene_count == 2
        assert result.review.zero_ad_confirmed_at is not None


@requires_db
def test_finish_review_caps_paid_tts_calls_before_creating_render_intent(db_engine):
    limit = TTS_BETA_MAX_APPROVED_SCENES_PER_REVIEW
    with Session(db_engine) as session:
        bounded_job = _job(session)
        _open_review(session, bounded_job, ("approved",) * limit)
        bounded = finish_review(
            session,
            PRINCIPAL,
            bounded_job.id,
            zero_ad_confirmed=False,
        )
        assert bounded.review.approved_scene_count == limit
        assert bounded.render.state == "queued"

        excessive_job = _job(session)
        excessive_review = _open_review(session, excessive_job, ("approved",) * (limit + 1))
        with pytest.raises(LifecycleConflict) as raised:
            finish_review(
                session,
                PRINCIPAL,
                excessive_job.id,
                zero_ad_confirmed=False,
            )
        assert raised.value.code == "tts_review_limit_exceeded"

        session.expire_all()
        assert session.get(Review, excessive_review.id).state == "open"
        assert session.get(Job, excessive_job.id).status == JobState.READY_FOR_REVIEW.value
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(Render)
                .where(Render.job_id == excessive_job.id)
            )
            == 0
        )
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(JobEvent)
                .where(JobEvent.job_id == excessive_job.id)
            )
            == 0
        )


@requires_db
def test_finish_review_rejects_orphan_decision_and_missing_manifest(db_engine):
    with Session(db_engine) as session:
        job = _job(session)
        _open_review(session, job, ("approved",))
        session.add(
            SceneOverride(
                job_id=job.id,
                scene_id="scene_99",
                review_status="rejected",
                reviewed_at=datetime.now(UTC),
            )
        )
        session.commit()
        with pytest.raises(LifecycleConflict, match="scene_decisions_invalid"):
            finish_review(session, PRINCIPAL, job.id, zero_ad_confirmed=False)

        session.execute(sa.delete(Artifact).where(Artifact.job_id == job.id))
        session.execute(sa.delete(SceneOverride).where(SceneOverride.scene_id == "scene_99"))
        session.commit()
        with pytest.raises(LifecycleInvariantError, match="scene_manifest_unavailable"):
            finish_review(session, PRINCIPAL, job.id, zero_ad_confirmed=False)
        assert session.get(Job, job.id).status == "READY_FOR_REVIEW"


@requires_db
def test_fenced_publish_rolls_back_partial_set_then_atomically_reveals_five(db_engine):
    with Session(db_engine) as session:
        job = _job(session)
        _open_review(session, job)
        finished = finish_review(session, PRINCIPAL, job.id, zero_ad_confirmed=False)
        claimed = claim_render(
            session,
            PRINCIPAL,
            job.id,
            worker_id="render-worker-1",
            lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        fence = claimed.fence_token
        _stage(session, job, finished.render, ("mp4", "mp3", "srt", "csv"))

        with pytest.raises(LifecycleInvariantError, match="deliverable_set_incomplete"):
            publish_staged_deliverables(
                session,
                PRINCIPAL,
                job.id,
                worker_id="render-worker-1",
                fence_token=fence,
            )
        session.expire_all()
        assert session.get(Job, job.id).status == "EXPORTING"
        assert session.get(Render, finished.render.id).state == "rendering"
        assert {
            row.state
            for row in session.execute(
                sa.select(Deliverable).where(Deliverable.job_id == job.id)
            ).scalars()
        } == {"staged"}
        assert (
            session.execute(
                sa.select(JobEvent).where(
                    JobEvent.job_id == job.id,
                    JobEvent.event_type == "job.completed",
                )
            ).scalar_one_or_none()
            is None
        )

        _stage(session, job, finished.render, ("docx",))
        published = publish_staged_deliverables(
            session,
            PRINCIPAL,
            job.id,
            worker_id="render-worker-1",
            fence_token=fence,
        )
        assert published.idempotent is False
        assert [row.format for row in published.deliverables] == [
            "mp4",
            "mp3",
            "srt",
            "csv",
            "docx",
        ]
        assert len({row.published_at for row in published.deliverables}) == 1
        assert published.render.integrity_manifest["deliverableCount"] == 5
        session.expire_all()
        assert session.get(Job, job.id).status == "COMPLETED"
        assert session.get(Render, finished.render.id).state == "completed"
        replay = publish_staged_deliverables(
            session,
            PRINCIPAL,
            job.id,
            worker_id="render-worker-1",
            fence_token=fence,
        )
        assert replay.idempotent is True and replay.event.id == published.event.id


@requires_db
def test_stale_render_fence_cannot_publish(db_engine):
    with Session(db_engine) as session:
        job = _job(session)
        _open_review(session, job)
        finished = finish_review(session, PRINCIPAL, job.id, zero_ad_confirmed=False)
        claimed = claim_render(
            session,
            PRINCIPAL,
            job.id,
            worker_id="render-worker-1",
            lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        _stage(session, job, finished.render)
        with pytest.raises(LifecycleConflict, match="render_fence_lost"):
            publish_staged_deliverables(
                session,
                PRINCIPAL,
                job.id,
                worker_id="stale-worker",
                fence_token=claimed.fence_token - 1,
            )
        assert session.get(Job, job.id).status == "EXPORTING"


@pytest.mark.parametrize(
    "invalid_identity",
    [
        "empty_object_key",
        "foreign_prefix",
        "path_traversal",
        "empty_version",
    ],
)
@requires_db
def test_publish_rejects_invalid_object_identity_without_partial_visibility(
    db_engine,
    invalid_identity,
):
    with Session(db_engine) as session:
        job = _job(session)
        _open_review(session, job)
        finished = finish_review(session, PRINCIPAL, job.id, zero_ad_confirmed=False)
        claimed = claim_render(
            session,
            PRINCIPAL,
            job.id,
            worker_id="render-worker-identity",
            lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        rows = _stage(session, job, finished.render)
        if invalid_identity == "empty_object_key":
            rows[0].object_key = ""
        elif invalid_identity == "foreign_prefix":
            rows[0].object_key = "deliverables/orgs/foreign/jobs/foreign/output.mp4"
        elif invalid_identity == "path_traversal":
            rows[
                0
            ].object_key = (
                f"deliverables/orgs/{job.organization_id}/jobs/{job.id}/../foreign/output.mp4"
            )
        else:
            rows[0].version_id = ""
        session.commit()

        with pytest.raises(LifecycleInvariantError, match="deliverable_identity_invalid"):
            publish_staged_deliverables(
                session,
                PRINCIPAL,
                job.id,
                worker_id="render-worker-identity",
                fence_token=claimed.fence_token,
            )
        session.expire_all()
        assert session.get(Job, job.id).status == "EXPORTING"
        assert session.get(Render, finished.render.id).state == "rendering"
        assert {
            row.state
            for row in session.execute(
                sa.select(Deliverable).where(Deliverable.job_id == job.id)
            ).scalars()
        } == {"staged"}
        assert (
            session.execute(
                sa.select(JobEvent).where(
                    JobEvent.job_id == job.id,
                    JobEvent.event_type == "job.completed",
                )
            ).scalar_one_or_none()
            is None
        )


@requires_db
def test_lifecycle_routes_hide_cross_tenant_ids_and_sign_only_published_content(
    db_engine,
    lifecycle_client,
    monkeypatch,
):
    foreign_organization_id = uuid.uuid4()
    with Session(db_engine) as session:
        session.add(
            Organization(
                id=foreign_organization_id,
                slug=f"foreign-{uuid.uuid4().hex[:10]}",
                name="Foreign organization",
            )
        )
        session.commit()

        own_job = _job(session)
        _open_review(session, own_job)
        own_finished = finish_review(session, PRINCIPAL, own_job.id, zero_ad_confirmed=False)
        own_claim = claim_render(
            session,
            PRINCIPAL,
            own_job.id,
            worker_id="render-worker-own",
            lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        _stage(session, own_job, own_finished.render)
        own_published = publish_staged_deliverables(
            session,
            PRINCIPAL,
            own_job.id,
            worker_id="render-worker-own",
            fence_token=own_claim.fence_token,
        )

        foreign_principal = PrincipalContext(
            organization_id=foreign_organization_id,
            principal_id=PORTFOLIO_PRINCIPAL_ID,
            principal_type="human",
            scopes=frozenset({"*"}),
        )
        foreign_job = _job(session, organization_id=foreign_organization_id)
        _open_review(session, foreign_job)
        foreign_finished = finish_review(
            session,
            foreign_principal,
            foreign_job.id,
            zero_ad_confirmed=False,
        )
        foreign_claim = claim_render(
            session,
            foreign_principal,
            foreign_job.id,
            worker_id="render-worker-foreign",
            lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        _stage(session, foreign_job, foreign_finished.render)
        foreign_published = publish_staged_deliverables(
            session,
            foreign_principal,
            foreign_job.id,
            worker_id="render-worker-foreign",
            fence_token=foreign_claim.fence_token,
        )
        own_deliverable = own_published.deliverables[0]
        foreign_deliverable = foreign_published.deliverables[0]
        own_job_id = own_job.id
        foreign_job_id = foreign_job.id
        own_deliverable_id = own_deliverable.id
        own_object_key = own_deliverable.object_key
        own_version_id = own_deliverable.version_id
        foreign_deliverable_id = foreign_deliverable.id

    signer_calls = []
    import app.api.integrations.lifecycle as lifecycle_routes

    monkeypatch.setattr(
        lifecycle_routes,
        "generate_download_url",
        lambda object_key, *, version_id, expires_in: (
            signer_calls.append((object_key, version_id, expires_in))
            or "https://media.example.test/version-pinned-download"
        ),
    )

    review = lifecycle_client.get(f"/api/integrations/v1/jobs/{own_job_id}/review")
    render = lifecycle_client.get(f"/api/integrations/v1/jobs/{own_job_id}/render")
    listed = lifecycle_client.get(f"/api/integrations/v1/jobs/{own_job_id}/deliverables")
    assert review.status_code == render.status_code == listed.status_code == 200
    assert review.json()["state"] == render.json()["state"] == "completed"
    assert review.json()["createdAt"].endswith("Z")
    assert render.json()["createdAt"].endswith("Z")
    assert listed.json()["completedSet"] is True
    assert all(item["createdAt"].endswith("Z") for item in listed.json()["items"])
    assert [item["kind"] for item in listed.json()["items"]] == [
        "mp4",
        "mp3",
        "srt",
        "csv",
        "docx",
    ]
    assert [item["fileName"] for item in listed.json()["items"]] == [
        DELIVERABLE_FILE_NAMES[kind] for kind in ("mp4", "mp3", "srt", "csv", "docx")
    ]

    redirect = lifecycle_client.get(
        f"/api/integrations/v1/deliverables/{own_deliverable_id}/content",
        follow_redirects=False,
    )
    assert redirect.status_code == 303
    assert redirect.headers["location"] == "https://media.example.test/version-pinned-download"
    assert redirect.headers["cache-control"] == "private, no-store"
    assert signer_calls == [
        (own_object_key, own_version_id, 300),
    ]

    missing_job = uuid.uuid4()
    missing_deliverable = uuid.uuid4()
    for path in (
        f"/api/integrations/v1/jobs/{foreign_job_id}/review",
        f"/api/integrations/v1/jobs/{foreign_job_id}/render",
        f"/api/integrations/v1/jobs/{foreign_job_id}/deliverables",
        f"/api/integrations/v1/jobs/{missing_job}/review",
        f"/api/integrations/v1/jobs/{missing_job}/render",
        f"/api/integrations/v1/jobs/{missing_job}/deliverables",
        f"/api/integrations/v1/deliverables/{foreign_deliverable_id}/content",
        f"/api/integrations/v1/deliverables/{missing_deliverable}/content",
    ):
        response = lifecycle_client.get(path, follow_redirects=False)
        assert response.status_code == 404, (path, response.text)
        assert response.json()["code"] == "not_found"
    assert len(signer_calls) == 1


@requires_db
def test_partial_deliverables_are_not_listed_or_signed(db_engine, lifecycle_client, monkeypatch):
    with Session(db_engine) as session:
        job = _job(session)
        _open_review(session, job)
        finished = finish_review(session, PRINCIPAL, job.id, zero_ad_confirmed=False)
        staged = _stage(session, job, finished.render, ("srt",))[0]
        job_id = job.id
        staged_id = staged.id

    import app.api.integrations.lifecycle as lifecycle_routes

    monkeypatch.setattr(
        lifecycle_routes,
        "generate_download_url",
        lambda *args, **kwargs: pytest.fail("a staged object must never be signed"),
    )
    listed = lifecycle_client.get(f"/api/integrations/v1/jobs/{job_id}/deliverables")
    assert listed.status_code == 409
    assert listed.json()["code"] == "deliverables_not_ready"
    content = lifecycle_client.get(
        f"/api/integrations/v1/deliverables/{staged_id}/content",
        follow_redirects=False,
    )
    assert content.status_code == 404
    assert content.json()["code"] == "not_found"


@requires_db
def test_browser_finish_failure_rolls_back_everything_then_replays_exactly(
    api_db_client,
    db_engine,
    monkeypatch,
):
    import app.api.browser.v1 as browser_routes
    from app.main import app

    with Session(db_engine) as session:
        job = _job(session)
        review = _open_review(session, job)
        job_id = job.id
        review_id = review.id

    browser_principal = BrowserPrincipal(
        subject="browser-reviewer",
        email="reviewer@example.test",
        display_name="Review Owner",
        organization_id=PORTFOLIO_ORGANIZATION_ID,
        organization_slug="portfolio",
        principal_id=PORTFOLIO_PRINCIPAL_ID,
        role="owner",
        mfa_verified=True,
    )
    app.dependency_overrides[require_browser_review_principal] = lambda: browser_principal
    real_complete = browser_routes.idempotency.complete

    def fail_before_commit(*args, **kwargs):
        raise sa.exc.SQLAlchemyError("forced pre-commit failure")

    monkeypatch.setattr(browser_routes.idempotency, "complete", fail_before_commit)
    headers = {"Idempotency-Key": "finish-review-atomic-1"}
    path = f"/api/app/v1/jobs/{job_id}/review/finish"
    try:
        failed = api_db_client.post(
            path,
            json={"zeroAdConfirmed": False},
            headers=headers,
        )
        assert failed.status_code == 503

        with Session(db_engine) as session:
            assert session.get(Job, job_id).status == "READY_FOR_REVIEW"
            assert session.get(Review, review_id).state == "open"
            assert (
                session.execute(
                    sa.select(Render).where(Render.job_id == job_id)
                ).scalar_one_or_none()
                is None
            )
            assert (
                session.execute(
                    sa.select(JobEvent).where(JobEvent.job_id == job_id)
                ).scalar_one_or_none()
                is None
            )
            assert (
                session.execute(
                    sa.select(IdempotencyRecord).where(
                        IdempotencyRecord.organization_id == PORTFOLIO_ORGANIZATION_ID,
                        IdempotencyRecord.key == "finish-review-atomic-1",
                    )
                ).scalar_one_or_none()
                is None
            )
            assert (
                session.execute(
                    sa.select(AuditEvent).where(AuditEvent.resource_id == str(review_id))
                ).scalar_one_or_none()
                is None
            )

        monkeypatch.setattr(browser_routes.idempotency, "complete", real_complete)
        completed = api_db_client.post(
            path,
            json={"zeroAdConfirmed": False},
            headers=headers,
        )
        assert completed.status_code == 200, completed.text
        replay = api_db_client.post(
            path,
            json={"zeroAdConfirmed": False},
            headers=headers,
        )
        assert replay.status_code == 200
        assert replay.headers["idempotent-replayed"] == "true"
        assert replay.json() == completed.json()

        with Session(db_engine) as session:
            record = session.execute(
                sa.select(IdempotencyRecord).where(
                    IdempotencyRecord.organization_id == PORTFOLIO_ORGANIZATION_ID,
                    IdempotencyRecord.key == "finish-review-atomic-1",
                )
            ).scalar_one()
            assert record.state == "completed"
            assert record.response_body == completed.json()
            assert session.get(Job, job_id).status == "EXPORT_QUEUED"
            assert (
                session.execute(
                    sa.select(sa.func.count()).select_from(Render).where(Render.job_id == job_id)
                ).scalar_one()
                == 1
            )
            assert (
                session.execute(
                    sa.select(sa.func.count())
                    .select_from(JobEvent)
                    .where(JobEvent.job_id == job_id, JobEvent.event_type == "render.requested")
                ).scalar_one()
                == 1
            )
            audit = session.execute(
                sa.select(AuditEvent).where(AuditEvent.resource_id == str(review_id))
            ).scalar_one()
            assert audit.organization_id == PORTFOLIO_ORGANIZATION_ID
            assert audit.actor_principal_id == PORTFOLIO_PRINCIPAL_ID
            assert (audit.action, audit.resource_type, audit.details) == (
                "review.finished",
                "review",
                {"outcome": "succeeded"},
            )
    finally:
        app.dependency_overrides.pop(require_browser_review_principal, None)
