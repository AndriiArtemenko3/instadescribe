#!/usr/bin/env python3
"""G8 Part E — mandatory five-minute 8 GiB memory test (risk R2).

Generates a temporary ~299 s video at runtime by looping the licensed Sintel
fixture (never committed), asserts its actual ffprobe duration is > 295 and
<= 300 s, uploads it through the real protected path, and processes it with
the CURRENT production worker image under Compose limits of 2 vCPU / 8 GiB,
concurrency one, fake provider, audio extraction ON, real FFmpeg/VAD/ASR from
baked offline weights, against a project-scoped PostgreSQL/LocalStack stack.

Fails nonzero on: OOM evidence (cgroup memory.events oom/oom_kill, Docker
OOMKilled), container restart, wrong image, a non-Ready terminal state,
missing artifacts, nonempty work queue or DLQ, measurement ambiguity, or
teardown residue. Prints a JSON evidence block on success. All resources are
run-owned (Compose project instadescribe-g8-memtest + one temp dir); cleanup is
attempted on every exit path.
"""

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from g8_common import (  # noqa: E402
    FIXTURE,
    artifact_rows,
    await_terminal,
    aws,
    cgroup_memory,
    cleanup_compose_project,
    compose,
    container_state,
    create_job_and_upload,
    db_engine,
    db_row,
    die,
    getenv_compat,
    preflight,
    preserve_primary_cleanup,
    queue_attrs,
    run,
    verify_image_source_binding,
)

PROJECT = "instadescribe-g8-memtest"
IMAGE = getenv_compat("INSTADESCRIBE_WORKER_IMAGE") or "instadescribe-worker:g8"
API_IMAGE = getenv_compat("INSTADESCRIBE_API_IMAGE") or "instadescribe-api:g8"
TARGET_SECS = 299
REQUIRED_TYPES = {
    "scenes_json",
    "entities_json",
    "audio_events_json",
    "ad_placement_gaps_json",
    "transcript_json",
    "system_info_json",
    "source_video",
}
OPTIONAL_TYPES = {"poster_jpg", "poster_avif"}

evidence: dict = {"project": PROJECT, "image_tag": IMAGE, "target_secs": TARGET_SECS}


def memory_compose(*args: str, **kwargs) -> str:
    """Every memory-gate Compose operation selects the G8 image override."""
    kwargs["g8_images"] = True
    return compose(PROJECT, *args, **kwargs)


def assert_running_image(container_id: str, expected_image_id: str, role: str) -> None:
    actual = container_state(container_id)["image"]
    if actual != expected_image_id:
        die(f"{role} container runs an unexpected image")


def prepare_stack(env: dict) -> None:
    """Acquire/refuse/clean through the exact G8 Compose definition."""
    preflight(PROJECT, env=env, g8_images=True)


def start_base_stack(env: dict, api_image_id: str) -> str:
    memory_compose(
        "up",
        "--no-build",
        "-d",
        "--wait",
        "postgres",
        "localstack",
        "migrate",
        "api",
        env=env,
    )
    api_ct = memory_compose("ps", "-q", "api", env=env).strip()
    assert_running_image(api_ct, api_image_id, "api-initial")
    return api_ct


def start_worker_stack(env: dict, api_image_id: str, worker_image_id: str) -> str:
    memory_compose(
        "--profile",
        "worker",
        "up",
        "-d",
        "--no-build",
        "--wait",
        "worker",
        env=env,
    )
    worker_ct = memory_compose(
        "--profile",
        "worker",
        "ps",
        "-q",
        "worker",
        env=env,
    ).strip()
    api_ct = memory_compose("ps", "-q", "api", env=env).strip()
    assert_running_image(api_ct, api_image_id, "api-post-worker")
    assert_running_image(worker_ct, worker_image_id, "worker")
    return worker_ct


def stop_worker_stack(env: dict) -> None:
    memory_compose("--profile", "worker", "stop", "worker", env=env)


def teardown_stack(env: dict) -> None:
    cleanup_compose_project(PROJECT, env=env, g8_images=True)


def generate_five_minute_video(tmp: Path) -> tuple[Path, float]:
    """Loop the 120 s fixture to ~299 s with stream copy; assert the measured
    duration is > 295 and <= 300 BEFORE any upload."""
    out = tmp / "g8-five-minute.mp4"
    ffmpeg_version = run(["ffmpeg", "-version"]).splitlines()[0]
    cmd = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-stream_loop",
        "2",
        "-i",
        str(FIXTURE),
        "-t",
        str(TARGET_SECS),
        "-c",
        "copy",
        str(out),
    ]
    run(cmd)
    probe = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(out),
        ]
    ).strip()
    duration = float(probe)
    if not 295.0 < duration <= 300.0:
        die(f"generated duration {duration}s outside (295, 300]")
    evidence["ffmpeg_version"] = ffmpeg_version
    evidence["ffmpeg_command"] = " ".join(cmd)
    evidence["generated_sha256"] = hashlib.sha256(out.read_bytes()).hexdigest()
    evidence["generated_note"] = (
        "hash recorded for this run only — no cross-ffmpeg-version byte reproducibility is claimed"
    )
    evidence["generated_bytes"] = out.stat().st_size
    evidence["measured_duration_secs"] = duration
    evidence["source_fixture_sha256"] = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    return out, duration


