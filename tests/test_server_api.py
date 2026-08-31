"""Golden-parity harness for the Flask API.

These tests pin the behaviour of every endpoint against a temp data dir, so the
storage/export_service extraction can be verified to preserve it. The pipeline
subprocess and any OpenAI/ffmpeg paths are never exercised — only deterministic
logic is.
"""

import io
import json
import logging
import subprocess

import export_service
import normalisation
import providers
import pytest
import server
import storage
import tts_render

STUDY_SESSION_ID = "s-123e4567-e89b-42d3-a456-426614174000"


@pytest.fixture
def tmp_env(tmp_path, monkeypatch):
    """Redirect all on-disk storage to a temp tree and stub the pipeline launch."""
    app_dir = tmp_path / "App"
    data_dir = app_dir / "public" / "data"
    videos_dir = app_dir / "public" / "videos"
    jobs_dir = tmp_path / "jobs"
    for d in (data_dir, videos_dir, jobs_dir, app_dir / "dist"):
        d.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(storage, "APP_DIR", app_dir)
    monkeypatch.setattr(storage, "DATA_DIR", data_dir)
    monkeypatch.setattr(storage, "VIDEOS_DIR", videos_dir)
    monkeypatch.setattr(storage, "JOBS_DIR", jobs_dir)
    monkeypatch.setattr(storage, "DIST_DIR", app_dir / "dist")
    monkeypatch.setattr(server, "STUDY_LOGS_DIR", tmp_path / "study_logs")
    # Never launch the real pipeline.
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: None)
    return tmp_path


@pytest.fixture
def client(tmp_env):
    server.app.config.update(TESTING=True)
    return server.app.test_client()


def _seed_job(job_id, status=None, settings=None, meta=None):
    jdir = storage.JOBS_DIR / job_id
    jdir.mkdir(parents=True, exist_ok=True)
    (jdir / "status.json").write_text(json.dumps(status or {"status": "ready", "progress": 100}))
    if settings is not None:
        (jdir / "settings.json").write_text(json.dumps(settings))
    if meta is not None:
        (jdir / "meta.json").write_text(json.dumps(meta))
    return jdir


def _seed_data(job_id, scenes, entities=None):
    ddir = storage.DATA_DIR / job_id
    ddir.mkdir(parents=True, exist_ok=True)
    (ddir / "scenes.json").write_text(json.dumps(scenes))
    if entities is not None:
        (ddir / "entities.json").write_text(json.dumps(entities))
    return ddir


def _assert_sanitized_internal_error(
    response, expected_error, internal_detail, caplog, operation, job_id
):
    assert response.status_code == 500
    assert response.get_json() == {"error": expected_error}
    assert internal_detail not in response.get_data(as_text=True)
    records = [
        record for record in caplog.records if getattr(record, "operation", None) == operation
    ]
    assert len(records) == 1
    assert records[0].job_id == job_id
    assert records[0].exc_info is not None


# ── create_job ────────────────────────────────────────────────────────────────


def test_create_job_requires_video(client):
    assert client.post("/api/jobs").status_code == 400


def test_create_job_rejects_bad_settings(client):
    r = client.post(
        "/api/jobs",
        data={"video": (io.BytesIO(b"x"), "v.mp4"), "settings": "{not json"},
        content_type="multipart/form-data",
    )
    assert r.status_code == 400


def test_create_job_happy_path(client):
    r = client.post(
        "/api/jobs",
        data={
            "video": (io.BytesIO(b"fake"), "v.mp4"),
            "settings": json.dumps(
                {"name": "Test", "settings": {"model": "gpt-4.1"}, "durationSecs": 12}
            ),
        },
        content_type="multipart/form-data",
    )
    assert r.status_code == 202
    body = r.get_json()
    assert body["jobId"] == body["projectId"]
    jid = body["jobId"]
    status = json.loads((storage.JOBS_DIR / jid / "status.json").read_text())
    assert status["status"] == "queued"
    settings = json.loads((storage.JOBS_DIR / jid / "settings.json").read_text())
    assert settings["model"] == "gpt-4.1"
    assert settings["project_name"] == "Test"


# ── get / list / delete / patch meta ────────────────────────────────────────────


