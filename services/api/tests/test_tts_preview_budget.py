"""Durable rolling paid-TTS preview budget proofs."""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from app.core.tenancy import (
    PORTFOLIO_ORGANIZATION_ID,
    PORTFOLIO_PRINCIPAL_ID,
    PrincipalContext,
)
from app.domain.states import JobState
from app.models import Artifact, Job, Project, Review, TtsPreview
from app.services import tts_previews as preview_service
from app.services.tts_previews import PreviewConflict, create_preview
from instadescribe_contracts.provider import (
    TTS_BETA_MAX_PREVIEW_REQUESTS_PER_JOB,
    TTS_BETA_MAX_PREVIEW_REQUESTS_PER_ORGANIZATION,
    TTS_BETA_PREVIEW_WINDOW_SECS,
)
from sqlalchemy.orm import Session

requires_db = pytest.mark.skipif(
    not __import__("os").environ.get("INSTADESCRIBE_TEST_DATABASE_URL"),
    reason="INSTADESCRIBE_TEST_DATABASE_URL not set (use `make cloud-test` or CI)",
)

PRINCIPAL = PrincipalContext(
    organization_id=PORTFOLIO_ORGANIZATION_ID,
    principal_id=PORTFOLIO_PRINCIPAL_ID,
    principal_type="human",
    scopes=frozenset(),
)


def _open_review_job(session: Session, *, scene_ids: tuple[str, ...] = ("scene_1",)) -> Job:
    project = Project(
        organization_id=PORTFOLIO_ORGANIZATION_ID,
        name=f"Preview budget {uuid.uuid4()}",
    )
    session.add(project)
    session.flush()
    job = Job(
        organization_id=PORTFOLIO_ORGANIZATION_ID,
        project_id=project.id,
        pipeline_revision="preview-budget-test",
        status=JobState.READY_FOR_REVIEW.value,
        settings={},
    )
    session.add(job)
    session.flush()
    session.add_all(
        (
            Artifact(
                organization_id=PORTFOLIO_ORGANIZATION_ID,
                job_id=job.id,
                artifact_type="scenes_json",
                object_key=f"jobs/{job.id}/attempts/1/analysis/scenes.json",
                content_type="application/json",
                size_bytes=100,
                checksum_sha256="a" * 64,
                meta={"scene_ids": list(scene_ids), "scene_count": len(scene_ids)},
            ),
            Review(
                organization_id=PORTFOLIO_ORGANIZATION_ID,
                job_id=job.id,
                state="open",
            ),
        )
    )
    session.commit()
    return job


def _terminal_previews(
    session: Session,
    job: Job,
    count: int,
    *,
    created_at: datetime | None = None,
) -> list[TtsPreview]:
    instant = created_at or datetime.now(UTC) - timedelta(minutes=1)
    rows = [
        TtsPreview(
            organization_id=PORTFOLIO_ORGANIZATION_ID,
            job_id=job.id,
            requested_by_principal_id=PORTFOLIO_PRINCIPAL_ID,
            scene_id="scene_1",
            text=f"Durable attempt {index}.",
            voice="onyx",
            speed=1,
            request_hash=f"{index:064x}"[-64:],
            state="failed",
            error_code="preview_generation_failed",
            error_message="The TTS preview could not be generated.",
            created_at=instant,
            finished_at=instant,
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        for index in range(count)
    ]
    session.add_all(rows)
    session.commit()
    return rows


@requires_db
def test_job_preview_budget_counts_terminal_rows_only_inside_rolling_window(db_engine):
    with Session(db_engine) as session:
        job = _open_review_job(session)
        rows = _terminal_previews(session, job, TTS_BETA_MAX_PREVIEW_REQUESTS_PER_JOB)

        with pytest.raises(PreviewConflict) as raised:
            create_preview(
                session,
                PRINCIPAL,
                job.id,
                scene_id="scene_1",
                text="One request beyond the job ceiling.",
                voice="onyx",
                speed=1,
            )
        assert raised.value.code == "tts_preview_job_limit_exceeded"
        session.rollback()

        rows[0].created_at = datetime.now(UTC) - timedelta(seconds=TTS_BETA_PREVIEW_WINDOW_SECS + 5)
        rows[0].expires_at = datetime.now(UTC) + timedelta(hours=1)
        session.commit()
        accepted = create_preview(
            session,
            PRINCIPAL,
            job.id,
            scene_id="scene_1",
            text="The expired ledger slot is reusable.",
            voice="onyx",
            speed=1,
        )
        session.commit()

        assert accepted.state == "queued"
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(TtsPreview)
                .where(TtsPreview.job_id == job.id)
            )
            == TTS_BETA_MAX_PREVIEW_REQUESTS_PER_JOB + 1
        )


@requires_db
def test_organization_preview_budget_aggregates_requests_across_jobs(db_engine):
    with Session(db_engine) as session:
        ledger_job = _open_review_job(session)
        target_job = _open_review_job(session)
        _terminal_previews(
            session,
            ledger_job,
            TTS_BETA_MAX_PREVIEW_REQUESTS_PER_ORGANIZATION,
        )

        with pytest.raises(PreviewConflict) as raised:
            create_preview(
                session,
                PRINCIPAL,
                target_job.id,
                scene_id="scene_1",
                text="One request beyond the organization ceiling.",
                voice="onyx",
                speed=1,
            )
        assert raised.value.code == "tts_preview_organization_limit_exceeded"
        session.rollback()
        assert (
            session.scalar(
                sa.select(sa.func.count())
                .select_from(TtsPreview)
                .where(TtsPreview.organization_id == PORTFOLIO_ORGANIZATION_ID)
            )
            == TTS_BETA_MAX_PREVIEW_REQUESTS_PER_ORGANIZATION
        )


@requires_db
def test_organization_mutex_closes_concurrent_preview_budget_race(db_engine, monkeypatch):
    with Session(db_engine) as session:
        first_job = _open_review_job(session)
        second_job = _open_review_job(session)
        job_ids = (first_job.id, second_job.id)

    monkeypatch.setattr(preview_service, "PREVIEW_MAX_REQUESTS_PER_JOB", 10)
    monkeypatch.setattr(preview_service, "PREVIEW_MAX_REQUESTS_PER_ORGANIZATION", 1)
    ready = threading.Barrier(2)

    def submit(job_id: uuid.UUID) -> str:
        with Session(db_engine) as session:
            ready.wait(timeout=5)
            try:
                create_preview(
                    session,
                    PRINCIPAL,
                    job_id,
                    scene_id="scene_1",
                    text=f"Concurrent request for {job_id}.",
                    voice="onyx",
                    speed=1,
                )
                session.commit()
                return "accepted"
            except PreviewConflict as exc:
                session.rollback()
                return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(submit, job_ids))

    assert outcomes == ["accepted", "tts_preview_organization_limit_exceeded"]
    with Session(db_engine) as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(TtsPreview)) == 1
