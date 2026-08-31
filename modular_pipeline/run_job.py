#!/usr/bin/env python3
"""
run_job.py — launched as a subprocess by server.py for each upload job.

Usage:
    python3 run_job.py <job_id> <settings_json_path>

Sets env vars from settings BEFORE importing any pipeline modules so that
config.py picks up the per-job values at import time.
"""

import base64
import json
import os
import shutil
import subprocess
import sys
import traceback
from io import BytesIO
from pathlib import Path

# ─── Resolve paths ────────────────────────────────────────────────────────────

PIPELINE_DIR = Path(__file__).resolve().parent
APP_DIR = PIPELINE_DIR.parent / "App"

job_id = sys.argv[1]
settings_path = Path(sys.argv[2])
settings = json.loads(settings_path.read_text())

JOB_DIR = PIPELINE_DIR / "jobs" / job_id
FRAMES_DIR = JOB_DIR / "frames"
WORK_DIR = JOB_DIR / "work"  # memory, chunks, reports
OUTPUT_DIR = APP_DIR / "public" / "data" / job_id  # final JSON → served by Vite
STATUS_FILE = JOB_DIR / "status.json"

# ─── Prompt assembly ──────────────────────────────────────────────────────────

DETAIL_INSTRUCTIONS = {
    1: "Provide brief, concise audio descriptions (1–2 sentences per scene).",
    2: "Provide standard-length audio descriptions.",
    3: "Provide detailed audio descriptions including background and context.",
    4: "Provide rich, descriptive audio descriptions with character detail and environment.",
    5: "Provide comprehensive, highly detailed audio descriptions covering all visual elements.",
}

STYLE_PREFIXES = {
    "documentary": "This is a documentary. Describe events factually and clearly.",
    "cinematic": "This is a cinematic film. Prioritise visual storytelling, mood, and composition.",
    "news": "This is a news broadcast. Be precise, neutral, and factual.",
    "sports": "This is a sports broadcast. Focus on player actions, positions, and ball movement.",
    "education": "This is educational content. Use clear, accessible language. Avoid jargon.",
}


def build_prompt(s: dict) -> str:
    parts = []
    style = s.get("preset_style", "documentary")
    detail = s.get("detail_level", 3)
    custom = (s.get("custom_prompt") or "").strip()
    if style in STYLE_PREFIXES:
        parts.append(STYLE_PREFIXES[style])
    parts.append(DETAIL_INSTRUCTIONS.get(detail, DETAIL_INSTRUCTIONS[3]))
    if custom:
        parts.append(custom)
    return " ".join(parts)


# ─── Set env vars BEFORE importing anything from pipeline ─────────────────────

fps = settings.get("fps", 1.0)
step = round(1.0 / fps, 6) if fps else 1.0

os.environ["JOB_VIDEO_ID"] = job_id
os.environ["JOB_VIDEO_PATH"] = settings["video_path"]
os.environ["JOB_FRAMES_DIR"] = str(FRAMES_DIR)
os.environ["JOB_PROJECT_DIR"] = str(WORK_DIR)
os.environ["JOB_OUTPUT_DIR"] = str(OUTPUT_DIR)
os.environ["JOB_MODEL"] = settings.get("model", "gpt-4.1")
os.environ["JOB_IMAGE_DETAIL"] = settings.get("frame_quality", "low")
os.environ["JOB_CHUNK_SIZES"] = str(settings.get("chunk_size", 60))
os.environ["JOB_PROMPT"] = build_prompt(settings)
os.environ["JOB_SKIP_EXISTING"] = "false"

lang = settings.get("language") or ""
if lang:
    os.environ["JOB_WHISPER_LANGUAGE"] = lang

# ─── Now safe to import pipeline modules ──────────────────────────────────────

sys.path.insert(0, str(PIPELINE_DIR))

from config import (
    CHUNK_SIZES,
    IMAGE_DETAIL,
    MEMORY_DIR,
    MODEL,
    RUNS_DIR,
    VIDEO_ID,
)
from config import (
    OUTPUT_DIR as CFG_OUTPUT_DIR,
)
from frame_extraction import extract_frames
from frames import chunk_frames, load_frames
from memory import load_memory, update_memory
from normalisation import export_app_state
from pipeline import (
    analyze_chunk,
    estimate_quality_proxy,
    safe_json_dump,
    validate_chunk_character_ids,
)
from providers import get_vision_provider

# ─── Status helpers ───────────────────────────────────────────────────────────


def write_status(
    status: str,
    progress: int,
    stage: str,
    error: str | None = None,
    chunks_done: int = 0,
    chunks_total: int = 0,
) -> None:
    STATUS_FILE.write_text(
        json.dumps(
            {
                "status": status,
                "progress": progress,
                "stage": stage,
                "chunks_done": chunks_done,
                "chunks_total": chunks_total,
                "error": error,
            },
            indent=2,
        )
    )