def test_get_job_not_found(client):
    assert client.get("/api/jobs/missing").get_json()["status"] == "not_found"


def test_get_and_batch_reject_stored_video_path_traversal(client, tmp_env):
    job_id = "job-stored-traversal"
    job = _seed_job(job_id, status={"status": "ready", "progress": 100})
    (job / "result.json").write_text(
        json.dumps({"video_file": "/videos/../../outside.mp4", "data_path": f"/data/{job_id}"})
    )
    outside = tmp_env / "outside.mp4"
    outside.write_bytes(b"not public")

    single = client.get(f"/api/jobs/{job_id}").get_json()
    batch = client.get("/api/jobs", query_string={"ids": job_id}).get_json()[job_id]

    assert single["video_file"] is None
    assert batch["video_file"] is None
    assert "outside" not in json.dumps(single)
    assert "outside" not in json.dumps(batch)


def test_failed_job_status_never_reemits_persisted_internal_details(client):
    internal_detail = "Traceback: private worker path and provider diagnostic"
    _seed_job(
        "job-failed",
        status={"status": "failed", "progress": 0, "stage": "failed", "error": internal_detail},
    )

    single = client.get("/api/jobs/job-failed")
    batch = client.get("/api/jobs", query_string={"ids": "job-failed"})

    assert single.get_json()["error"] == "job processing failed"
    assert batch.get_json()["job-failed"]["error"] == "job processing failed"
    assert internal_detail not in single.get_data(as_text=True)
    assert internal_detail not in batch.get_data(as_text=True)


def test_list_and_batch(client):
    _seed_job("job-a", status={"status": "ready", "progress": 100}, settings={"project_name": "A"})
    _seed_job("job-b", status={"status": "queued", "progress": 0})
    allj = client.get("/api/jobs").get_json()
    assert set(allj) == {"job-a", "job-b"}
    one = client.get("/api/jobs?ids=job-a").get_json()
    assert set(one) == {"job-a"}
    assert one["job-a"]["project_name"] == "A"


@pytest.mark.parametrize(
    "query",
    [
        "ids=..%2Foutside",
        "ids=%2Fetc%2Fpasswd",
        "ids=job-a%2Foutside",
        "ids=job-a%5Coutside",
        "ids=%252e%252e",
    ],
)
def test_batch_jobs_rejects_traversal_and_encoded_separators(client, query):
    response = client.get(f"/api/jobs?{query}")
    assert response.status_code == 400
    assert response.get_json() == {"error": "invalid id"}


@pytest.mark.parametrize(
    "path",
    [
        "/api/jobs/%2e%2e",
        "/api/jobs/..%2Foutside",
        "/api/jobs/%2Fetc%2Fpasswd",
        "/api/jobs/job%5Coutside",
    ],
)
def test_get_job_rejects_traversal_and_encoded_separators(client, path):
    assert 400 <= client.get(path).status_code < 500


def test_job_routes_enforce_id_length_and_batch_count_bounds(client):
    oversized = "a" * (storage.MAX_STORAGE_ID_LENGTH + 1)
    assert client.get(f"/api/jobs/{oversized}").status_code == 400
    assert client.get("/api/jobs", query_string={"ids": oversized}).status_code == 400

    too_many = ",".join(f"job-{index}" for index in range(server.MAX_BATCH_JOB_IDS + 1))
    response = client.get("/api/jobs", query_string={"ids": too_many})
    assert response.status_code == 400
    assert response.get_json() == {"error": "too many ids"}


def test_list_and_get_fail_closed_for_symlinked_job(client, tmp_env):
    outside = tmp_env / "outside-job"
    outside.mkdir()
    (outside / "status.json").write_text(json.dumps({"status": "ready", "progress": 100}))
    (storage.JOBS_DIR / "alias").symlink_to(outside, target_is_directory=True)

    assert client.get("/api/jobs/alias").status_code == 400
    assert client.get("/api/jobs").get_json() == {}


def test_delete_job(client):
    assert client.delete("/api/jobs/bad id").status_code == 400  # space → invalid
    assert client.delete("/api/jobs/ghost").status_code == 204  # idempotent
    _seed_job("job-d")
    assert client.delete("/api/jobs/job-d").status_code == 204
    assert not (storage.JOBS_DIR / "job-d").exists()


