"""Export worker: merge scene overrides, render TTS, mix, and write outputs.

The route layer only starts a background thread and polls a status file; all the
heavy lifting (TTS render, loudness mix, ffmpeg) lives here so it can be reasoned
about and tested in isolation.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading

import storage

logger = logging.getLogger(__name__)

VALID_VOICES = {"onyx", "nova", "alloy", "shimmer", "echo", "fable"}
VALID_FORMATS = {"mp4", "mp3", "srt", "csv", "docx"}
AUDIO_FORMATS = {"mp4", "mp3"}
EXTENSION_MIME = {
    "mp4": "video/mp4",
    "mp3": "audio/mpeg",
    "srt": "application/x-subrip",
    "csv": "text/csv",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

# Cap simultaneous heavy renders so concurrent participants (e.g. several clicking
# the eyes-closed preview at once) can't exhaust the box's CPU/memory. Extra
# renders queue and the participant simply sees "rendering…" a little longer.
MAX_CONCURRENT_RENDERS = int(os.environ.get("MAX_CONCURRENT_RENDERS", "2"))
_render_sem = threading.Semaphore(MAX_CONCURRENT_RENDERS)


def merged_scenes(job_id: str) -> list[dict]:
    """scenes.json with scene_overrides.json applied as text/active/voice/locked.
    Zero-duration scenes are dropped (the pipeline occasionally emits start==end at
    the tail; the frontend filters them too)."""
    raw = json.loads(storage.scenes_file(job_id).read_text())
    overrides = storage.read_overrides(job_id)
    merged: list[dict] = []
    for s in raw:
        start = float(s.get("start", 0.0))
        end = float(s.get("end", start))
        if end <= start:
            continue
        sid = s.get("scene_id", "")
        ov = overrides.get(sid, {})
        merged.append(
            {
                **s,
                "text": ov.get("ad", s.get("caption", "")),
                "active": ov.get("active", True),
                "voice": ov.get("voice"),
                "speed": ov.get("speed", 1.0),
                "locked": ov.get("locked", False),
            }
        )
    return merged


def run_export(job_id: str, export_id: str, fmt: str, voice_default: str) -> None:
    """Background export. Writes status.json into the export dir."""
    edir = storage.export_dir(job_id, export_id)
    status_path = edir / "status.json"

    def update(status: str, progress: int, stage: str, **extra) -> None:
        payload = {"status": status, "progress": progress, "stage": stage, "format": fmt}
        payload.update(extra)
        status_path.write_text(json.dumps(payload, indent=2))

    _render_sem.acquire()  # queue behind any in-flight renders
    try:
        sys.path.insert(0, str(storage.SERVER_DIR))
        merged = merged_scenes(job_id)

        # ── Text-only formats: no TTS, no ffmpeg, near-instant ────────────────
        if fmt in {"srt", "csv", "docx"}:
            from exports import write_csv, write_docx, write_srt

            out_path = edir / f"export.{fmt}"
            update("processing", 50, f"writing_{fmt}")

            if fmt == "srt":
                write_srt(merged, out_path)
            elif fmt == "csv":
                write_csv(merged, out_path)
            else:  # docx
                entities = json.loads(storage.entities_file(job_id).read_text())
                entities_by_id = {e["id"]: e for e in entities}
                settings_path = storage.settings_file(job_id)
                project_name = "Audio Description Script"
                if settings_path.exists():
                    try:
                        project_name = json.loads(settings_path.read_text()).get(
                            "project_name", project_name
                        )
                    except Exception:
                        pass
                meta = storage.read_meta(job_id)
                if meta.get("name"):
                    project_name = meta["name"]
                write_docx(project_name, merged, entities_by_id, out_path)

            update(
                "ready",
                100,
                "complete",
                download_url=f"/api/jobs/{job_id}/export/{export_id}/download",
            )
            return

        # ── Audio/video formats: render TTS, mix, optionally extract mp3 ──────
        from tts_render import (
            DUCK_THRESHOLD,
            AdBlock,
            adjust_speed,
            clamp_speed,
            export_with_ad,
            get_duration,
            measure_gap_lufs,
            normalise_audio,
            render_line,
        )

        video_rel = storage.video_url_for(job_id)
        if not video_rel:
            update("failed", 0, "video_missing", error="no source video found")
            return
        video_path = storage.recorded_video_path(video_rel)
        if not video_path.exists():
            update("failed", 0, "video_missing", error="source video unavailable")
            return

        active = []
        for s in merged:
            if not s.get("active", True):
                continue
            text = (s.get("text") or "").strip()
            if not text:
                continue
            voice = s.get("voice") or voice_default
            speed = clamp_speed(s.get("speed", 1.0))
            active.append((s["scene_id"], s["start"], text, voice, speed))

        total = len(active)
        update("processing", 5, "rendering_tts", total_scenes=total, done=0)

        blocks: list[AdBlock] = []
        tts_dir = edir / "tts"
        tts_dir.mkdir(parents=True, exist_ok=True)

        for i, (sid, start, text, voice, speed) in enumerate(active):
            raw = tts_dir / f"{sid}_raw.mp3"
            norm = tts_dir / f"{sid}_norm.mp3"
            final = tts_dir / f"{sid}_final.mp3"
            render_line(text, voice, raw)
            normalise_audio(raw, norm)
            adjust_speed(norm, final, speed)
            tts_duration = get_duration(final)
            ad_start = max(0.0, start + 0.25)
            bg_lufs = measure_gap_lufs(video_path, ad_start, ad_start + tts_duration)
            blocks.append(
                AdBlock(
                    scene_id=sid,
                    start_secs=ad_start,
                    text=text,
                    voice=voice,
                    tts_path=final,
                    tts_duration_secs=tts_duration,
                    background_lufs=bg_lufs,
                    apply_duck=bg_lufs > DUCK_THRESHOLD,
                )
            )
            progress = 5 + int(70 * (i + 1) / max(1, total))
            update("processing", progress, "rendering_tts", total_scenes=total, done=i + 1)

        update("processing", 80, "mixing_video", total_scenes=total, done=total)
        out_mp4 = edir / "export.mp4"
        export_with_ad(video_path, blocks, out_mp4)

        if fmt == "mp3":
            update("processing", 92, "extracting_mp3", total_scenes=total, done=total)
            out_mp3 = edir / "export.mp3"
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(out_mp4),
                    "-vn",
                    "-c:a",
                    "libmp3lame",
                    "-q:a",
                    "2",
                    str(out_mp3),
                ],
                check=True,
                capture_output=True,
            )

        update(
            "ready",
            100,
            "complete",
            total_scenes=total,
            done=total,
            download_url=f"/api/jobs/{job_id}/export/{export_id}/download",
        )
    except Exception:
        logger.exception(
            "export failed",
            extra={"job_id": job_id, "export_id": export_id, "operation": "export"},
        )
        update("failed", 0, "error", error="export failed")
    finally:
        _render_sem.release()
