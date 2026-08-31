"""Deterministic Browser-to-worker acceptance for local investigations.

This test deliberately crosses the production seams that smaller contract
tests isolate: Browser API creation, a versioned direct S3 upload, the
investigation-only SQS queue, the real fenced worker consumer, its isolated
fixture child, and the analyst decision/report boundary.  The fixture runtime
does not call a model or the network.
"""

from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import uuid
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
from app.api.browser.auth import (
    BrowserPrincipal,
    require_browser_access_principal,
    require_browser_review_principal,
    require_browser_upload_principal,
)
from app.core.config import get_settings
from app.core.tenancy import PORTFOLIO_ORGANIZATION_ID, PORTFOLIO_PRINCIPAL_ID
from app.db.session import reset_engine_caches
from app.domain.states import JobState
from app.main import app
from app.models import Asset, Investigation, Job, JobEvent, SourceRecord
from app.services.s3 import reset_s3_caches
from app.services.sqs import reset_sqs_caches
from fastapi.testclient import TestClient
from instadescribe_worker.config import get_worker_settings, reset_worker_settings
from instadescribe_worker.consumer import reset_worker_caches, run_once
from instadescribe_worker.investigation import parent_trace_id
from queue_support import make_queue_pair
from sqlalchemy.orm import Session

pytestmark = pytest.mark.skipif(
    not (
        os.environ.get("INSTADESCRIBE_TEST_DATABASE_URL")
        and os.environ.get("INSTADESCRIBE_TEST_S3")
    ),
    reason="PostgreSQL and LocalStack acceptance gates are not enabled",
)


def _direct_upload(client: httpx.Client, contract: dict, body: bytes) -> None:
    response = client.post(
        contract["url"],
        data=contract["fields"],
        files={"file": ("rights-cleared-fixture.mp4", body, "video/mp4")},
    )
    assert response.status_code in {200, 201, 204}, response.text


def _queue_depth(sqs, queue_url: str) -> tuple[int, int]:
    attributes = sqs.get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
    )["Attributes"]
    return (
        int(attributes.get("ApproximateNumberOfMessages", "0")),
        int(attributes.get("ApproximateNumberOfMessagesNotVisible", "0")),
    )


def _write_video(path: Path) -> bytes:
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=30:size=64x64:rate=1",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(path),
        ],
        check=True,
        timeout=30,
    )
    return path.read_bytes()