def test_patch_job_meta(client):
    assert client.patch("/api/jobs/bad id").status_code == 400
    assert client.patch("/api/jobs/ghost", json={"name": "x"}).status_code == 404
    _seed_job("job-m")
    r = client.patch("/api/jobs/job-m", json={"name": "  Renamed  ", "starred": True})
    meta = r.get_json()
    assert meta["name"] == "Renamed" and meta["starred"] is True


# ── sanitized internal errors ─────────────────────────────────────────────────


def test_smart_fill_failure_hides_exception_details(client, monkeypatch, caplog):
    internal_detail = "provider diagnostic detail"
    _seed_job("job-smart")

    class BrokenTextProvider:
        def rewrite(self, **_kwargs):
            raise RuntimeError(internal_detail)

    monkeypatch.setattr(providers, "get_text_provider", lambda: BrokenTextProvider())
    with caplog.at_level(logging.ERROR, logger=server.__name__):
        response = client.post(
            "/api/jobs/job-smart/smart-fill",
            json={"text": "A person enters.", "target_secs": 2},
        )

    _assert_sanitized_internal_error(
        response, "smart-fill failed", internal_detail, caplog, "smart_fill", "job-smart"
    )


def test_tts_render_failure_hides_exception_details(client, monkeypatch, caplog):
    internal_detail = "tts upstream diagnostic detail"
    _seed_job("job-tts")

    def fail_render(*_args):
        raise RuntimeError(internal_detail)

    monkeypatch.setattr(tts_render, "render_line", fail_render)
    with caplog.at_level(logging.ERROR, logger=server.__name__):
        response = client.post(
            "/api/jobs/job-tts/tts-preview",
            json={"text": "A person enters.", "voice": "onyx"},
        )

    _assert_sanitized_internal_error(
        response, "tts render failed", internal_detail, caplog, "tts_render", "job-tts"
    )


def test_tts_speed_failure_hides_exception_details(client, monkeypatch, caplog):
    internal_detail = "ffmpeg diagnostic detail"
    _seed_job("job-speed")

    def render_fixture(_text, _voice, destination):
        destination.write_bytes(b"fixture mp3")

    def fail_adjust(*_args):
        raise RuntimeError(internal_detail)

    monkeypatch.setattr(tts_render, "render_line", render_fixture)
    monkeypatch.setattr(tts_render, "adjust_speed", fail_adjust)
    with caplog.at_level(logging.ERROR, logger=server.__name__):
        response = client.post(
            "/api/jobs/job-speed/tts-preview",
            json={"text": "A person enters.", "voice": "onyx", "speed": 1.25},
        )

    _assert_sanitized_internal_error(
        response,
        "speed adjust failed",
        internal_detail,
        caplog,
        "tts_speed_adjust",
        "job-speed",
    )


# ── scene overrides (the lock-protected read-modify-write) ──────────────────────


def test_patch_scene_and_get_overrides(client):
    _seed_job("job-s")
    r = client.patch(
        "/api/jobs/job-s/scenes/scene_1",
        json={"ad": "new line", "active": False, "locked": True, "voice": "nova"},
    )
    ov = r.get_json()["override"]
    assert ov == {"ad": "new line", "active": False, "locked": True, "voice": "nova"}
    # An invalid voice is silently ignored, not stored.
    client.patch("/api/jobs/job-s/scenes/scene_1", json={"voice": "bogus"})
    stored = client.get("/api/jobs/job-s/overrides").get_json()
    assert stored["scene_1"]["voice"] == "nova"


# ── entity rename ───────────────────────────────────────────────────────────────


