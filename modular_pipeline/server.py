#!/usr/bin/env python3
"""InstaDescribe legacy backend server.

A single-origin Flask app: it serves the built SPA, per-job data, videos, and the
JSON API the editor (uploadApi.ts) consumes. Path/storage logic lives in
``storage``; the export worker lives in ``export_service``.

Run with:  cd modular_pipeline && python3 server.py
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import uuid

import export_service
import storage
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from logging_config import configure_logging
from providers import (
    VALID_BACKENDS,
    active_backend,
    provider_status,
    set_active_backend,
)

logger = logging.getLogger(__name__)

MAX_BATCH_JOB_IDS = 100
MAX_BATCH_IDS_QUERY_LENGTH = MAX_BATCH_JOB_IDS * (storage.MAX_STORAGE_ID_LENGTH + 1)
STUDY_SESSION_PREFIX = "s-"
LEGACY_BIND_HOST = "127.0.0.1"


def _is_valid_study_session_id(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith(STUDY_SESSION_PREFIX):
        return False
    uuid_text = value.removeprefix(STUDY_SESSION_PREFIX)
    try:
        parsed = uuid.UUID(uuid_text)
    except (AttributeError, ValueError):
        return False
    return parsed.version == 4 and parsed.variant == uuid.RFC_4122 and str(parsed) == uuid_text


app = Flask(__name__)
# CORS origins are configurable for deployment. Set STUDY_CORS_ORIGINS to the
# deployed frontend URL (comma-separated), or "*" to allow any origin.
_cors_env = os.environ.get(
    "STUDY_CORS_ORIGINS",
    "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173",
).strip()
CORS(
    app, origins="*" if _cors_env == "*" else [o.strip() for o in _cors_env.split(",") if o.strip()]
)
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024 * 1024  # 4 GB


@app.errorhandler(storage.InvalidStoragePath)
def invalid_storage_path(_error):
    """Keep storage-boundary rejection fail-closed at every legacy route."""
    return jsonify({"error": "invalid id"}), 400


# ─── POST /api/jobs ───────────────────────────────────────────────────────────


@app.post("/api/jobs")
def create_job():
    if "video" not in request.files:
        return jsonify({"error": "No video file provided"}), 400

    video_file = request.files["video"]
    raw_settings = request.form.get("settings", "{}")
    try:
        frontend_settings = json.loads(raw_settings)
    except json.JSONDecodeError:
        return jsonify({"error": "Invalid settings JSON"}), 400

    # Use the same ID for job and project so the frontend can navigate /editor/{jobId}
    job_id = str(uuid.uuid4())
    jdir = storage.job_dir(job_id)
    jdir.mkdir(parents=True, exist_ok=True)

    video_path = storage.job_video_file(job_id)
    video_file.save(str(video_path))

    cfg = frontend_settings.get("settings", {})
    settings = {
        "job_id": job_id,
        "video_path": str(video_path),
        "model": cfg.get("model", "gpt-4.1"),
        "frame_quality": cfg.get("frameQuality", "low"),
        "fps": cfg.get("fps", 1.0),
        "chunk_size": cfg.get("chunkSizeSecs", 60),
        "audio_extraction": cfg.get("audioExtraction", True),
        "custom_prompt": cfg.get("customPrompt", frontend_settings.get("customPrompt", "")),
        "language": cfg.get("language"),
        "detail_level": cfg.get("detailLevel", 3),
        "preset_style": cfg.get("presetStyle", "documentary"),
        "project_name": frontend_settings.get("name", "Untitled Project"),
        "duration_secs": frontend_settings.get("durationSecs", 0),
    }

    settings_path = storage.settings_file(job_id)
    settings_path.write_text(json.dumps(settings, indent=2))

    storage.status_file(job_id).write_text(
        json.dumps(
            {
                "status": "queued",
                "progress": 0,
                "stage": "queued",
                "chunks_done": 0,
                "chunks_total": 0,
                "error": None,
            }
        )
    )

    # Launch run_job.py as a non-blocking subprocess, pinned to the active model
    # backend (the in-app picker / INSTADESCRIBE_BACKEND).
    job_env = dict(os.environ)
    job_env["INSTADESCRIBE_BACKEND"] = active_backend()
    subprocess.Popen(
        [storage.PYTHON, str(storage.SERVER_DIR / "run_job.py"), job_id, str(settings_path)],
        cwd=str(storage.SERVER_DIR),
        env=job_env,
        stdout=subprocess.DEVNULL,
        stderr=open(str(storage.stderr_file(job_id)), "w"),
    )

    return jsonify({"jobId": job_id, "projectId": job_id}), 202


# ─── GET /api/jobs/<job_id> ───────────────────────────────────────────────────


@app.get("/api/jobs/<job_id>")
def get_job(job_id: str):
    if not storage.is_valid_storage_id(job_id):
        return jsonify({"error": "invalid id"}), 400
    data = storage.read_status(job_id)
    status = data.get("status", "unknown")
    response = {
        "status": status,
        "progress": data.get("progress", 0),
        "stage": data.get("stage", ""),
        "chunks_done": data.get("chunks_done", 0),
        "chunks_total": data.get("chunks_total", 0),
        "error": "job processing failed" if status == "failed" else None,
    }

    if data.get("status") == "ready":
        result_path = storage.result_file(job_id)
        if result_path.exists():
            try:
                result = json.loads(result_path.read_text())
                response.update(
                    {
                        "data_path": result.get("data_path", f"/data/{job_id}"),
                        "video_file": storage.video_url_for(job_id),
                        "poster_file": result.get("poster_file"),
                        "poster_avif_file": result.get("poster_avif_file"),
                        "poster_placeholder": result.get("poster_placeholder"),
                        "scene_count": result.get("scene_count"),
                        "tokens_used": result.get("tokens_used"),
                    }
                )
            except Exception:
                response["data_path"] = f"/data/{job_id}"
                response["video_file"] = storage.video_url_for(job_id)

    return jsonify(response)


# ─── GET /api/jobs (list all, or batch status for ?ids=…) ─────────────────────


def _job_summary(jid: str) -> dict:
    """Status summary for one job ID, merging settings + status + result + meta."""
    storage.validate_storage_id(jid, field="job id")
    data = storage.read_status(jid)
    entry: dict = {"status": data.get("status", "unknown"), "progress": data.get("progress", 0)}

    settings_path = storage.settings_file(jid)
    if settings_path.exists():
        try:
            s = json.loads(settings_path.read_text())
            entry["project_name"] = s.get("project_name")
            entry["duration_secs"] = s.get("duration_secs")
            entry["model"] = s.get("model")
            entry["chunk_size"] = s.get("chunk_size")
        except Exception:
            pass

    if data.get("status") == "ready":
        result_path = storage.result_file(jid)
        if result_path.exists():
            try:
                r = json.loads(result_path.read_text())
                entry.update(
                    {
                        "data_path": r.get("data_path", f"/data/{jid}"),
                        "video_file": storage.video_url_for(jid),
                        "poster_file": r.get("poster_file"),
                        "poster_avif_file": r.get("poster_avif_file"),
                        "poster_placeholder": r.get("poster_placeholder"),
                        "scene_count": r.get("scene_count"),
                        "tokens_used": r.get("tokens_used"),
                    }
                )
            except Exception:
                entry["data_path"] = f"/data/{jid}"
                entry["video_file"] = storage.video_url_for(jid)

    if data.get("status") == "failed":
        entry["error"] = "job processing failed"

    meta = storage.read_meta(jid)
    if "name" in meta:
        entry["project_name"] = meta["name"]
    if "starred" in meta:
        entry["starred"] = bool(meta["starred"])

    return entry


@app.get("/api/jobs")
def list_or_batch_jobs():
    """?ids=id1,id2 → status per requested ID (batch sync). No args → all jobs."""
    ids_arg = request.args.get("ids")
    if ids_arg is not None:
        if len(ids_arg) > MAX_BATCH_IDS_QUERY_LENGTH:
            return jsonify({"error": "too many or oversized ids"}), 400
        job_ids = [jid.strip() for jid in ids_arg.split(",") if jid.strip()]
        if len(job_ids) > MAX_BATCH_JOB_IDS:
            return jsonify({"error": "too many ids"}), 400
        if any(not storage.is_valid_storage_id(jid) for jid in job_ids):
            return jsonify({"error": "invalid id"}), 400
        result = {}
        for jid in job_ids:
            result[jid] = _job_summary(jid)
        return jsonify(result)

    if not storage.JOBS_DIR.exists():
        return jsonify({})
    result = {}
    for jdir in sorted(storage.JOBS_DIR.iterdir()):
        if not storage.is_valid_storage_id(jdir.name):
            continue
        try:
            safe_job_dir = storage.job_dir(jdir.name)
        except storage.InvalidStoragePath:
            continue
        if safe_job_dir.is_dir():
            result[jdir.name] = _job_summary(jdir.name)
    return jsonify(result)


# ─── DELETE /api/jobs/<job_id> ────────────────────────────────────────────────


@app.delete("/api/jobs/<job_id>")
def delete_job(job_id: str):
    if not storage.is_valid_storage_id(job_id):
        return jsonify({"error": "invalid id"}), 400
    jdir = storage.job_dir(job_id)
    if not jdir.exists():
        return ("", 204)  # idempotent
    shutil.rmtree(jdir)
    return ("", 204)


# ─── PATCH /api/jobs/<job_id> ─────────────────────────────────────────────────


@app.patch("/api/jobs/<job_id>")
def patch_job(job_id: str):
    if not storage.is_valid_storage_id(job_id):
        return jsonify({"error": "invalid id"}), 400
    if not storage.job_dir(job_id).exists():
        return jsonify({"error": "not found"}), 404

    body = request.get_json(silent=True) or {}
    meta = storage.read_meta(job_id)
    if "name" in body and isinstance(body["name"], str):
        meta["name"] = body["name"].strip()[:200]
    if "starred" in body and isinstance(body["starred"], bool):
        meta["starred"] = body["starred"]
    storage.write_meta(job_id, meta)
    return jsonify(meta)


# ─── POST /api/jobs/<id>/smart-fill ───────────────────────────────────────────

# AD speech is typically ~150 wpm ≈ 2.5 wps; 2.3 leaves a little headroom in the gap.
SMARTFILL_WPS = 2.3


@app.post("/api/jobs/<job_id>/smart-fill")
def smart_fill(job_id: str):
    """Rewrite an AD line to fit inside the available silence gap (single OpenAI call)."""
    if not storage.is_valid_storage_id(job_id):
        return jsonify({"error": "invalid id"}), 400
    if not storage.job_dir(job_id).exists():
        return jsonify({"error": "not found"}), 404

    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    try:
        target_secs = float(body.get("target_secs") or 0)
    except (TypeError, ValueError):
        target_secs = 0.0
    if not text:
        return jsonify({"error": "text required"}), 400
    if target_secs <= 0.0:
        return jsonify({"error": "target_secs required and > 0"}), 400

    target_secs = min(target_secs, 120.0)
    target_words = max(3, int(round(target_secs * SMARTFILL_WPS)))

    system_msg = (
        "You are an audio description editor for blind and low-vision viewers. "
        "Rewrite the description to fit a strict time budget without losing the "
        "essential visual information. Keep concrete actions, named characters, "
        "and visual changes; cut interpretation, motivation guesses, and filler. "
        "Match the existing tense and reading rhythm. Output the rewritten "
        "description only — no preamble, no quotes."
    )
    user_msg = (
        f"Time budget: {target_secs:.1f} seconds (~{target_words} words at typical AD pace).\n\n"
        f"Current description:\n{text}\n\n"
        f"Rewrite to fit within the time budget."
    )

    try:
        sys.path.insert(0, str(storage.SERVER_DIR))
        from providers import get_text_provider

        result = get_text_provider().rewrite(
            system=system_msg, user=user_msg, temperature=0.4, max_tokens=400
        )
        new_text = result.text.strip()
        if len(new_text) >= 2 and new_text[0] in {'"', "'"} and new_text[-1] == new_text[0]:
            new_text = new_text[1:-1].strip()
        tokens = result.tokens
        model_used = result.model
    except Exception:
        logger.exception(
            "smart-fill failed",
            extra={"job_id": job_id, "operation": "smart_fill"},
        )
        return jsonify({"error": "smart-fill failed"}), 500

    return jsonify(
        {
            "ad": new_text,
            "target_secs": target_secs,
            "target_words": target_words,
            "estimated_secs": round(len(new_text.split()) / SMARTFILL_WPS, 2),
            "tokens_used": tokens,
            "model": model_used,
        }
    )


# ─── POST /api/jobs/<id>/tts-preview ──────────────────────────────────────────


@app.post("/api/jobs/<job_id>/tts-preview")
def tts_preview(job_id: str):
    if not storage.is_valid_storage_id(job_id):
        return jsonify({"error": "invalid id"}), 400
    if not storage.job_dir(job_id).exists():
        return jsonify({"error": "not found"}), 404

    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    voice = (body.get("voice") or "onyx").strip().lower()
    if not text:
        return jsonify({"error": "text required"}), 400
    if len(text) > 4000:
        return jsonify({"error": "text too long (max 4000 chars)"}), 400

    sys.path.insert(0, str(storage.SERVER_DIR))
    from tts_render import adjust_speed, clamp_speed, render_line

    speed = clamp_speed(body.get("speed"))

    base_key = hashlib.sha256(f"{voice}::{text}".encode()).hexdigest()[:24]
    cache = storage.tts_cache_dir(job_id)
    cache.mkdir(parents=True, exist_ok=True)
    base_path = cache / f"{base_key}.mp3"

    if not base_path.exists():
        try:
            render_line(text, voice, base_path)
        except Exception:
            logger.exception(
                "tts render failed",
                extra={"job_id": job_id, "operation": "tts_render"},
            )
            return jsonify({"error": "tts render failed"}), 500

    if abs(speed - 1.0) < 0.01:
        out_path = base_path
    else:
        speed_tag = f"{speed:.2f}".replace(".", "_")
        out_path = cache / f"{base_key}_s{speed_tag}.mp3"
        if not out_path.exists():
            try:
                adjust_speed(base_path, out_path, speed)
            except Exception:
                logger.exception(
                    "speed adjust failed",
                    extra={"job_id": job_id, "operation": "tts_speed_adjust"},
                )
                return jsonify({"error": "speed adjust failed"}), 500

    return send_file(out_path, mimetype="audio/mpeg", conditional=True)


# ─── PATCH /api/jobs/<id>/scenes/<scene_id> ───────────────────────────────────


@app.patch("/api/jobs/<job_id>/scenes/<scene_id>")
def patch_scene(job_id: str, scene_id: str):
    if not storage.is_valid_storage_id(job_id) or not storage.is_valid_storage_id(scene_id):
        return jsonify({"error": "invalid id"}), 400
    if not storage.job_dir(job_id).exists():
        return jsonify({"error": "not found"}), 404

    body = request.get_json(silent=True) or {}

    # Hold the per-job lock across the whole read-modify-write so parallel scene
    # flushes (Promise.all in the editor's preview/export) can't clobber each other.
    with storage.overrides_lock(job_id):
        overrides = storage.read_overrides(job_id)
        scene_ov = dict(overrides.get(scene_id, {}))

        if "ad" in body and isinstance(body["ad"], str):
            scene_ov["ad"] = body["ad"][:8000]
        if "active" in body and isinstance(body["active"], bool):
            scene_ov["active"] = body["active"]
        if "locked" in body and isinstance(body["locked"], bool):
            scene_ov["locked"] = body["locked"]
        if "voice" in body and isinstance(body["voice"], str):
            v = body["voice"].strip().lower()
            if v in export_service.VALID_VOICES:
                scene_ov["voice"] = v
        if "speed" in body:
            sys.path.insert(0, str(storage.SERVER_DIR))
            from tts_render import clamp_speed

            scene_ov["speed"] = clamp_speed(body["speed"])

        overrides[scene_id] = scene_ov
        storage.write_overrides(job_id, overrides)
    return jsonify({"sceneId": scene_id, "override": scene_ov})


# ─── PATCH /api/jobs/<id>/entities/<char_id> ──────────────────────────────────


@app.patch("/api/jobs/<job_id>/entities/<char_id>")
def patch_entity(job_id: str, char_id: str):
    if not storage.is_valid_storage_id(job_id) or not storage.is_valid_storage_id(char_id):
        return jsonify({"error": "invalid id"}), 400
    if not storage.job_dir(job_id).exists():
        return jsonify({"error": "not found"}), 404

    body = request.get_json(silent=True) or {}
    new_name = (body.get("name") or "").strip()
    if not new_name:
        return jsonify({"error": "name required"}), 400
    if len(new_name) > 200:
        return jsonify({"error": "name too long"}), 400

    entities_path = storage.entities_file(job_id)
    scenes_path = storage.scenes_file(job_id)
    if not entities_path.exists() or not scenes_path.exists():
        return jsonify({"error": "job data not found"}), 404

    try:
        sys.path.insert(0, str(storage.SERVER_DIR))
        from normalisation import (
            apply_manual_character_rename,
            rerender_scenes_with_updated_entities,
        )

        entities = json.loads(entities_path.read_text())
        scenes = json.loads(scenes_path.read_text())

        if not any(e.get("id") == char_id for e in entities):
            return jsonify({"error": "character not found"}), 404

        updated_entities = apply_manual_character_rename(entities, char_id, new_name)
        updated_scenes = rerender_scenes_with_updated_entities(scenes, updated_entities)

        entities_path.write_text(json.dumps(updated_entities, indent=2, ensure_ascii=False))
        scenes_path.write_text(json.dumps(updated_scenes, indent=2, ensure_ascii=False))
    except Exception:
        logger.exception(
            "rename failed",
            extra={"job_id": job_id, "operation": "entity_rename"},
        )
        return jsonify({"error": "rename failed"}), 500

    return jsonify({"characterId": char_id, "name": new_name})


# ─── Export ───────────────────────────────────────────────────────────────────


@app.post("/api/jobs/<job_id>/export")
def start_export(job_id: str):
    if not storage.is_valid_storage_id(job_id):
        return jsonify({"error": "invalid id"}), 400
    if not storage.job_dir(job_id).exists():
        return jsonify({"error": "not found"}), 404
    if not storage.scenes_file(job_id).exists():
        return jsonify({"error": "job not ready (scenes.json missing)"}), 409

    body = request.get_json(silent=True) or {}
    voice = (body.get("voice") or "onyx").strip().lower()
    if voice not in export_service.VALID_VOICES:
        voice = "onyx"
    fmt = (body.get("format") or "mp4").strip().lower()
    if fmt not in export_service.VALID_FORMATS:
        return jsonify({"error": f"unsupported format {fmt!r}"}), 400

    export_id = uuid.uuid4().hex[:12]
    edir = storage.export_dir(job_id, export_id)
    edir.mkdir(parents=True, exist_ok=True)
    (edir / "status.json").write_text(
        json.dumps(
            {
                "status": "queued",
                "progress": 0,
                "stage": "queued",
                "format": fmt,
            }
        )
    )

    t = threading.Thread(
        target=export_service.run_export,
        args=(job_id, export_id, fmt, voice),
        daemon=True,
    )
    t.start()
    return jsonify({"exportId": export_id, "format": fmt, "status": "queued"}), 202


@app.get("/api/jobs/<job_id>/export/<export_id>")
def get_export(job_id: str, export_id: str):
    if not storage.is_valid_storage_id(job_id) or not storage.is_valid_storage_id(export_id):
        return jsonify({"error": "invalid id"}), 400
    status_path = storage.export_dir(job_id, export_id) / "status.json"
    if not status_path.exists():
        return jsonify({"error": "not found"}), 404
    try:
        payload = json.loads(status_path.read_text())
        if payload.get("status") == "failed":
            payload["error"] = "export failed"
        return jsonify(payload)
    except Exception:
        return jsonify({"error": "corrupt status"}), 500


@app.get("/api/jobs/<job_id>/export/<export_id>/download")
def download_export(job_id: str, export_id: str):
    if not storage.is_valid_storage_id(job_id) or not storage.is_valid_storage_id(export_id):
        return jsonify({"error": "invalid id"}), 400
    edir = storage.export_dir(job_id, export_id)
    status_path = edir / "status.json"
    fmt = "mp4"
    if status_path.exists():
        try:
            fmt = json.loads(status_path.read_text()).get("format", "mp4")
        except Exception:
            pass
    out = edir / f"export.{fmt}"
    if not out.exists():
        return jsonify({"error": "not ready"}), 404
    # ?inline=1 streams for in-page playback (the eyes-closed study preview);
    # default is a download attachment.
    inline = request.args.get("inline") == "1"
    return send_file(
        out,
        mimetype=export_service.EXTENSION_MIME.get(fmt, "application/octet-stream"),
        as_attachment=not inline,
        download_name=f"instadescribe_{job_id[:8]}_{export_id}.{fmt}",
        conditional=True,
    )


# ─── GET /api/jobs/<id>/overrides ─────────────────────────────────────────────


@app.get("/api/jobs/<job_id>/overrides")
def get_overrides(job_id: str):
    """Server-stored per-scene edits, applied on top of scenes.json by the editor."""
    if not storage.is_valid_storage_id(job_id):
        return jsonify({"error": "invalid id"}), 400
    if not storage.job_dir(job_id).exists():
        return jsonify({"error": "not found"}), 404
    return jsonify(storage.read_overrides(job_id))


# ─── GET /api/jobs/<id>/evaluation ────────────────────────────────────────────


@app.get("/api/jobs/<job_id>/evaluation")
def get_evaluation(job_id: str):
    """Score the job's current (override-merged) descriptions against the AD-quality
    rubric. Pure CPU — no model call — so it is cheap to poll as the author edits."""
    if not storage.is_valid_storage_id(job_id):
        return jsonify({"error": "invalid id"}), 400
    if not storage.scenes_file(job_id).exists():
        return jsonify({"error": "job data not found"}), 404

    sys.path.insert(0, str(storage.SERVER_DIR))
    from evaluation import evaluate_ad

    merged = export_service.merged_scenes(job_id)
    try:
        audio_events = json.loads(storage.audio_events_file(job_id).read_text())
    except Exception:
        audio_events = []
    try:
        entities = json.loads(storage.entities_file(job_id).read_text())
    except Exception:
        entities = []

    duration = max((float(s.get("end", 0.0)) for s in merged), default=0.0)
    settings_path = storage.settings_file(job_id)
    if settings_path.exists():
        try:
            ds = json.loads(settings_path.read_text()).get("duration_secs")
            if ds:
                duration = float(ds)
        except Exception:
            pass

    return jsonify(evaluate_ad(merged, audio_events, entities, duration))


# ─── Study mode: per-session provisioning + anonymised event logging ──────────
#
# The evaluation runs many participants against ONE frozen clip. Each session gets
# its own copy of the clip's data, keyed by the anonymous session UUID, so a rename
# or edit never mutates the shared canonical draft or another participant's session.

STUDY_SOURCE_JOB = storage.validate_storage_id(
    os.environ.get("STUDY_SOURCE_JOB", "sintel-blender-cc"), field="study source job"
)
# Override with a mounted volume path in deploy to persist logs across restarts.
STUDY_LOGS_DIR = storage.SERVER_DIR / "study_logs"
if os.environ.get("STUDY_LOGS_DIR"):
    from pathlib import Path

    STUDY_LOGS_DIR = Path(os.environ["STUDY_LOGS_DIR"])


def _scene_count(job_id: str) -> int:
    try:
        raw = json.loads(storage.scenes_file(job_id).read_text())
        return sum(1 for s in raw if float(s.get("end", 0)) > float(s.get("start", 0)))
    except Exception:
        return 0


def _study_video_duration() -> int:
    src = storage.public_video_file(STUDY_SOURCE_JOB)
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(src),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return int(float(out.stdout.strip()))
    except Exception:
        return 0


@app.post("/api/study/session")
def provision_study_session():
    """Create an isolated copy of the frozen study clip for one participant.
    Idempotent: a returning session reuses its existing copy."""
    body = request.get_json(silent=True) or {}
    session_id = body.get("sessionId")
    if not _is_valid_study_session_id(session_id):
        return jsonify({"error": "valid sessionId required"}), 400

    src_data = storage.data_dir(STUDY_SOURCE_JOB)
    if not storage.scenes_file(STUDY_SOURCE_JOB).exists():
        return jsonify({"error": "study source clip not found on server"}), 500

    dst_data = storage.data_dir(session_id)
    if not dst_data.exists():
        shutil.copytree(src_data, dst_data)

    jdir = storage.job_dir(session_id)
    jdir.mkdir(parents=True, exist_ok=True)
    storage.result_file(session_id).write_text(
        json.dumps(
            {
                "data_path": f"/data/{session_id}",
                "video_file": f"/videos/{STUDY_SOURCE_JOB}.mp4",
                "scene_count": _scene_count(session_id),
            },
            indent=2,
        )
    )
    storage.status_file(session_id).write_text(
        json.dumps(
            {
                "status": "ready",
                "progress": 100,
                "stage": "complete",
                "error": None,
            }
        )
    )

    # Study scenes start inactive: the participant activates the ones they approve.
    # Seed the override file so a server-rendered preview matches the editor's
    # inactive-by-default state. Seed only when no overrides exist yet, so a
    # returning participant's activations are preserved.
    if not storage.overrides_path(session_id).exists():
        try:
            seed_scenes = json.loads(storage.scenes_file(session_id).read_text())
            seed = {
                s["scene_id"]: {"active": False}
                for s in seed_scenes
                if s.get("scene_id") and float(s.get("end", 0)) > float(s.get("start", 0))
            }
            storage.write_overrides(session_id, seed)
        except Exception:
            pass  # a missing or malformed scenes.json must not block provisioning

    return jsonify(
        {
            "projectId": session_id,
            "name": "Audio Description — Test Clip",
            "dataPath": f"/data/{session_id}",
            "videoFile": f"/videos/{STUDY_SOURCE_JOB}.mp4",
            "durationSecs": _study_video_duration(),
            "sceneCount": _scene_count(session_id),
            "status": "ready",
        }
    )


@app.post("/api/log")
def study_log():
    """Append one anonymised interaction event, keyed by session UUID. No PII."""
    body = request.get_json(silent=True) or {}
    session_id = body.get("sessionId")
    if not _is_valid_study_session_id(session_id):
        return jsonify({"error": "valid sessionId required"}), 400
    event = str(body.get("event") or "")[:80]
    try:
        detail_str = json.dumps(body.get("detail"))[:2000]
    except Exception:
        detail_str = "null"
    STUDY_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    record = json.dumps(
        {
            "sessionId": session_id,
            "event": event,
            "detail": detail_str,
            "ts": body.get("ts"),
        }
    )
    with storage.study_log_file(STUDY_LOGS_DIR, session_id).open("a") as f:
        f.write(record + "\n")
    return ("", 204)


@app.get("/api/study/config")
def study_config():
    """Runtime config the SPA fetches on load, so the questionnaire link is a host
    setting (env var) changeable without rebuilding the frontend."""
    return jsonify(
        {
            "questionnaireUrl": os.environ.get("STUDY_QUESTIONNAIRE_URL", ""),
            "questionnaireParam": os.environ.get("STUDY_QUESTIONNAIRE_PARAM", "session"),
        }
    )


# ─── GET/POST /api/providers ──────────────────────────────────────────────────


@app.get("/api/providers")
def get_providers():
    """Backends the app can switch to, with per-backend readiness (API keys stay
    in .env — never returned here) and the currently active one."""
    return jsonify({"backends": provider_status(), "current": active_backend()})


@app.post("/api/providers")
def set_providers():
    body = request.get_json(silent=True) or {}
    backend = (body.get("backend") or "").strip().lower()
    if backend not in VALID_BACKENDS:
        return jsonify({"error": f"unknown backend: {backend!r}"}), 400
    set_active_backend(backend)
    logger.info("model backend switched to %s via /api/providers", backend)
    return jsonify({"backends": provider_status(), "current": active_backend()})


# ─── Static serving (single-origin deploy: backend also serves the SPA) ───────


@app.get("/data/<path:subpath>")
def serve_data(subpath: str):
    try:
        path = storage.public_data_path(subpath)
    except storage.InvalidStoragePath:
        return jsonify({"error": "not found"}), 404
    if not path.is_file():
        return jsonify({"error": "not found"}), 404
    return send_file(path, conditional=True)


@app.get("/videos/<path:subpath>")
def serve_videos(subpath: str):
    try:
        path = storage.public_video_path(subpath)
    except storage.InvalidStoragePath:
        return jsonify({"error": "not found"}), 404
    if not path.is_file():
        return jsonify({"error": "not found"}), 404
    return send_file(path, conditional=True)


@app.get("/")
def serve_index():
    index_path = storage.static_asset_path("index.html")
    if index_path.exists():
        return send_file(index_path, conditional=True)
    return jsonify({"status": "InstaDescribe backend running (no SPA build present)"})


@app.get("/<path:path>")
def serve_spa(path: str):
    """Serve built assets, else fall back to index.html for client-side routes."""
    if path.startswith("api/"):
        return jsonify({"error": "not found"}), 404
    try:
        asset_path = storage.static_asset_path(path)
    except storage.InvalidStoragePath:
        return jsonify({"error": "not found"}), 404
    if asset_path.is_file():
        return send_file(asset_path, conditional=True)
    index_path = storage.static_asset_path("index.html")
    if index_path.exists():
        return send_file(index_path, conditional=True)
    return jsonify({"error": "not found"}), 404


# ─── Run ──────────────────────────────────────────────────────────────────────


def run_local_server() -> None:
    """Run the unauthenticated legacy surface on loopback only."""
    configure_logging()
    storage.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    port = int(os.environ.get("PORT", "8765"))
    logger.info("InstaDescribe legacy backend starting on http://%s:%s", LEGACY_BIND_HOST, port)
    logger.info("Jobs directory: %s", storage.JOBS_DIR)
    app.run(host=LEGACY_BIND_HOST, port=port, threaded=True, debug=False)


if __name__ == "__main__":
    run_local_server()