def main() -> None:
    # G8.1 B1: a stale-but-compatible tag fails BEFORE any database/queue
    # work — the image label must match the digest of the current tree.
    binding = verify_image_source_binding(IMAGE, "worker")
    image_id = binding["image_id"]
    evidence["image_id"] = image_id
    evidence["source_digest"] = binding["source_digest"]
    evidence["base_digest_label"] = binding["base_digest_label"]
    evidence["model_revision_label"] = binding["model_revision_label"]
    api_binding = verify_image_source_binding(API_IMAGE, "api")
    evidence["api_image_id"] = api_binding["image_id"]
    evidence["api_source_digest"] = api_binding["source_digest"]

    env = {**os.environ, "INSTADESCRIBE_WORKER_IMAGE": IMAGE, "INSTADESCRIBE_API_IMAGE": API_IMAGE}
    prepare_stack(env)
    with tempfile.TemporaryDirectory(prefix="instadescribe-g8-mem-") as tmp:
        try:
            video, duration = generate_five_minute_video(Path(tmp))

            start_base_stack(env, api_binding["image_id"])
            engine = db_engine()
            sqs = aws("sqs")

            _, job_id = create_job_and_upload("g8-memtest-299s", video, duration)
            evidence["job_id"] = job_id
            if queue_attrs(sqs, "instascribe-work") != ("1", "0", "0"):
                die("expected exactly one visible message before the worker starts")

            worker_ct = start_worker_stack(env, api_binding["image_id"], image_id)
            mem = cgroup_memory(worker_ct)
            if mem["limit_bytes"] != 8 * 1024**3:
                die(f"container memory limit {mem['limit_bytes']} != 8 GiB")

            # G8.1 D1: the EFFECTIVE CPU constraint must be exactly two vCPUs
            # — verified from Docker HostConfig AND cgroup cpu.max, with raw
            # and normalized values recorded.
            host_cfg = json.loads(run(["docker", "inspect", worker_ct]))[0]["HostConfig"]
            nano = host_cfg.get("NanoCpus", 0)
            cpu_max_raw = run(
                ["docker", "exec", worker_ct, "cat", "/sys/fs/cgroup/cpu.max"]
            ).strip()
            quota, period = cpu_max_raw.split()
            cpu_normalized = (int(quota) / int(period)) if quota != "max" else None
            if nano != 2_000_000_000:
                die(f"HostConfig.NanoCpus {nano} != 2 vCPU (2e9)")
            if cpu_normalized != 2.0:
                die(f"cgroup cpu.max {cpu_max_raw!r} normalizes to {cpu_normalized}, not 2.0")
            evidence["cpu_nano_cpus"] = nano
            evidence["cgroup_cpu_max_raw"] = cpu_max_raw
            evidence["cpu_normalized_vcpus"] = cpu_normalized

            transitions, wall, stats_peak_mib = await_terminal(
                engine, job_id, worker_ct, deadline_secs=2400
            )
            mem = cgroup_memory(worker_ct)
            state = container_state(worker_ct)

            oom = int(mem["events"].get("oom", "0"))
            oom_kill = int(mem["events"].get("oom_kill", "0"))
            if oom or oom_kill or state["oom_killed"]:
                die(
                    f"OOM evidence: events oom={oom} oom_kill={oom_kill} "
                    f"OOMKilled={state['oom_killed']}"
                )
            if state["restart_count"]:
                die(f"worker restarted {state['restart_count']} times")

            row = db_row(engine, job_id)
            if row["status"] != "READY_FOR_REVIEW" or row["progress"] != 100:
                die(f"non-Ready terminal state: {row['status']}/{row['progress']}")
            arts = {a["artifact_type"] for a in artifact_rows(engine, job_id)}
            # Optional posters degrade rather than fail (ADR-0008) — they may
            # be present, but nothing else beyond the required set may be.
            if not REQUIRED_TYPES <= arts or arts - REQUIRED_TYPES - OPTIONAL_TYPES:
                die(f"artifact set mismatch: {sorted(arts)}")
            if abs(float(row["duration_secs"]) - duration) > 2.0:
                die(f"DB duration {row['duration_secs']} disagrees with ffprobe {duration}")

            work = queue_attrs(sqs, "instascribe-work")
            dlq = queue_attrs(sqs, "instascribe-work-dlq")
            if work != ("0", "0", "0") or dlq != ("0", "0", "0"):
                die(
                    f"queues not empty after success (visible/in-flight/delayed): "
                    f"work={work} dlq={dlq}"
                )

            if wall > 900:
                die(
                    "processing exceeded 15 minutes: R7 must be reopened and the "
                    "visibility-timeout posture resolved (owner decision: raise "
                    "visibility to 60 min and record the limitation) before AWS"
                )

            stop_worker_stack(env)

            peak = mem["peak_bytes"]
            limit = mem["limit_bytes"]
            evidence.update(
                {
                    "status_transitions": transitions,
                    "wall_secs_worker_start_to_ready": round(wall, 1),
                    "db_processing_secs": round(
                        (row["completed_at"] - row["started_at"]).total_seconds(), 1
                    ),
                    "cgroup_memory_peak_bytes": peak,
                    "cgroup_memory_peak_gib": round(peak / 1024**3, 2),
                    "cgroup_memory_limit_bytes": limit,
                    "headroom_pct": round(100.0 * (limit - peak) / limit, 1),
                    "docker_stats_peak_mib": round(stats_peak_mib, 1),
                    "memory_events": mem["events"],
                    "oom_killed": state["oom_killed"],
                    "restart_count": state["restart_count"],
                    "artifact_types": sorted(arts),
                    "work_queue": work,
                    "dlq": dlq,
                }
            )
        finally:
            preserve_primary_cleanup(
                lambda: teardown_stack(env),
                sys.exc_info()[1],
            )

    print("\n=== G8 FIVE-MINUTE MEMORY TEST PASSED ===")
    print(json.dumps(evidence, indent=2, default=str))


if __name__ == "__main__":
    main()