def test_patch_entity_rename_rerenders_scenes(client):
    _seed_job("job-e")
    _seed_data(
        "job-e",
        scenes=[
            {
                "scene_id": "scene_1",
                "caption_template": "{char_1_first} runs.",
                "caption": "old",
                "locked": False,
            }
        ],
        entities=[
            {"id": "char_1", "name": "a man", "first_mention_label": "a man", "pronoun": "he"}
        ],
    )
    r = client.patch("/api/jobs/job-e/entities/char_1", json={"name": "Indiana"})
    assert r.status_code == 200
    scenes = json.loads((storage.DATA_DIR / "job-e" / "scenes.json").read_text())
    assert scenes[0]["caption"] == "Indiana runs."

    assert client.patch("/api/jobs/job-e/entities/char_9", json={"name": "X"}).status_code == 404
    assert client.patch("/api/jobs/job-x/entities/char_1", json={"name": "X"}).status_code == 404


def test_entity_rename_failure_hides_exception_details(client, monkeypatch, caplog):
    internal_detail = "entity database diagnostic detail"
    _seed_job("job-rename")
    _seed_data(
        "job-rename",
        scenes=[
            {
                "scene_id": "scene_1",
                "caption_template": "{char_1_first} runs.",
                "caption": "old",
                "locked": False,
            }
        ],
        entities=[
            {"id": "char_1", "name": "a man", "first_mention_label": "a man", "pronoun": "he"}
        ],
    )

    def fail_rename(*_args):
        raise RuntimeError(internal_detail)

    monkeypatch.setattr(normalisation, "apply_manual_character_rename", fail_rename)
    with caplog.at_level(logging.ERROR, logger=server.__name__):
        response = client.patch(
            "/api/jobs/job-rename/entities/char_1",
            json={"name": "Indiana"},
        )

    _assert_sanitized_internal_error(
        response, "rename failed", internal_detail, caplog, "entity_rename", "job-rename"
    )


# ── merged scenes (override precedence + zero-duration drop) ─────────────────────


def test_merged_scenes_applies_overrides_and_drops_zero_duration(tmp_env):
    _seed_job("job-merge")
    _seed_data(
        "job-merge",
        scenes=[
            {"scene_id": "scene_1", "start": 0.0, "end": 5.0, "caption": "first"},
            {"scene_id": "scene_2", "start": 5.0, "end": 5.0, "caption": "zero-length"},
        ],
    )
    storage.write_overrides(
        "job-merge", {"scene_1": {"ad": "edited", "active": False, "voice": "nova"}}
    )
    merged = export_service.merged_scenes("job-merge")
    assert len(merged) == 1  # zero-duration scene dropped
    assert merged[0]["text"] == "edited"  # override "ad" wins over caption
    assert merged[0]["active"] is False  # override active wins
    assert merged[0]["voice"] == "nova"


# ── export validation + start ───────────────────────────────────────────────────


def test_export_validation(client, monkeypatch):
    # Stub the background render thread so start_export's contract is tested in
    # isolation (no ffmpeg/TTS, no teardown race on the temp dir).
    class _NoThread:
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

    monkeypatch.setattr(server.threading, "Thread", _NoThread)

    assert client.post("/api/jobs/bad id/export").status_code == 400
    assert client.post("/api/jobs/ghost/export").status_code == 404
    _seed_job("job-ex")  # no scenes.json yet
    assert client.post("/api/jobs/job-ex/export", json={"format": "srt"}).status_code == 409
    _seed_data("job-ex", scenes=[{"scene_id": "scene_1", "start": 0, "end": 2, "caption": "x"}])
    assert client.post("/api/jobs/job-ex/export", json={"format": "weird"}).status_code == 400
    r = client.post("/api/jobs/job-ex/export", json={"format": "srt"})
    assert r.status_code == 202 and "exportId" in r.get_json()


def test_export_status_and_download_not_ready(client):
    _seed_job("job-ex2")
    assert client.get("/api/jobs/job-ex2/export/nope").status_code == 404
    assert client.get("/api/jobs/job-ex2/export/nope/download").status_code == 404