@pytest.mark.parametrize(
    ("fixture_scenario", "expected_abstain"),
    (("supportive", False), ("abstention", True)),
)
def test_local_investigation_browser_to_report_journey(
    db_session,
    worker_env,
    aws_resources,
    monkeypatch,
    tmp_path,
    fixture_scenario,
    expected_abstain,
):
    """Create → upload → isolated consume → review → evidence-backed report."""

    sqs = aws_resources["sqs"]
    queue_base = f"instadescribe-investigation-e2e-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    investigation_queue_url, investigation_dlq_url = make_queue_pair(
        sqs,
        queue_base,
        visibility="30",
    )
    local_http = httpx.Client(timeout=10, trust_env=False)

    monkeypatch.setenv("DATABASE_URL", worker_env.database_url)
    # The API provider remains an audio-description deployment setting and
    # deliberately rejects ``local``. Investigation jobs are routed by their
    # workflow kind to a separately configured local worker below.
    monkeypatch.setenv("INSTADESCRIBE_PROVIDER", "fake")
    monkeypatch.setenv("INSTADESCRIBE_INVESTIGATION_RUNTIME", "fixture")
    monkeypatch.setenv("INSTADESCRIBE_TEST_FIXTURE_RUNTIME", "true")
    monkeypatch.setenv("INSTADESCRIBE_TEST_FIXTURE_SCENARIO", fixture_scenario)
    # Port 9 has no model service in the test stack. A successful isolated
    # child therefore proves the explicit fixture path does not contact Ollama.
    monkeypatch.setenv("INSTADESCRIBE_OLLAMA_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("INSTADESCRIBE_MEDIA_BUCKET", aws_resources["bucket"])
    monkeypatch.setenv("INSTADESCRIBE_WORK_QUEUE_URL", aws_resources["queue_url"])
    monkeypatch.setenv(
        "INSTADESCRIBE_INVESTIGATION_QUEUE_URL",
        investigation_queue_url,
    )
    monkeypatch.setenv(
        "INSTADESCRIBE_S3_ENDPOINT_PUBLIC",
        worker_env.s3_endpoint_internal or "http://localhost:4566",
    )
    monkeypatch.setenv("INSTADESCRIBE_S3_FORCE_PATH_STYLE", "1")
    monkeypatch.setenv("INSTADESCRIBE_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("INSTADESCRIBE_SUBPROCESS_TIMEOUT_SECS", "30")
    monkeypatch.setenv("INSTADESCRIBE_GRACE_SECS", "1")

    get_settings.cache_clear()
    reset_engine_caches()
    reset_s3_caches()
    reset_sqs_caches()
    reset_worker_settings()
    reset_worker_caches()

    browser = BrowserPrincipal(
        subject="investigation-e2e-owner",
        email="owner@example.test",
        display_name="Investigation E2E Owner",
        organization_id=PORTFOLIO_ORGANIZATION_ID,
        organization_slug="portfolio",
        principal_id=PORTFOLIO_PRINCIPAL_ID,
        role="owner",
        mfa_verified=True,
    )
    app.dependency_overrides[require_browser_access_principal] = lambda: browser
    app.dependency_overrides[require_browser_upload_principal] = lambda: browser
    app.dependency_overrides[require_browser_review_principal] = lambda: browser

    api = TestClient(app)
    try:
        video = _write_video(tmp_path / "rights-cleared-fixture.mp4")
        source_sha256 = hashlib.sha256(video).hexdigest()
        created = api.post(
            "/api/app/v1/investigations",
            headers={"Idempotency-Key": f"investigation-e2e-create-{fixture_scenario}"},
            json={
                "name": f"Rights-cleared local investigation ({fixture_scenario})",
                "kind": "geolocateProvenance",
                "connectivityPolicy": "local",
                "video": {
                    "fileName": "rights-cleared-fixture.mp4",
                    "contentType": "video/mp4",
                    "sizeBytes": len(video),
                    "durationSeconds": 30,
                },
                "source": {
                    "publisherUrl": (f"https://publisher.example.test/fixtures/{fixture_scenario}"),
                    "publishedAt": "2026-08-30T12:00:00Z",
                    "legalBasis": "licensed",
                    "license": "CC BY 4.0",
                    "redistributionPolicy": "metadataOnly",
                    "retentionDays": 14,
                },
            },
        )
        assert created.status_code == 201, created.text
        creation = created.json()
        investigation_id = creation["investigation"]["investigationId"]
        job_id = creation["investigation"]["jobId"]
        assert creation["investigation"]["status"] == "awaitingUpload"
        assert creation["investigation"]["connectivityPolicy"] == "local"

        _direct_upload(local_http, creation["upload"], video)
        completed_upload = api.post(
            f"/api/app/v1/jobs/{job_id}/uploads/complete",
            headers={"Idempotency-Key": f"investigation-e2e-upload-complete-{fixture_scenario}"},
        )
        assert completed_upload.status_code == 202, completed_upload.text
        assert completed_upload.json()["state"] == "queued"

        # Publication is workflow-isolated: the legacy AD queue stays empty
        # while the local worker has exactly one investigation task to claim.
        assert _queue_depth(sqs, aws_resources["queue_url"]) == (0, 0)
        assert _queue_depth(sqs, investigation_queue_url) == (1, 0)

        monkeypatch.setenv("INSTADESCRIBE_PROVIDER", "local")
        reset_worker_settings()
        reset_worker_caches()
        settings = get_worker_settings()
        assert settings.provider == "local"
        assert settings.investigation_runtime == "fixture"
        assert settings.investigation_test_fixture_scenario == fixture_scenario
        assert settings.work_queue_url != settings.investigation_queue_url
        assert run_once(settings) == "success"
        assert _queue_depth(sqs, investigation_queue_url) == (0, 0)

        detail = api.get(f"/api/app/v1/investigations/{investigation_id}")
        steps = api.get(f"/api/app/v1/investigations/{investigation_id}/steps")
        evidence = api.get(f"/api/app/v1/investigations/{investigation_id}/evidence")
        keyframes = api.get(f"/api/app/v1/investigations/{investigation_id}/keyframes")
        beliefs = api.get(f"/api/app/v1/investigations/{investigation_id}/beliefs")
        initial_report = api.get(f"/api/app/v1/investigations/{investigation_id}/report")
        assert {
            detail.status_code,
            steps.status_code,
            evidence.status_code,
            keyframes.status_code,
            beliefs.status_code,
            initial_report.status_code,
        } == {200}

        detail_body = detail.json()
        expected_trace_id = str(parent_trace_id(uuid.UUID(investigation_id)))
        step_rows = steps.json()["data"]
        evidence_rows = evidence.json()["data"]
        keyframe_rows = keyframes.json()["data"]
        belief_rows = beliefs.json()["data"]
        assert detail_body["status"] == "needsReview"
        assert detail_body["traceId"] == expected_trace_id
        assert detail_body["modelProvenance"] == {
            "executedLocally": True,
            "modelDigest": hashlib.sha256(b"deterministic-fixture-v1").hexdigest(),
            "modelId": "deterministic-fixture",
            "promptDigest": hashlib.sha256(
                (
                    "fixture-observation-v1"
                    if fixture_scenario == "supportive"
                    else "fixture-observation-v1:abstention"
                ).encode()
            ).hexdigest(),
        }
        assert detail_body["runtimeProvenance"] == {
            "platform": f"{platform.system().lower()}-{platform.machine().lower()}",
            "runtime": "fixture",
            "runtimeVersion": "1",
        }
        assert len(step_rows) == 3
        assert [row["sequence"] for row in step_rows] == [1, 2, 3]
        assert len(evidence_rows) == (2 if expected_abstain else 4)
        assert len(keyframe_rows) == 2
        assert [row["frameTimeMs"] for row in keyframe_rows] == [4000, 12500]
        assert len(belief_rows) == 1
        latest_belief = belief_rows[0]
        assert latest_belief["abstained"] is expected_abstain
        assert abs(sum(item["probability"] for item in latest_belief["candidates"]) - 1) < 1e-5
        assert initial_report.json()["decision"] is None
        assert initial_report.json()["latestBelief"] == latest_belief

        top_candidate = latest_belief["candidates"][0]
        evidence_decision = "rejected" if expected_abstain else "accepted"
        finalized = api.post(
            f"/api/app/v1/investigations/{investigation_id}/decision",
            headers={"Idempotency-Key": f"investigation-e2e-finalize-{fixture_scenario}"},
            json={
                "evidenceDecisions": [
                    {"evidenceId": item["evidenceId"], "decision": evidence_decision}
                    for item in evidence_rows
                ],
                "finalHypothesis": (
                    None
                    if expected_abstain
                    else {"id": top_candidate["id"], "label": top_candidate["label"]}
                ),
                "abstain": expected_abstain,
                "abstentionReason": (
                    "The deterministic fixture contains insufficient independent evidence."
                    if expected_abstain
                    else None
                ),
                "notes": "Reviewed only the deterministic, locally generated fixture evidence.",
            },
        )
        assert finalized.status_code == 200, finalized.text
        assert finalized.json()["investigation"]["status"] == "completed"
        assert finalized.json()["investigation"]["calibratedConfidence"] is None

        report = api.get(f"/api/app/v1/investigations/{investigation_id}/report")
        assert report.status_code == 200, report.text
        report_body = report.json()
        assert report_body["investigation"]["status"] == "completed"
        assert report_body["decision"]["status"] == "final"
        assert report_body["decision"]["abstained"] is expected_abstain
        if expected_abstain:
            assert report_body["decision"]["finalHypothesis"] is None
            assert report_body["decision"]["abstentionReason"]
        else:
            assert report_body["decision"]["finalHypothesis"]["id"] == top_candidate["id"]
        assert all(item["verificationState"] == "proposed" for item in report_body["evidence"])
        assert {item["decision"] for item in report_body["decision"]["evidenceDecisions"]} == {
            evidence_decision
        }

        with Session(db_session.get_bind()) as session:
            stored_job = session.get(Job, uuid.UUID(job_id))
            stored_investigation = session.get(Investigation, uuid.UUID(investigation_id))
            source = session.scalar(
                sa.select(SourceRecord).where(
                    SourceRecord.investigation_id == uuid.UUID(investigation_id)
                )
            )
            asset = session.scalar(
                sa.select(Asset).where(
                    Asset.job_id == uuid.UUID(job_id),
                    Asset.asset_type == "source_video",
                )
            )
            event_types = list(
                session.scalars(
                    sa.select(JobEvent.event_type)
                    .where(JobEvent.job_id == uuid.UUID(job_id))
                    .order_by(JobEvent.occurred_at)
                )
            )
            assert stored_job.status == JobState.COMPLETED.value
            assert stored_investigation.status == "completed"
            assert str(stored_investigation.trace_id) == expected_trace_id
            assert source.media_sha256 == source_sha256
            assert (source.legal_basis, source.license_name) == ("licensed", "CC BY 4.0")
            assert asset.status == "validated"
            assert asset.version_id and stored_job.source_version_id == asset.version_id
            assert "job.needs_review" in event_types
    finally:
        api.close()
        local_http.close()
        app.dependency_overrides.pop(require_browser_access_principal, None)
        app.dependency_overrides.pop(require_browser_upload_principal, None)
        app.dependency_overrides.pop(require_browser_review_principal, None)
        get_settings.cache_clear()
        reset_engine_caches()
        reset_s3_caches()
        reset_sqs_caches()
        reset_worker_settings()
        reset_worker_caches()
        for queue_url in (investigation_queue_url, investigation_dlq_url):
            try:
                sqs.delete_queue(QueueUrl=queue_url)
            except Exception:
                pass
