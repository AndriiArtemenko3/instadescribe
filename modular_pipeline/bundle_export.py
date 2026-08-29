"""Render the atomic five-format InstaDescribe deliverable bundle.

This module is deliberately independent of Flask, FastAPI, PostgreSQL and S3.
The cloud render worker supplies an immutable reviewed scene snapshot and an
attempt-scoped directory, then publishes every returned file in one guarded
database transaction.  A partially rendered directory is never a public
deliverable set.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from export_service import VALID_VOICES
from exports import write_csv, write_docx, write_srt
from instadescribe_contracts.provider import TTS_BETA_MAX_APPROVED_SCENES_PER_REVIEW
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

DELIVERABLE_FILENAMES = {
    "mp4": "described_video.mp4",
    "mp3": "audio_description.mp3",
    "srt": "audio_description.srt",
    "csv": "audio_description.csv",
    "docx": "audio_description.docx",
}
FULL_MEDIA_TIMEOUT_SECS = 10800

ProgressCallback = Callable[[str, int], None]


def _noop_progress(_stage: str, _percent: int) -> None:
    return None


def _reviewed_scenes(scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return export-shaped scenes and reject an incomplete review snapshot."""
    reviewed: list[dict[str, Any]] = []
    approved_count = 0
    for scene in scenes:
        decision = scene.get("review_state")
        if decision not in {"approved", "rejected"}:
            raise ValueError("every scene must be approved or rejected before rendering")
        if decision == "approved":
            approved_count += 1
            if approved_count > TTS_BETA_MAX_APPROVED_SCENES_PER_REVIEW:
                raise ValueError("review exceeds the beta TTS synthesis limit")
        start = float(scene.get("start", 0.0))
        end = float(scene.get("end", start))
        if end <= start:
            raise ValueError("reviewed scene end must exceed start")
        reviewed.append(
            {
                **scene,
                "text": (scene.get("text") or scene.get("caption") or "").strip(),
                "active": decision == "approved",
            }
        )
    return reviewed


def _render_blocks(
    source_video: Path,
    scenes: list[dict[str, Any]],
    tts_dir: Path,
    default_voice: str,
    on_progress: ProgressCallback,
) -> list[AdBlock]:
    active = [scene for scene in scenes if scene["active"] and scene["text"]]
    blocks: list[AdBlock] = []
    tts_dir.mkdir(parents=True, exist_ok=True)

    for index, scene in enumerate(active):
        scene_id = str(scene.get("scene_id") or f"scene_{index + 1}")
        voice = scene.get("voice") or default_voice
        if voice not in VALID_VOICES:
            raise ValueError("reviewed scene contains an unsupported voice")
        speed = clamp_speed(scene.get("speed", 1.0))
        raw = tts_dir / f"{scene_id}_raw.mp3"
        normalised = tts_dir / f"{scene_id}_normalised.mp3"
        final = tts_dir / f"{scene_id}.mp3"
        render_line(scene["text"], voice, raw)
        normalise_audio(raw, normalised)
        adjust_speed(normalised, final, speed)
        duration = get_duration(final)
        start = max(0.0, float(scene["start"]) + 0.25)
        background_lufs = measure_gap_lufs(source_video, start, start + duration)
        blocks.append(
            AdBlock(
                scene_id=scene_id,
                start_secs=start,
                text=scene["text"],
                voice=voice,
                tts_path=final,
                tts_duration_secs=duration,
                background_lufs=background_lufs,
                apply_duck=background_lufs > DUCK_THRESHOLD,
            )
        )
        on_progress("rendering_tts", 10 + int(50 * (index + 1) / max(1, len(active))))
    return blocks


def _extract_mp3(source: Path, destination: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(source),
            "-vn",
            "-c:a",
            "libmp3lame",
            "-q:a",
            "2",
            str(destination),
        ],
        check=True,
        capture_output=True,
        timeout=FULL_MEDIA_TIMEOUT_SECS,
    )


def render_all_deliverables(
    *,
    source_video: Path,
    scenes: list[dict[str, Any]],
    entities_by_id: dict[str, dict[str, Any]],
    output_dir: Path,
    project_name: str,
    default_voice: str,
    on_progress: ProgressCallback = _noop_progress,
) -> dict[str, Path]:
    """Render MP4, MP3, SRT, CSV and DOCX from one locked review snapshot.

    All paths live beneath ``output_dir``. On failure, known outputs and TTS
    intermediates are removed before the exception propagates; the caller must
    still use an attempt-scoped directory and guarded publication.
    """
    if default_voice not in VALID_VOICES:
        raise ValueError("default voice is unsupported")
    if not source_video.is_file():
        raise ValueError("source video is missing")

    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {kind: output_dir / name for kind, name in DELIVERABLE_FILENAMES.items()}
    tts_dir = output_dir / ".tts"
    try:
        reviewed = _reviewed_scenes(scenes)
        on_progress("writing_documents", 5)
        write_srt(reviewed, outputs["srt"])
        write_csv(reviewed, outputs["csv"])
        write_docx(
            project_name or "Audio Description Script", reviewed, entities_by_id, outputs["docx"]
        )

        blocks = _render_blocks(source_video, reviewed, tts_dir, default_voice, on_progress)
        on_progress("mixing_video", 70)
        export_with_ad(source_video, blocks, outputs["mp4"])
        on_progress("extracting_audio", 90)
        _extract_mp3(outputs["mp4"], outputs["mp3"])

        missing = [kind for kind, path in outputs.items() if not path.is_file()]
        if missing:
            raise RuntimeError("render did not produce the complete deliverable set")
        on_progress("complete", 100)
        return outputs
    except Exception:
        for path in outputs.values():
            path.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(tts_dir, ignore_errors=True)