def test_export_failure_hides_exception_details(client, monkeypatch, caplog):
    internal_detail = "export provider diagnostic detail"
    job_id = "job-export-failure"
    export_id = "export-1"
    _seed_job(job_id)
    export_dir = storage.export_dir(job_id, export_id)
    export_dir.mkdir(parents=True)

    def fail_merge(_job_id):
        raise RuntimeError(internal_detail)

    monkeypatch.setattr(export_service, "merged_scenes", fail_merge)
    with caplog.at_level(logging.ERROR, logger=export_service.__name__):
        export_service.run_export(job_id, export_id, "srt", "onyx")

    response = client.get(f"/api/jobs/{job_id}/export/{export_id}")
    body = response.get_json()
    assert response.status_code == 200
    assert body["status"] == "failed"
    assert body["error"] == "export failed"
    assert internal_detail not in response.get_data(as_text=True)
    records = [
        record for record in caplog.records if getattr(record, "operation", None) == "export"
    ]
    assert len(records) == 1
    assert records[0].job_id == job_id
    assert records[0].export_id == export_id
    assert records[0].exc_info is not None


def test_export_status_never_reemits_persisted_internal_details(client):
    internal_detail = "Traceback: private export path and provider diagnostic"
    job_id = "job-old-export-failure"
    export_id = "export-old"
    _seed_job(job_id)
    export_dir = storage.export_dir(job_id, export_id)
    export_dir.mkdir(parents=True)
    (export_dir / "status.json").write_text(
        json.dumps(
            {
                "status": "failed",
                "progress": 0,
                "stage": "error",
                "format": "srt",
                "error": internal_detail,
            }
        )
    )

    response = client.get(f"/api/jobs/{job_id}/export/{export_id}")

    assert response.status_code == 200
    assert response.get_json()["error"] == "export failed"
    assert internal_detail not in response.get_data(as_text=True)


# ── study mode ──────────────────────────────────────────────────────────────────


def test_study_session_provision_is_idempotent(client, monkeypatch):
    monkeypatch.setattr(server, "STUDY_SOURCE_JOB", "studysrc")
    _seed_data("studysrc", scenes=[{"scene_id": "scene_1", "start": 0, "end": 3, "caption": "c"}])
    r1 = client.post("/api/study/session", json={"sessionId": STUDY_SESSION_ID})
    assert r1.status_code == 200
    body = r1.get_json()
    assert body["projectId"] == STUDY_SESSION_ID
    assert body["dataPath"] == f"/data/{STUDY_SESSION_ID}"
    assert storage.scenes_file(STUDY_SESSION_ID).exists()
    # Scenes are seeded inactive for the study.
    ov = json.loads(storage.overrides_path(STUDY_SESSION_ID).read_text())
    assert ov["scene_1"] == {"active": False}
    # Returning session reuses its copy without error.
    assert (
        client.post("/api/study/session", json={"sessionId": STUDY_SESSION_ID}).status_code == 200
    )


def test_study_session_requires_valid_id(client):
    assert client.post("/api/study/session", json={}).status_code == 400
    for session_id in (
        "../outside",
        "/absolute",
        r"..\outside",
        "a" * 129,
        "ordinary-job-id",
        "123e4567-e89b-42d3-a456-426614174000",
        "s-123e4567-e89b-12d3-a456-426614174000",
        123,
        True,
        [STUDY_SESSION_ID],
        {"id": STUDY_SESSION_ID},
    ):
        assert client.post("/api/study/session", json={"sessionId": session_id}).status_code == 400


def test_study_session_cannot_overwrite_normal_job(client):
    job = _seed_job("ordinary-job-id", status={"status": "queued", "progress": 12})
    result = job / "result.json"
    result.write_text(json.dumps({"sentinel": "normal job"}))
    before_status = (job / "status.json").read_bytes()
    before_result = result.read_bytes()

    response = client.post("/api/study/session", json={"sessionId": "ordinary-job-id"})

    assert response.status_code == 400
    assert (job / "status.json").read_bytes() == before_status
    assert result.read_bytes() == before_result


def test_study_log_appends(client):
    for invalid_body in (
        {},
        {"sessionId": 123},
        {"sessionId": True},
        {"sessionId": [STUDY_SESSION_ID]},
        {"sessionId": {"id": STUDY_SESSION_ID}},
    ):
        assert client.post("/api/log", json=invalid_body).status_code == 400
    assert (
        client.post("/api/log", json={"sessionId": "ordinary-job-id", "event": "edit"}).status_code
        == 400
    )
    assert (
        client.post(
            "/api/log", json={"sessionId": STUDY_SESSION_ID, "event": "edit", "ts": 1}
        ).status_code
        == 204
    )
    line = json.loads(storage.study_log_file(server.STUDY_LOGS_DIR, STUDY_SESSION_ID).read_text())
    assert line["event"] == "edit"


