#!/usr/bin/env python3
"""G0 container-feasibility smoke — runs INSIDE the worker image.

Exercises the unrefactored run_job.py subprocess contract in a synthesized job
workspace (the exact seam the G5 executor will own) and prints a JSON evidence
report to stdout. Two cases:

  A. fixture job: fake provider, audio extraction ON — must exit 0 AND produce
     the required artifact set (exit 0 alone is insufficient).
  B. deliberately broken pre-main() job: invalid settings JSON crashes run_job
     before its __main__ try/except — must exit nonzero while the seeded
     status.json still reads "queued" (proves exit-code authority).

Refuses to run with any backend other than "fake": G0 must not make paid calls.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from modular_pipeline.environment import getenv_compat

APP = Path("/app")
PIPELINE_DIR = APP / "modular_pipeline"
JOBS_DIR = PIPELINE_DIR / "jobs"
DATA_DIR = APP / "App" / "public" / "data"
VIDEOS_DIR = APP / "App" / "public" / "videos"
FIXTURE = APP / "fixtures" / "sintel-blender-cc.mp4"
TIMEOUT_SECS = int(os.environ.get("G0_TIMEOUT_SECS", "2700"))


def ffprobe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout.strip())


def seed_job(job_id: str, settings_text: str) -> Path:
    """Mirror the server's job seeding (server.py:70-107): job dir, video copy,
    settings.json, and a 'queued' status.json."""
    jdir = JOBS_DIR / job_id
    jdir.mkdir(parents=True, exist_ok=True)
    video = jdir / "video.mp4"
    if not video.exists():
        video.write_bytes(FIXTURE.read_bytes())
    (jdir / "settings.json").write_text(settings_text)
    (jdir / "status.json").write_text(
        json.dumps(
            {
                "status": "queued",
                "progress": 0,
                "stage": "queued",
                "chunks_done": 0,
                "chunks_total": 0,
                "error": None,
            },
            indent=2,
        )
    )
    return jdir


def run_job(job_id: str, env: dict) -> tuple[int, float, str]:
    jdir = JOBS_DIR / job_id
    stderr_path = jdir / "stderr.log"
    t0 = time.monotonic()
    with open(stderr_path, "w") as stderr:
        proc = subprocess.Popen(
            [sys.executable, str(PIPELINE_DIR / "run_job.py"), job_id, str(jdir / "settings.json")],
            cwd=str(PIPELINE_DIR),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=stderr,
        )
        try:
            rc = proc.wait(timeout=TIMEOUT_SECS)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            rc = -9
    dur = time.monotonic() - t0
    tail = stderr_path.read_text()[-2000:] if stderr_path.exists() else ""
    return rc, dur, tail


def valid_json_file(path: Path) -> tuple[bool, object]:
    try:
        return True, json.loads(path.read_text())
    except Exception:
        return False, None


def validate_artifacts(job_id: str) -> dict:
    out = DATA_DIR / job_id
    checks: dict[str, bool] = {}
    ok, scenes = valid_json_file(out / "scenes.json")
    checks["scenes.json valid non-empty list"] = ok and isinstance(scenes, list) and len(scenes) > 0
    ok, entities = valid_json_file(out / "entities.json")
    checks["entities.json valid"] = ok and isinstance(entities, list)
    for name in ("audio_events.json", "ad_placement_gaps.json", "transcript.json"):
        ok, payload = valid_json_file(out / name)
        checks[f"{name} valid list"] = ok and isinstance(payload, list)
    # Audio ON must produce real (non-empty) audio evidence for a dialogue clip.
    ok, events = valid_json_file(out / "audio_events.json")
    checks["audio_events non-empty (audio ON)"] = (
        ok and isinstance(events, list) and len(events) > 0
    )
    # Video contract the future worker uploads from:
    checks["public video copy exists"] = (VIDEOS_DIR / f"{job_id}.mp4").exists()
    ok, result = valid_json_file(JOBS_DIR / job_id / "result.json")
    checks["result.json valid with video_file + scene_count"] = (
        ok
        and isinstance(result, dict)
        and result.get("video_file") == f"/videos/{job_id}.mp4"
        and isinstance(result.get("scene_count"), int)
        and result["scene_count"] > 0
    )
    checks["poster.jpg present (optional, informational)"] = (out / "poster.jpg").exists()
    return checks


def du_bytes(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) if path.exists() else 0


def main() -> int:
    backend = getenv_compat("INSTADESCRIBE_BACKEND") or ""
    if backend != "fake":
        print(
            json.dumps({"error": f"G0 smoke requires INSTADESCRIBE_BACKEND=fake, got {backend!r}"})
        )
        return 2
    env = dict(os.environ)

    report: dict = {
        "python": sys.version.split()[0],
        "backend": backend,
        "fixture_duration_secs": round(ffprobe_duration(FIXTURE), 2),
        "timeout_secs": TIMEOUT_SECS,
    }

    # Case A — fixture job, fake provider, audio ON.
    job_a = "g0-fixture-smoke"
    settings_a = {
        "job_id": job_a,
        "video_path": str(JOBS_DIR / job_a / "video.mp4"),
        "model": "gpt-4.1",
        "frame_quality": "low",
        "fps": 1.0,
        "chunk_size": 60,
        "audio_extraction": True,
        "custom_prompt": "",
        "language": None,
        "detail_level": 3,
        "preset_style": "documentary",
        "project_name": "G0 fixture smoke",
        "duration_secs": report["fixture_duration_secs"],
    }
    seed_job(job_a, json.dumps(settings_a, indent=2))
    rc_a, dur_a, stderr_a = run_job(job_a, env)
    checks = validate_artifacts(job_a)
    required = {k: v for k, v in checks.items() if "optional" not in k}
    case_a_pass = rc_a == 0 and all(required.values())
    report["case_a"] = {
        "exit_code": rc_a,
        "processing_secs": round(dur_a, 1),
        "artifact_checks": checks,
        "pass": case_a_pass,
        "stderr_tail": stderr_a if rc_a != 0 else "",
    }

    # Case B — broken pre-main(): invalid settings JSON. run_job crashes at
    # json.loads (line 29) before __main__'s try/except; the seeded status.json
    # must remain 'queued' while the exit code is nonzero.
    job_b = "g0-broken-premain"
    seed_job(job_b, "{ this is not json")
    rc_b, dur_b, stderr_b = run_job(job_b, env)
    status_b = json.loads((JOBS_DIR / job_b / "status.json").read_text())
    case_b_pass = rc_b not in (0, -9) and status_b.get("status") == "queued"
    report["case_b"] = {
        "exit_code": rc_b,
        "status_json_after": status_b.get("status"),
        "detected_from_exit_code_alone": case_b_pass,
        "stderr_tail": stderr_b[-400:],
    }

    # Workspace usage (end-state; the dominant footprints are frames + outputs).
    report["workspace_bytes"] = {
        "jobs_dir": du_bytes(JOBS_DIR),
        "data_dir": du_bytes(DATA_DIR),
        "videos_dir": du_bytes(VIDEOS_DIR),
        "tmp": du_bytes(Path("/tmp")),
    }
    peak = Path("/sys/fs/cgroup/memory.peak")
    report["container_memory_peak_bytes"] = int(peak.read_text()) if peak.exists() else None
    report["pass"] = case_a_pass and case_b_pass

    print(json.dumps(report, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
