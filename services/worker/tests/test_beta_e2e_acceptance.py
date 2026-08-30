"""One coherent beta acceptance journey across API, workers and LocalStack.

This is intentionally not a production-provider smoke.  It exercises the
real Integration and Browser HTTP contracts, PostgreSQL state transitions,
presigned direct S3 transfers, SQS handoff, analysis/render fencing, outbox
materialization, webhook signing and version-pinned downloads while both
compute stages stay deterministic test fakes.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import sqlalchemy as sa
from app.api.browser.auth import (
    BrowserPrincipal,
    require_browser_review_principal,
    require_browser_scene_principal,
)
from app.core.config import get_settings
from app.core.tenancy import PORTFOLIO_ORGANIZATION_ID, PORTFOLIO_PRINCIPAL_ID
from app.db.session import reset_engine_caches
from app.main import app
from app.models import (
    Asset,
    Deliverable,
    Job,
    JobEvent,
    Render,
    Review,
    WebhookDelivery,
    WebhookEndpoint,
)
from app.services.api_keys import create_service_account, issue_api_key
from app.services.s3 import reset_s3_caches
from app.services.sqs import reset_sqs_caches
from app.services.webhook_dispatcher import dispatch_one, materialize_public_deliveries
from fastapi.testclient import TestClient
from instadescribe_worker.config import get_worker_settings, reset_worker_settings
from instadescribe_worker.consumer import reset_worker_caches, run_once
from instadescribe_worker.render import run_render_once
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.skipif(
    not os.environ.get("INSTADESCRIBE_TEST_S3"),
    reason="INSTADESCRIBE_TEST_S3 not set (explicit LocalStack acceptance gate)",
)

_TRANSCRIPT = b"WEBVTT\n\n00:00:00.050 --> 00:00:00.700\nSpoken words.\n"
_WEBHOOK_SECRET = b"beta-acceptance-webhook-secret-32-bytes"
_WEBHOOK_HOST = "hooks.beta-customer.example"

_FAKE_ANALYSIS = r"""import json
import pathlib
import sys

job_id = sys.argv[1]
settings = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
assert settings["audio_extraction"] is False
assert settings["voice"] == "alloy"
assert settings["provided_transcript_format"] == "vtt"
provided = pathlib.Path(settings["provided_transcript_path"])
assert provided.is_file()
assert "Spoken words." in provided.read_text(encoding="utf-8")

pipeline = pathlib.Path(__file__).resolve().parent
job_dir = pipeline / "jobs" / job_id
output = pipeline.parent / "App" / "public" / "data" / job_id
output.mkdir(parents=True, exist_ok=True)

payloads = {
    "scenes.json": [
        {"scene_id": "scene_1", "start": 0.0, "end": 0.8, "caption": "A person enters."}
    ],
    "entities.json": [{"id": "person_1", "name": "Person"}],
    "audio_events.json": [
        {"start": 0.05, "end": 0.7, "event_type": "dialogue", "transcript": "Spoken words."}
    ],
    "ad_placement_gaps.json": [],
    "transcript.json": [
        {"text": "Spoken words.", "start": 0.05, "end": 0.7, "words": []}
    ],
}
for name, payload in payloads.items():
    (output / name).write_text(json.dumps(payload), encoding="utf-8")