def test_study_log_rejects_symlink_destination(client, tmp_env):
    outside = tmp_env / "outside.jsonl"
    outside.write_text("unchanged")
    server.STUDY_LOGS_DIR.mkdir()
    (server.STUDY_LOGS_DIR / f"{STUDY_SESSION_ID}.jsonl").symlink_to(outside)

    response = client.post("/api/log", json={"sessionId": STUDY_SESSION_ID, "event": "edit"})
    assert response.status_code == 400
    assert outside.read_text() == "unchanged"


def test_study_config_defaults(client):
    cfg = client.get("/api/study/config").get_json()
    assert cfg["questionnaireParam"] == "session"


# ── static serving ──────────────────────────────────────────────────────────────


def test_serve_data_file(client):
    (storage.DATA_DIR / "demo").mkdir(parents=True, exist_ok=True)
    (storage.DATA_DIR / "demo" / "scenes.json").write_text('{"ok": true}')
    r = client.get("/data/demo/scenes.json")
    assert r.status_code == 200 and r.get_json() == {"ok": True}


def test_missing_data_and_video_files_remain_404(client):
    assert client.get("/data/missing.json").status_code == 404
    assert client.get("/videos/missing.mp4").status_code == 404


def test_public_routes_reject_oversized_path_components_without_500(client):
    oversized = "x" * (storage.MAX_PATH_COMPONENT_BYTES + 1)

    assert client.get(f"/data/{oversized}").status_code == 404
    assert client.get(f"/videos/{oversized}").status_code == 404
    assert client.get(f"/{oversized}").status_code == 404


def test_spa_fallback_does_not_disclose_outside_file_existence(client, tmp_env):
    index = storage.DIST_DIR / "index.html"
    index.write_text("<html>fallback</html>")
    outside_existing = storage.DIST_DIR.parent / "outside-existing.txt"
    outside_existing.write_text("private")

    with server.app.test_request_context("/"):
        existing_response, existing_status = server.serve_spa("../outside-existing.txt")
        missing_response, missing_status = server.serve_spa("../outside-missing.txt")

    assert existing_status == missing_status == 404
    assert existing_response.get_json() == missing_response.get_json() == {"error": "not found"}
    encoded_existing = client.get("/..%2Foutside-existing.txt")
    encoded_missing = client.get("/..%2Foutside-missing.txt")
    assert encoded_existing.status_code == encoded_missing.status_code == 404
    assert client.get("/client-side/route").status_code == 200


def test_serve_index_without_build(client):
    r = client.get("/")
    assert r.status_code == 200 and "backend running" in r.get_json()["status"]


def test_api_unknown_route_is_404(client):
    assert client.get("/api/does-not-exist").status_code == 404


def test_legacy_server_binds_loopback_only(tmp_env, monkeypatch):
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(server.app, "run", fake_run)
    monkeypatch.setenv("PORT", "9876")

    server.run_local_server()

    assert captured == {
        "host": "127.0.0.1",
        "port": 9876,
        "threaded": True,
        "debug": False,
    }


# ── evaluation endpoint ─────────────────────────────────────────────────────────


def test_evaluation_endpoint(client):
    assert client.get("/api/jobs/bad id/evaluation").status_code == 400
    assert client.get("/api/jobs/ghost/evaluation").status_code == 404
    _seed_job("job-ev")
    ddir = _seed_data(
        "job-ev",
        scenes=[
            {
                "scene_id": "scene_1",
                "start": 0,
                "end": 5,
                "caption": "a man waves",
                "character_ids": [],
            }
        ],
    )
    (ddir / "audio_events.json").write_text(json.dumps([]))
    (ddir / "entities.json").write_text(json.dumps([]))
    body = client.get("/api/jobs/job-ev/evaluation").get_json()
    assert body["active_count"] == 1
    assert 0.0 <= body["overall"] <= 1.0
    assert set(body["dimensions"]) == {
        "timing",
        "dialogue_safety",
        "coverage",
        "character_consistency",
        "grounding",
    }
