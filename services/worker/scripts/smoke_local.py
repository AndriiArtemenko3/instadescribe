#!/usr/bin/env python3
"""G8 Part F — `make smoke-local`: the one-command local acceptance smoke.

Proves the complete local cloud vertical slice from a controlled, run-owned
state using the FRESHLY built production worker image:

 1. API liveness/readiness at migration head;
 2. protected create returns distinct project/job IDs;
 3. browser-style presigned upload reaches private S3;
 4. upload-complete produces the strict queue contract (exactly one message);
 5. worker observes QUEUED/UPLOAD_COMPLETE -> PROCESSING -> READY_FOR_REVIEW;
 6. required artifact rows/objects, checksums, content types, attempt prefix
    and source VersionId;
 7. manifest signs the row-resolved, version-pinned video plus every required
    JSON artifact;
 8. an exact manifest scene ID is PATCHed without mutating scenes.json;
 9. API restart preserves job, artifact and override state;
10. a fresh manifest/override fetch remains usable after the restart;
11. the queue message is deleted only after the durable success commit;
12. every run-created resource is accounted for and cleaned safely.

Operates ONLY on the explicitly named Compose project `instadescribe-g8-smoke`.
Its final `down -v` destroys THAT project's volume only (the development
project and its pgdata volume are untouched; the script refuses to start
while the dev stack is running). Uses the rights-cleared Sintel fixture
(© copyright Blender Foundation | durian.blender.org, CC BY 3.0); fake
vision output, but real S3/SQS/PostgreSQL/FFmpeg/VAD/ASR orchestration.
"""

import hashlib
import json
import os
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