(output / "system_info.json").write_text(json.dumps({
    "video_id": job_id,
    "processing": {"model": "gpt-4.1", "image_detail": "low", "chunk_sizes": [60]},
    "tokens": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
    "status": "completed",
}), encoding="utf-8")
(job_dir / "result.json").write_text(json.dumps({
    "data_path": f"/data/{job_id}",
    "video_file": f"/videos/{job_id}.mp4",
    "scene_count": 1,
    "tokens_used": 0,
}), encoding="utf-8")
(job_dir / "status.json").write_text(json.dumps({
    "status": "ready",
    "progress": 100,
    "stage": "complete",
    "chunks_done": 1,
    "chunks_total": 1,
    "error": None,
}), encoding="utf-8")
"""


def _direct_upload(
    client: httpx.Client,
    contract: dict,
    *,
    filename: str,
    content_type: str,
    body: bytes,
) -> None:
    response = client.post(
        contract["url"],
        data=contract["fields"],
        files={"file": (filename, body, content_type)},
    )
    assert response.status_code in {200, 201, 204}, response.text


def _assert_signed_webhook(
    captured: dict,
    *,
    event_type: str,
    job_id: str,
) -> None:
    signed = captured["signed"]
    payload = json.loads(signed.body)
    assert captured["url"] == f"https://{_WEBHOOK_HOST}/instadescribe"
    assert captured["addresses"] == ("203.0.113.10",)
    assert payload == {
        "id": signed.headers["Webhook-Id"],
        "jobId": job_id,
        "occurredAt": payload["occurredAt"],
        "state": event_type.removeprefix("job."),
        "type": event_type,
    }
    assert signed.headers["Content-Type"] == "application/json"
    assert signed.headers["Webhook-Attempt"] == "1"
    timestamp = signed.headers["Webhook-Timestamp"]
    expected = hmac.new(
        _WEBHOOK_SECRET,
        signed.headers["Webhook-Id"].encode() + b"." + timestamp.encode() + b"." + signed.body,
        hashlib.sha256,
    ).hexdigest()
    assert hmac.compare_digest(signed.headers["Webhook-Signature"], f"v1={expected}")
    # Public events reconcile state only; media identities, URLs, prompts and
    # secret material must be fetched through authenticated APIs instead.
    assert not {
        "deliverables",
        "media",
        "presignedUrl",
        "prompt",
        "secret",
        "versionId",
    } & set(payload)


def _fake_renderer(**kwargs) -> dict[str, Path]:
    assert kwargs["project_name"] == "BIO101 beta acceptance"
    assert kwargs["default_voice"] == "alloy"
    assert kwargs["entities_by_id"] == {"person_1": {"id": "person_1", "name": "Person"}}
    assert kwargs["scenes"] == [
        {
            "scene_id": "scene_1",
            "start": 0.0,
            "end": 0.8,
            "caption": "A person enters.",
            "text": "Reviewed narration.",
            "voice": "nova",
            "speed": 1.25,
            "review_state": "approved",
        }
    ]
    kwargs["on_progress"]("rendering", 50)
    output_dir: Path = kwargs["output_dir"]
    output_dir.mkdir(parents=True)
    names = {
        "mp4": "described_video.mp4",
        "mp3": "audio_description.mp3",
        "srt": "audio_description.srt",
        "csv": "audio_description.csv",
        "docx": "audio_description.docx",
    }
    outputs: dict[str, Path] = {}
    for kind, name in names.items():
        path = output_dir / name
        path.write_bytes(f"beta-acceptance-{kind}\n".encode())
        outputs[kind] = path
    return outputs


def test_api_first_beta_journey_publishes_atomic_bundle_and_signed_webhooks(
    db_session,
    worker_env,
    aws_resources,
    monkeypatch,
    tmp_path,
):
    """Create → upload → analyze → review → render → webhook → download."""

    pipeline = tmp_path / "fake-analysis-pipeline"
    pipeline.mkdir()
    (pipeline / "run_job.py").write_text(_FAKE_ANALYSIS, encoding="utf-8")
    monkeypatch.setenv("INSTADESCRIBE_PIPELINE_SOURCE", str(pipeline))
    monkeypatch.setenv("INSTADESCRIBE_SUBPROCESS_TIMEOUT_SECS", "30")
    monkeypatch.setenv("INSTADESCRIBE_GRACE_SECS", "1")
    monkeypatch.setenv("DATABASE_URL", worker_env.database_url)
    monkeypatch.setenv(
        "INSTADESCRIBE_API_KEY_PEPPER",
        "beta-acceptance-integration-key-pepper",
    )
    monkeypatch.setenv("INSTADESCRIBE_MEDIA_BUCKET", aws_resources["bucket"])
    monkeypatch.setenv("INSTADESCRIBE_WORK_QUEUE_URL", aws_resources["queue_url"])
    monkeypatch.setenv(
        "INSTADESCRIBE_S3_ENDPOINT_PUBLIC",
        worker_env.s3_endpoint_internal or "http://localhost:4566",
    )
    monkeypatch.setenv("INSTADESCRIBE_S3_FORCE_PATH_STYLE", "1")

    get_settings.cache_clear()
    reset_engine_caches()
    reset_s3_caches()
    reset_sqs_caches()
    reset_worker_settings()
    reset_worker_caches()

    with Session(db_session.get_bind()) as session:
        account = create_service_account(
            session,
            PORTFOLIO_ORGANIZATION_ID,
            name="Beta acceptance integration",
        )
        issued = issue_api_key(session, account, label="beta acceptance")
        session.add(
            WebhookEndpoint(
                organization_id=PORTFOLIO_ORGANIZATION_ID,
                endpoint_url=f"https://{_WEBHOOK_HOST}/instadescribe",
                signing_secret_ref="test://beta-acceptance-webhook",
            )
        )
        session.commit()
        api_key = issued.token

    browser = BrowserPrincipal(
        subject="beta-acceptance-reviewer",
        email="reviewer@example.test",
        display_name="Beta Reviewer",
        organization_id=PORTFOLIO_ORGANIZATION_ID,
        organization_slug="portfolio",
        principal_id=PORTFOLIO_PRINCIPAL_ID,
        role="reviewer",
        mfa_verified=False,
    )
    app.dependency_overrides[require_browser_scene_principal] = lambda: browser
    app.dependency_overrides[require_browser_review_principal] = lambda: browser

    api = TestClient(app)
    local_http = httpx.Client(timeout=10, trust_env=False)
    auth = {"Authorization": f"Bearer {api_key}"}
    try:
        clip = tmp_path / "acceptance.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc=duration=1:size=64x64:rate=10",
                "-pix_fmt",
                "yuv420p",
                str(clip),
            ],
            check=True,
            timeout=20,
        )
        video = clip.read_bytes()
        created = api.post(
            "/api/integrations/v1/jobs",
            headers={**auth, "Idempotency-Key": "beta-e2e-create"},
            json={
                "project": {
                    "name": "BIO101 beta acceptance",
                    "externalId": "bio101-beta-acceptance",
                },
                "clientReference": "lms-run-001",
                "video": {
                    "fileName": "lecture.mp4",
                    "contentType": "video/mp4",
                    "sizeBytes": len(video),
                    "durationSeconds": 1,
                },
                "transcript": {
                    "fileName": "lecture.vtt",
                    "format": "vtt",
                    "contentType": "text/vtt",
                    "sizeBytes": len(_TRANSCRIPT),
                },
                "settings": {
                    "preset": "standard",
                    "style": "education",
                    "detail": 3,
                    "language": "en-GB",
                    "instructions": "Describe meaningful visual action.",
                    "voice": "alloy",
                },
            },
        )
        assert created.status_code == 201, created.text
        creation = created.json()
        job_id = creation["job"]["id"]
        assert creation["job"]["state"] == "awaiting_upload"
        assert set(creation["uploads"]) == {"video", "transcript"}

        _direct_upload(
            local_http,
            creation["uploads"]["video"],
            filename="lecture.mp4",
            content_type="video/mp4",
            body=video,
        )
        _direct_upload(
            local_http,
            creation["uploads"]["transcript"],
            filename="lecture.vtt",
            content_type="text/vtt",
            body=_TRANSCRIPT,
        )
        completed_upload = api.post(
            f"/api/integrations/v1/jobs/{job_id}/uploads/complete",
            headers={**auth, "Idempotency-Key": "beta-e2e-upload-complete"},
        )
        assert completed_upload.status_code == 202, completed_upload.text
        assert completed_upload.json()["state"] == "queued"

        # The production consumer uses the real queued message, pinned source
        # and provided VTT.  Only the child analysis implementation is fake.
        assert run_once() == "success"
        needs_review = api.get(f"/api/integrations/v1/jobs/{job_id}", headers=auth)
        assert needs_review.status_code == 200
        assert needs_review.json()["state"] == "needs_review"
        assert needs_review.json()["reviewUrl"].endswith(f"/jobs/{job_id}/review")

        with Session(db_session.get_bind()) as session:
            assets = list(
                session.scalars(
                    sa.select(Asset).where(Asset.job_id == uuid.UUID(job_id)).order_by(Asset.id)
                )
            )
            assert {row.asset_type for row in assets} == {
                "source_video",
                "source_transcript",
            }
            assert all(row.status == "validated" and row.version_id for row in assets)
            assert materialize_public_deliveries(session) == 1

        import app.services.webhook_dispatcher as dispatcher

        monkeypatch.setattr(
            dispatcher,
            "resolve_public_addresses",
            lambda _hostname: ("203.0.113.10",),
        )
        hook_calls: list[dict] = []

        def sender(url, signed, addresses):
            hook_calls.append({"url": url, "signed": signed, "addresses": addresses})
            return 204

        with Session(db_session.get_bind()) as session:
            result = dispatch_one(
                session,
                allowed_hosts=(_WEBHOOK_HOST,),
                secret_resolver=lambda reference: (
                    _WEBHOOK_SECRET
                    if reference == "test://beta-acceptance-webhook"
                    else pytest.fail("unexpected secret reference")
                ),
                sender=sender,
            )
            assert result is not None and result.action == "success"
        _assert_signed_webhook(hook_calls.pop(), event_type="job.needs_review", job_id=job_id)

        scene = api.patch(
            f"/api/app/v1/jobs/{job_id}/scenes/scene_1",
            headers={"Idempotency-Key": "beta-e2e-scene-approve"},
            json={
                "ad": "Reviewed narration.",
                "active": True,
                "locked": False,
                "voice": "nova",
                "speed": 1.25,
                "reviewStatus": "approved",
            },
        )
        assert scene.status_code == 200, scene.text
        assert scene.json()["reviewStatus"] == "approved"
        finished = api.post(
            f"/api/app/v1/jobs/{job_id}/review/finish",
            headers={"Idempotency-Key": "beta-e2e-finish-review"},
            json={"zeroAdConfirmed": False},
        )
        assert finished.status_code == 200, finished.text
        assert (finished.json()["reviewState"], finished.json()["renderState"]) == (
            "completed",
            "queued",
        )
        rendering = api.get(f"/api/integrations/v1/jobs/{job_id}", headers=auth)
        assert rendering.status_code == 200 and rendering.json()["state"] == "rendering"

        maker = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
        render_settings = get_worker_settings().model_copy(
            update={"workspace_root": str(tmp_path), "render_lease_duration_secs": 60}
        )
        assert (
            run_render_once(
                render_settings,
                session_factory=maker,
                s3=aws_resources["s3"],
                renderer=_fake_renderer,
            )
            == "success"
        )

        terminal = api.get(f"/api/integrations/v1/jobs/{job_id}", headers=auth)
        assert terminal.status_code == 200 and terminal.json()["state"] == "completed"
        listed = api.get(
            f"/api/integrations/v1/jobs/{job_id}/deliverables",
            headers=auth,
        )
        assert listed.status_code == 200, listed.text
        deliverables = listed.json()
        assert deliverables["completedSet"] is True
        assert [item["kind"] for item in deliverables["items"]] == [
            "mp4",
            "mp3",
            "srt",
            "csv",
            "docx",
        ]

        # Version IDs deliberately stay server-side, but the redirect must
        # pin the exact published row rather than S3's mutable latest object.
        with Session(db_session.get_bind()) as session:
            published_versions = {
                str(row.id): row.version_id
                for row in session.scalars(
                    sa.select(Deliverable).where(Deliverable.job_id == uuid.UUID(job_id))
                )
            }

        for item in deliverables["items"]:
            redirect = api.get(
                f"/api/integrations/v1/deliverables/{item['id']}/content",
                headers=auth,
                follow_redirects=False,
            )
            assert redirect.status_code == 303
            assert redirect.headers["cache-control"] == "private, no-store"
            location = redirect.headers["location"]
            query = parse_qs(urlsplit(location).query)
            assert query["versionId"] == [published_versions[item["id"]]]
            downloaded = local_http.get(location)
            assert downloaded.status_code == 200
            assert len(downloaded.content) == item["byteSize"]
            assert hashlib.sha256(downloaded.content).hexdigest() == item["sha256"]

        with Session(db_session.get_bind()) as session:
            rows = list(
                session.scalars(
                    sa.select(Deliverable)
                    .where(Deliverable.job_id == uuid.UUID(job_id))
                    .order_by(Deliverable.format)
                )
            )
            assert len(rows) == 5
            assert all(row.state == "published" and row.version_id for row in rows)
            assert len({row.published_at for row in rows}) == 1
            render = session.scalar(sa.select(Render).where(Render.job_id == uuid.UUID(job_id)))
            review = session.scalar(sa.select(Review).where(Review.job_id == uuid.UUID(job_id)))
            assert render is not None and render.integrity_manifest["deliverableCount"] == 5
            assert review is not None and review.approved_scene_count == 1
            assert materialize_public_deliveries(session) == 1

        with Session(db_session.get_bind()) as session:
            result = dispatch_one(
                session,
                allowed_hosts=(_WEBHOOK_HOST,),
                secret_resolver=lambda _reference: _WEBHOOK_SECRET,
                sender=sender,
            )
            assert result is not None and result.action == "success"
        _assert_signed_webhook(hook_calls.pop(), event_type="job.completed", job_id=job_id)

        with Session(db_session.get_bind()) as session:
            public_events = list(
                session.scalars(
                    sa.select(JobEvent)
                    .where(
                        JobEvent.job_id == uuid.UUID(job_id),
                        JobEvent.event_type.in_(("job.needs_review", "job.completed")),
                    )
                    .order_by(JobEvent.occurred_at)
                )
            )
            deliveries = list(
                session.scalars(
                    sa.select(WebhookDelivery).where(
                        WebhookDelivery.event_id.in_([event.id for event in public_events])
                    )
                )
            )
            assert [event.event_type for event in public_events] == [
                "job.needs_review",
                "job.completed",
            ]
            assert all(event.dispatched_at is not None for event in public_events)
            assert len(deliveries) == 2
            assert all(row.state == "succeeded" and row.attempt_count == 1 for row in deliveries)
            internal = session.scalar(
                sa.select(JobEvent).where(
                    JobEvent.job_id == uuid.UUID(job_id),
                    JobEvent.event_type == "render.requested",
                )
            )
            assert internal is not None and internal.dispatched_at is None
            assert session.get(Job, uuid.UUID(job_id)).status == "COMPLETED"
    finally:
        local_http.close()
        app.dependency_overrides.pop(require_browser_scene_principal, None)
        app.dependency_overrides.pop(require_browser_review_principal, None)
        get_settings.cache_clear()
        reset_engine_caches()
        reset_s3_caches()
        reset_sqs_caches()
        reset_worker_settings()
        reset_worker_caches()