# ─── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    JOB_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    write_status("processing", 2, "initializing")

    video_path = Path(settings["video_path"])

    # ── Stage 1: Frame extraction (2 → 15%) ────────────────────────────────
    write_status("processing", 5, "extracting_frames")
    print(f"[{job_id}] Extracting frames from {video_path.name} (step={step}s)…")
    frame_count = extract_frames(video_path, FRAMES_DIR, step=step)
    print(f"[{job_id}] {frame_count} frames extracted → {FRAMES_DIR}")

    # Generate dashboard thumbnails from the first extracted frame.
    # Three artifacts: a sized JPG fallback, an AVIF preferred source,
    # and a tiny base64 WebP for the LQIP blur placeholder. AVIF is
    # best-effort: a missing encoder must not fail the job.
    poster_src = FRAMES_DIR / "frame_0000.jpg"
    poster_jpg = OUTPUT_DIR / "poster.jpg"
    poster_avif = OUTPUT_DIR / "poster.avif"
    poster_placeholder = None
    poster_avif_ok = False

    if poster_src.exists():
        # Tier 1: scaled JPG (520×N, 16:9 retina-suitable for ~260px tiles)
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(poster_src),
                    "-vf",
                    "scale=520:-2",
                    "-q:v",
                    "4",
                    "-frames:v",
                    "1",
                    str(poster_jpg),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            shutil.copy2(str(poster_src), str(poster_jpg))  # fall back to original

        # Tier 3: AVIF (best-effort)
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(poster_src),
                    "-vf",
                    "scale=520:-2",
                    "-c:v",
                    "libsvtav1",
                    "-crf",
                    "35",
                    "-frames:v",
                    "1",
                    "-f",
                    "avif",
                    str(poster_avif),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            poster_avif_ok = poster_avif.exists() and poster_avif.stat().st_size > 0
        except Exception:
            pass

        # Tier 2: LQIP — 24×14 WebP base64, decodes to a colorful blur in <1ms
        try:
            from PIL import Image  # local import keeps cold-start fast

            img = Image.open(poster_src).convert("RGB")
            img.thumbnail((24, 24), Image.Resampling.LANCZOS)
            buf = BytesIO()
            img.save(buf, format="WebP", quality=50, method=6)
            poster_placeholder = base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception:
            poster_placeholder = None

    write_status("processing", 15, "extracting_frames")

    frames = load_frames(FRAMES_DIR)

    # ── Stage 2: Audio pipeline (15 → 25%) ─────────────────────────────────
    write_status("processing", 16, "transcribing_audio")
    print(f"[{job_id}] Running audio pipeline…")

    provided_transcript_path = settings.get("provided_transcript_path")
    provided_transcript_format = settings.get("provided_transcript_format")
    if provided_transcript_path:
        from timed_transcript import (
            parse_timed_transcript_bytes,
            transcript_ad_gaps,
            transcript_audio_events,
        )

        transcript_payload = parse_timed_transcript_bytes(
            Path(provided_transcript_path).read_bytes(),
            provided_transcript_format,
            video_duration_seconds=float(settings["duration_secs"]),
        )
        audio_payload = transcript_audio_events(
            transcript_payload,
            video_duration_seconds=float(settings["duration_secs"]),
        )
        safe_json_dump(CFG_OUTPUT_DIR / "audio_events.json", audio_payload)
        safe_json_dump(
            CFG_OUTPUT_DIR / "ad_placement_gaps.json",
            transcript_ad_gaps(
                transcript_payload,
                video_duration_seconds=float(settings["duration_secs"]),
            ),
        )
        safe_json_dump(CFG_OUTPUT_DIR / "transcript.json", transcript_payload)
        audio_events = []
    elif settings.get("audio_extraction", True):
        from audio_whisperx_pipeline import (
            calculate_ad_gaps,
            load_audio_events,
            load_transcript,
            transcript_segment_to_dict,
        )

        audio_events = load_audio_events(video_path, frames)
        audio_payload = [
            {
                "start": e.start,
                "end": e.end,
                "event_type": e.event_type,
                "confidence": e.confidence,
                "transcript": e.transcript,
            }
            for e in audio_events
        ]
        safe_json_dump(CFG_OUTPUT_DIR / "audio_events.json", audio_payload)

        gaps = calculate_ad_gaps(audio_events)
        safe_json_dump(CFG_OUTPUT_DIR / "ad_placement_gaps.json", gaps)

        transcript = load_transcript(video_path, frames)
        transcript_payload = [transcript_segment_to_dict(s) for s in transcript]
        safe_json_dump(CFG_OUTPUT_DIR / "transcript.json", transcript_payload)
    else:
        # Audio off — write empty files so the frontend doesn't 404
        safe_json_dump(CFG_OUTPUT_DIR / "audio_events.json", [])
        safe_json_dump(CFG_OUTPUT_DIR / "ad_placement_gaps.json", [])
        safe_json_dump(CFG_OUTPUT_DIR / "transcript.json", [])
        audio_events = []

    write_status("processing", 25, "transcribing_audio")

    # ── Stage 3: LLM chunk analysis (25 → 88%) ─────────────────────────────
    write_status("processing", 25, "analyzing_frames")
    print(f"[{job_id}] Running LLM analysis…")

    vision = get_vision_provider()
    chunk_size = CHUNK_SIZES[0]
    chunks = chunk_frames(frames, chunk_size)
    total = len(chunks)

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)

    memory_file = MEMORY_DIR / f"memory_chunk_{chunk_size}s.json"
    chunks_dir = RUNS_DIR / f"chunk_{chunk_size}s" / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)

    memory = load_memory(memory_file)
    results: list = []
    total_input = total_output = total_tokens_all = 0

    for chunk_id, chunk in enumerate(chunks):
        print(f"[{job_id}] chunk {chunk_id + 1}/{total}…")
        chunk_output = analyze_chunk(vision, chunk_id, chunk, memory)
        safe_json_dump(chunks_dir / f"chunk_{chunk_id:03d}.json", chunk_output)

        orphaned = validate_chunk_character_ids(chunk_output)
        if orphaned:
            print(f"[{job_id}] WARNING orphaned char IDs chunk {chunk_id}: {orphaned}")

        memory = update_memory(memory, chunk_output)
        safe_json_dump(memory_file, memory)

        usage = chunk_output.get("_usage", {})
        total_input += usage.get("input_tokens") or 0
        total_output += usage.get("output_tokens") or 0
        total_tokens_all += usage.get("total_tokens") or 0

        results.append(
            {
                "chunk_id": chunk_id,
                "chunk_start": chunk_output.get("chunk_start"),
                "chunk_end": chunk_output.get("chunk_end"),
                "usage": usage,
                "quality_proxy": estimate_quality_proxy(chunk_output),
                "num_scenes": len(chunk_output.get("scenes", [])),
                "global_summary": chunk_output.get("global_summary", ""),
            }
        )

        progress = 25 + int(63 * (chunk_id + 1) / total)
        write_status(
            "processing", progress, "analyzing_frames", chunks_done=chunk_id + 1, chunks_total=total
        )

    summary = {
        "video_id": VIDEO_ID,
        "chunk_size": chunk_size,
        "num_chunks": total,
        "total_usage": {
            "input_tokens": total_input,
            "output_tokens": total_output,
            "total_tokens": total_tokens_all,
        },
        "chunks": results,
    }

    # ── Stage 4: Export to App/public/data/{jobId}/ (88 → 100%) ────────────
    write_status("processing", 88, "exporting")
    print(f"[{job_id}] Exporting app state…")

    import json as _json

    final_memory = _json.loads(memory_file.read_text())

    export_app_state(
        memory=final_memory,
        summaries=[summary],
        out_dir=CFG_OUTPUT_DIR,
        video_id=VIDEO_ID,
        model=MODEL,
        image_detail=IMAGE_DETAIL,
        chunk_sizes=CHUNK_SIZES,
        num_frames=len(frames),
    )

    # Copy the 5 frontend files that CFG_OUTPUT_DIR now contains.
    # (CFG_OUTPUT_DIR IS OUTPUT_DIR which IS App/public/data/{jobId}/)
    # so no copy needed — they're already there.  Verify:
    for fname in (
        "scenes.json",
        "audio_events.json",
        "ad_placement_gaps.json",
        "transcript.json",
        "entities.json",
    ):
        dst = OUTPUT_DIR / fname
        if not dst.exists():
            print(f"[{job_id}] WARNING: expected output file missing: {fname}")

    # Copy video to App/public/videos/ so the browser can serve it
    videos_dir = APP_DIR / "public" / "videos"
    videos_dir.mkdir(parents=True, exist_ok=True)
    video_src = JOB_DIR / "video.mp4"
    video_public = videos_dir / f"{job_id}.mp4"
    if video_src.exists():
        shutil.copy2(str(video_src), str(video_public))
        video_file_path = f"/videos/{job_id}.mp4"
    else:
        video_file_path = None
        print(f"[{job_id}] WARNING: source video not found, video preview will be unavailable")

    # Write final status
    scene_count = (
        len(_json.loads((OUTPUT_DIR / "scenes.json").read_text()))
        if (OUTPUT_DIR / "scenes.json").exists()
        else 0
    )
    write_status("ready", 100, "complete")

    # Write metadata sidecar for the poll endpoint
    (JOB_DIR / "result.json").write_text(
        _json.dumps(
            {
                "data_path": f"/data/{job_id}",
                "video_file": video_file_path,
                "poster_file": f"/data/{job_id}/poster.jpg" if poster_jpg.exists() else None,
                "poster_avif_file": f"/data/{job_id}/poster.avif" if poster_avif_ok else None,
                "poster_placeholder": poster_placeholder,
                "scene_count": scene_count,
                "tokens_used": total_tokens_all,
            }
        )
    )

    print(
        f"[{job_id}] Done. {scene_count} scenes, {total_tokens_all} tokens, video={video_file_path}"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(f"[{job_id}] FAILED:", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        STATUS_FILE.write_text(
            json.dumps(
                {
                    "status": "failed",
                    "progress": 0,
                    "stage": "failed",
                    "chunks_done": 0,
                    "chunks_total": 0,
                    "error": "job processing failed",
                }
            )
        )
        sys.exit(1)