from g8_accounting import reconcile_bucket, reconcile_database  # noqa: E402
from g8_common import (  # noqa: E402
    API,
    FIXTURE,
    TOKEN,
    artifact_rows,
    await_terminal,
    aws,
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
from g8_log_order import assert_ready_before_ack  # noqa: E402

PROJECT = "instadescribe-g8-smoke"
IMAGE = getenv_compat("INSTADESCRIBE_WORKER_IMAGE") or "instadescribe-worker:g8"
API_IMAGE = getenv_compat("INSTADESCRIBE_API_IMAGE") or "instadescribe-api:g8"
REQUIRED_JSON_TYPES = {
    "scenes_json",
    "entities_json",
    "audio_events_json",
    "ad_placement_gaps_json",
    "transcript_json",
    "system_info_json",
}

evidence: dict = {"project": PROJECT, "image_tag": IMAGE, "steps": []}


def step(name: str) -> None:
    print(f"\n=== {name}", flush=True)
    evidence["steps"].append(name)


def fetch_signed(url: str) -> bytes:
    res = httpx.get(url, timeout=60)
    if res.status_code != 200:
        die(f"signed fetch failed {res.status_code} for a manifest URL")
    return res.content


def reconcile_smoke_database(
    engine,
    *,
    project_id: str,
    job_id: str,
    validated_artifacts: dict[str, dict],
    override_scene_id: str,
) -> dict[str, int]:
    """Reconcile against the earlier independently validated snapshot.

    Never derive the expected set from a fresh database query: a late extra
    row or identity mutation must make accounting fail closed.
    """
    expected = {
        (artifact_type, artifact["object_key"])
        for artifact_type, artifact in validated_artifacts.items()
    }
    return reconcile_database(
        engine,
        project_id=project_id,
        job_id=job_id,
        expected_artifacts=expected,
        override_scene_id=override_scene_id,
    )


def main() -> None:
    t0 = time.monotonic()
    # G8.1 B1: stale-but-compatible tags fail before any database/queue work.
    binding = verify_image_source_binding(IMAGE, "worker")
    image_id = binding["image_id"]
    evidence["image_id"] = image_id
    evidence["source_digest"] = binding["source_digest"]
    api_binding = verify_image_source_binding(API_IMAGE, "api")
    evidence["api_image_id"] = api_binding["image_id"]
    evidence["api_source_digest"] = api_binding["source_digest"]
    env = {**os.environ, "INSTADESCRIBE_WORKER_IMAGE": IMAGE, "INSTADESCRIBE_API_IMAGE": API_IMAGE}

    preflight(PROJECT, env=env, g8_images=True)
    try:
        step("1. run-owned stack up; liveness + readiness at migration head")
        compose(
            PROJECT,
            "up",
            "--no-build",
            "-d",
            "--wait",
            "postgres",
            "localstack",
            "migrate",
            "api",
            env=env,
            g8_images=True,
        )
        api_ct = compose(PROJECT, "ps", "-q", "api", env=env, g8_images=True).strip()
        if container_state(api_ct)["image"] != api_binding["image_id"]:
            die("api container is not running the exact source-bound API image")
        with httpx.Client(timeout=10) as client:
            if client.get(f"{API}/healthz").status_code != 200:
                die("liveness failed")
            ready = client.get(f"{API}/api/readyz")
            if ready.status_code != 200:
                die(f"readiness not at migration head: {ready.status_code} {ready.text[:300]}")
        engine = db_engine()
        sqs, s3 = aws("sqs"), aws("s3")

        step("2-4. protected create (distinct IDs) -> presigned S3 POST -> strict enqueue")
        probe = run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(FIXTURE),
            ]
        ).strip()
        duration = float(probe)
        fixture_sha = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
        project_id, job_id = create_job_and_upload("g8-smoke-sintel", FIXTURE, duration)
        evidence["project_id_prefix"] = project_id[:8]
        evidence["job_id_prefix"] = job_id[:8]
        if queue_attrs(sqs, "instascribe-work") != ("1", "0", "0"):
            die("expected exactly one visible strict-contract message")
        row = db_row(engine, job_id)
        if row["status"] not in ("QUEUED", "UPLOAD_COMPLETE"):
            die(f"unexpected post-enqueue status {row['status']}")
        if not row["source_version_id"]:
            die("upload verification did not pin a source VersionId")

        step("5. worker observes QUEUED/UPLOAD_COMPLETE -> PROCESSING -> READY_FOR_REVIEW")
        compose(
            PROJECT,
            "--profile",
            "worker",
            "up",
            "-d",
            "--no-build",
            "--wait",
            "worker",
            env=env,
            g8_images=True,
        )
        worker_ct = compose(
            PROJECT, "--profile", "worker", "ps", "-q", "worker", env=env, g8_images=True
        ).strip()
        if container_state(worker_ct)["image"] != image_id:
            die("worker container is not running the freshly built image")
        transitions, wall, _ = await_terminal(engine, job_id, worker_ct, deadline_secs=1200)
        if "PROCESSING" not in transitions:
            die(f"never observed PROCESSING: {transitions}")
        evidence["status_transitions"] = transitions
        evidence["wall_secs_to_ready"] = round(wall, 1)

        step("6. artifact rows/objects: checksums, content types, attempt prefix, VersionId")
        row = db_row(engine, job_id)
        arts = {a["artifact_type"]: a for a in artifact_rows(engine, job_id)}
        # Optional posters degrade rather than fail (ADR-0008).
        required = REQUIRED_JSON_TYPES | {"source_video"}
        if not required <= set(arts) or set(arts) - required - {"poster_jpg", "poster_avif"}:
            die(f"artifact set mismatch: {sorted(arts)}")
        for atype, art in arts.items():
            if atype == "source_video":
                if art["object_key"] != row["input_object_key"]:
                    die("source_video key != job.input_object_key")
                if art["checksum_sha256"] != fixture_sha:
                    die("source_video checksum != fixture sha256")
                continue
            if not art["object_key"].startswith(f"jobs/{job_id}/attempts/1/"):
                die(f"generated key not attempt-scoped: {art['object_key']}")
            if atype in REQUIRED_JSON_TYPES and art["content_type"] != "application/json":
                die(f"unexpected content type for {atype}")
            obj = s3.get_object(Bucket="instascribe-media", Key=art["object_key"])
            blob = obj["Body"].read()
            if hashlib.sha256(blob).hexdigest() != art["checksum_sha256"]:
                die(f"checksum mismatch for {atype}")
        evidence["source_version_id_present"] = True

        step("7. manifest signs the version-pinned video + every required JSON artifact")
        with httpx.Client(timeout=30) as client:
            man = client.get(f"{API}/api/v1/jobs/{job_id}/manifest", headers=TOKEN)
        if man.status_code != 200:
            die(f"manifest failed: {man.status_code} {man.text[:300]}")
        manifest = man.json()
        if manifest["jobId"] != job_id or manifest["projectId"] != project_id:
            die("manifest identity mismatch")
        source_bytes = fetch_signed(manifest["artifacts"]["video"]["url"])
        if hashlib.sha256(source_bytes).hexdigest() != fixture_sha:
            die("manifest video URL does not serve the pinned analyzed bytes")
        scenes_payload = None
        system_info = None
        for key in (
            "scenes",
            "entities",
            "audioEvents",
            "placementGaps",
            "transcript",
            "systemInfo",
        ):
            blob = fetch_signed(manifest["artifacts"][key]["url"])
            if key == "scenes":
                scenes_payload = json.loads(blob)
            elif key == "systemInfo":
                system_info = json.loads(blob)
        if (
            not isinstance(system_info, dict)
            or system_info.get("video_id") != job_id
            or system_info.get("processing", {}).get("provider") != "fake"
            or system_info.get("processing", {}).get("model") != "gpt-4.1"
        ):
            die("system-info provenance mismatch")
        tokens = system_info.get("tokens")
        if (
            not isinstance(tokens, dict)
            or any(
                isinstance(tokens.get(name), bool)
                or not isinstance(tokens.get(name), int)
                or tokens[name] < 0
                for name in ("input_tokens", "output_tokens", "total_tokens")
            )
            or tokens.get("total_tokens")
            != tokens.get("input_tokens", -1) + tokens.get("output_tokens", -1)
        ):
            die("system-info token totals disagree")
        evidence["manifest_artifacts_fetched"] = 7  # video + six JSON
        evidence["system_info_provider"] = "fake"

        step("8. exact scene PATCH persists an override without mutating scenes.json")
        scenes_row = arts["scenes_json"]
        target_scene = scenes_payload[2]["scene_id"]  # an exact generated ID
        with httpx.Client(timeout=30) as client:
            patched = client.patch(
                f"{API}/api/v1/jobs/{job_id}/scenes/{target_scene}",
                headers=TOKEN,
                json={"ad": "G8 smoke persistent edit.", "active": True},
            )
            if patched.status_code != 200:
                die(f"scene PATCH failed: {patched.status_code} {patched.text[:300]}")
            overrides = client.get(f"{API}/api/v1/jobs/{job_id}/overrides", headers=TOKEN)
        if overrides.json().get(target_scene, {}).get("ad") != "G8 smoke persistent edit.":
            die("override map does not contain the patched scene")
        regen = s3.get_object(Bucket="instascribe-media", Key=scenes_row["object_key"])
        if hashlib.sha256(regen["Body"].read()).hexdigest() != scenes_row["checksum_sha256"]:
            die("scenes.json object changed after the PATCH — generated data mutated")
        evidence["patched_scene_id"] = target_scene

        step("9-10. API restart preserves state; fresh manifest/override remain usable")
        compose(PROJECT, "restart", "api", env=env, g8_images=True)
        for _ in range(30):
            try:
                if httpx.get(f"{API}/healthz", timeout=2).status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(2)
        else:
            die("API did not return after restart")
        if db_row(engine, job_id)["status"] != "READY_FOR_REVIEW":
            die("job state lost across restart")
        if len(artifact_rows(engine, job_id)) != len(arts):
            die("artifact rows lost across restart")
        with httpx.Client(timeout=30) as client:
            man2 = client.get(f"{API}/api/v1/jobs/{job_id}/manifest", headers=TOKEN)
            if man2.status_code != 200:
                die("fresh manifest failed after restart")
            fresh = man2.json()
            fetch_signed(fresh["artifacts"]["scenes"]["url"])
            ov2 = client.get(f"{API}/api/v1/jobs/{job_id}/overrides", headers=TOKEN)
        if ov2.json().get(target_scene, {}).get("ad") != "G8 smoke persistent edit.":
            die("override lost across restart")
        evidence["restart_preserved"] = True

        step("11. message deleted only after the durable success commit")
        work = queue_attrs(sqs, "instascribe-work")
        dlq = queue_attrs(sqs, "instascribe-work-dlq")
        if work != ("0", "0", "0") or dlq != ("0", "0", "0"):
            die(f"queues not drained (visible/in-flight/delayed): work={work} dlq={dlq}")
        logs = compose(
            PROJECT,
            "--profile",
            "worker",
            "logs",
            "--no-color",
            "worker",
            env=env,
            g8_images=True,
        )
        # G8.1 D3: STRUCTURED parsing — exactly one job_ready and exactly one
        # LATER message_success for the same job and attempt; any
        # success_ack_pending rejects; the drained queue corroborates.
        try:
            order = assert_ready_before_ack(logs, job_id)
        except ValueError as exc:
            die(f"delete-order proof failed: {exc}")
        if logs.count('"event": "job_claimed"') != 1:
            die("more than one claim observed")
        evidence["delete_after_commit"] = order

        step("12. resource accounting — QUERIED identities, never constants")
        try:
            db_counts = reconcile_smoke_database(
                engine,
                project_id=project_id,
                job_id=job_id,
                validated_artifacts=arts,
                override_scene_id=target_scene,
            )
        except ValueError as exc:
            die(f"database accounting failed: {exc}")

        expected_keys = {row["input_object_key"]} | {
            a["object_key"]
            for a in artifact_rows(engine, job_id)
            if a["artifact_type"] != "source_video"
        }
        try:
            bucket_account = reconcile_bucket(s3, "instascribe-media", expected_keys)
        except ValueError as exc:
            die(f"bucket accounting failed: {exc}")
        evidence["run_created"] = {
            "db_counts": db_counts,
            "s3": bucket_account,
            "compose_project": PROJECT,
        }
        compose(PROJECT, "--profile", "worker", "stop", "worker", env=env, g8_images=True)
    finally:
        preserve_primary_cleanup(
            lambda: cleanup_compose_project(PROJECT, env=env, g8_images=True),
            sys.exc_info()[1],
        )

    evidence["total_secs"] = round(time.monotonic() - t0, 1)
    print("\n=== G8 SMOKE-LOCAL PASSED ===")
    print(json.dumps(evidence, indent=2, default=str))


if __name__ == "__main__":
    main()
