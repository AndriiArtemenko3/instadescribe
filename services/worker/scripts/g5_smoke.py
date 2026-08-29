#!/usr/bin/env python3
"""G5 production-image vertical slice (FABLE5 G5 Part C).

One protected job travels the REAL local cloud path end to end using the
PRODUCTION worker image: browser-style presigned upload of the rights-cleared
Sintel fixture (© copyright Blender Foundation | durian.blender.org, CC BY
3.0), strict SQS message, atomic claim, ffprobe gate, the unchanged pipeline
under the fake vision provider with offline Whisper/VAD, deterministic S3
artifacts, one success transaction and delete-after-commit. Fake vision
output — but real S3/SQS/PostgreSQL/ffmpeg/VAD/ASR/pipeline orchestration.

Run via `make g5-smoke` (host venv: services/api/.venv). Exits nonzero on the
first failed step; prints a JSON evidence block on success.
"""

import hashlib
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import boto3
import httpx
import sqlalchemy as sa

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "packages" / "contracts"))

from instadescribe_contracts.queue import QueueMessage  # noqa: E402

API = "http://localhost:8000"
TOKEN = {"X-Portfolio-Token": "local-dev-token"}  # local placeholder only
APP_DB = "postgresql+psycopg://instascribe:local-dev-only@127.0.0.1:5432/instascribe"
S3_ENDPOINT = "http://localhost:4566"
FIXTURE = REPO / "App" / "public" / "videos" / "sintel-blender-cc.mp4"
IMAGE = "instadescribe-worker:g5"
REQUIRED_TYPES = {
    "scenes_json",
    "entities_json",
    "audio_events_json",
    "ad_placement_gaps_json",
    "transcript_json",
    "source_video",
}

evidence: dict = {"steps": []}


def step(name: str) -> None:
    print(f"\n=== {name}", flush=True)
    evidence["steps"].append(name)


def die(msg: str) -> None:
    print(f"SMOKE FAILED: {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


def run(cmd: list[str], timeout: int = 900) -> str:
    print(f"$ {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        die(f"{cmd[0]} failed rc={proc.returncode}: {proc.stderr[-2000:]}")
    return proc.stdout


def aws(service):
    return boto3.client(
        service,
        region_name="eu-west-2",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )


def db_row(engine, job_id: str) -> dict:
    with engine.connect() as conn:
        row = (
            conn.execute(
                sa.text(
                    "SELECT status, progress, stage, attempt_count, worker_id, error_code, "
                    "input_object_key, source_etag, source_version_id, duration_secs, "
                    "enqueue_message_id, enqueue_requested_at, "
                    "started_at, completed_at FROM jobs WHERE id = :id"
                ),
                {"id": job_id},
            )
            .mappings()
            .one()
        )
    return dict(row)


def artifact_rows(engine, job_id: str) -> list[dict]:
    with engine.connect() as conn:
        rows = (
            conn.execute(
                sa.text(
                    "SELECT artifact_type, object_key, content_type, size_bytes, "
                    "checksum_sha256, created_at FROM artifacts WHERE job_id = :id "
                    "ORDER BY artifact_type"
                ),
                {"id": job_id},
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


def main() -> None:
    t0 = time.monotonic()

    step("1. clean local stack: PostgreSQL + LocalStack + API at migration head")
    # The worker profile must be INCLUDED in the teardown: `down` ignores
    # profile-scoped services, and a surviving worker (bounded-backoff loop)
    # would consume this run's message mid-smoke.
    run(["docker", "compose", "--profile", "worker", "down", "-v", "--remove-orphans"])
    run(
        [
            "docker",
            "compose",
            "up",
            "--build",
            "-d",
            "--wait",
            "postgres",
            "localstack",
            "migrate",
            "api",
        ],
        timeout=1200,
    )
    engine = sa.create_engine(APP_DB)
    sqs = aws("sqs")
    s3 = aws("s3")
    queue_url = sqs.get_queue_url(QueueName="instascribe-work")["QueueUrl"]

    step("2. protected create + browser-style presigned upload of the Sintel fixture")
    probe = json.loads(
        subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(FIXTURE),
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    duration = float(probe["format"]["duration"])
    body = FIXTURE.read_bytes()
    fixture_sha = hashlib.sha256(body).hexdigest()
    with httpx.Client(timeout=60) as client:
        unauth = client.post(f"{API}/api/v1/jobs", json={})
        if unauth.status_code not in (401, 403):
            die(f"unauthenticated create not rejected: {unauth.status_code}")
        created = client.post(
            f"{API}/api/v1/jobs",
            headers=TOKEN,
            json={
                "name": "g5-smoke-sintel",
                "durationSecs": duration,
                "fileName": FIXTURE.name,
                "contentType": "video/mp4",
                "fileSizeBytes": len(body),
                "settings": {"audioExtraction": True},
            },
        )
        if created.status_code != 201:
            die(f"create failed: {created.status_code} {created.text[:500]}")
        payload = created.json()
        job_id = payload["jobId"]
        upload = payload["upload"]
        posted = client.post(
            upload["url"],
            data=upload["fields"],
            files={"file": (FIXTURE.name, body, "video/mp4")},
        )
        if posted.status_code not in (201, 204):
            die(f"presigned POST failed: {posted.status_code} {posted.text[:500]}")

        step("3. upload-complete -> exactly one strict message on the work queue")
        done = client.post(f"{API}/api/v1/jobs/{job_id}/upload-complete", headers=TOKEN)
        if done.status_code != 202:
            die(f"upload-complete failed: {done.status_code} {done.text[:500]}")
    attrs = sqs.get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
    )["Attributes"]
    if (attrs["ApproximateNumberOfMessages"], attrs["ApproximateNumberOfMessagesNotVisible"]) != (
        "1",
        "0",
    ):
        die(f"expected exactly one visible message, got {attrs}")
    job = db_row(engine, job_id)
    if job["status"] not in ("QUEUED", "UPLOAD_COMPLETE"):
        die(f"unexpected post-enqueue status {job['status']}")
    evidence["enqueued_status"] = job["status"]

    step("4. production worker up (linux/amd64, 2 vCPU, 8 GiB, concurrency one, fake, offline)")
    run(["docker", "compose", "--profile", "worker", "up", "-d", "--no-build", "--wait", "worker"])
    worker_ct = run(["docker", "compose", "--profile", "worker", "ps", "-q", "worker"]).strip()
    worker_started = time.monotonic()

    step("5. observe QUEUED/UPLOAD_COMPLETE -> PROCESSING -> READY_FOR_REVIEW")
    seen: list[str] = [job["status"]]
    mem_peak_stats = 0.0
    deadline = time.monotonic() + 1200
    while True:
        row = db_row(engine, job_id)
        if row["status"] != seen[-1]:
            seen.append(row["status"])
            print(f"  status -> {row['status']} (t+{time.monotonic() - worker_started:.0f}s)")
        stats = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", worker_ct],
            capture_output=True,
            text=True,
        ).stdout.strip()
        if stats:
            raw = stats.split("/")[0].strip()
            for unit, mult in (("GiB", 1024.0), ("MiB", 1.0), ("KiB", 1 / 1024)):
                if raw.endswith(unit):
                    mem_peak_stats = max(mem_peak_stats, float(raw[: -len(unit)]) * mult)
                    break
        if row["status"] == "READY_FOR_REVIEW":
            break
        if row["status"] == "FAILED":
            die(f"job failed: {row['error_code']}")
        if time.monotonic() > deadline:
            die(f"timed out awaiting READY_FOR_REVIEW; last={row['status']} seen={seen}")
        time.sleep(2)
    wall = time.monotonic() - worker_started
    if "PROCESSING" not in seen:
        die(f"never observed PROCESSING: {seen}")
    cgroup_peak = subprocess.run(
        ["docker", "exec", worker_ct, "cat", "/sys/fs/cgroup/memory.peak"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    evidence["status_transitions"] = seen
    evidence["ready_wall_secs_from_worker_start"] = round(wall, 1)
    evidence["worker_mem_peak_stats_mib"] = round(mem_peak_stats, 1)
    evidence["worker_cgroup_memory_peak_bytes"] = cgroup_peak

    step("6. artifacts: private S3 objects, exact rows/checksums, source identity, attempts")
    row = db_row(engine, job_id)
    if not (row["progress"] == 100 and row["stage"] == "complete"):
        die(f"bad terminal progress/stage: {row['progress']}/{row['stage']}")
    if row["attempt_count"] != 1:
        die(f"attempt_count={row['attempt_count']}, expected exactly 1")
    try:
        # G5.1 A1: the fencing value is a per-claim UUID token, not the label.
        __import__("uuid").UUID(row["worker_id"])
    except Exception:
        die(f"worker_id is not a claim token: {row['worker_id']}")
    if not row["source_version_id"]:
        die("job has no pinned source VersionId")
    measured = float(row["duration_secs"])
    if not 100 < measured < 130:
        die(f"measured duration {measured} is not the fixture's actual length")
    started_at, completed_at = row["started_at"], row["completed_at"]
    evidence["processing_secs_started_to_completed"] = round(
        (completed_at - started_at).total_seconds(), 1
    )
    arts = {a["artifact_type"]: a for a in artifact_rows(engine, job_id)}
    missing = REQUIRED_TYPES - set(arts)
    if missing:
        die(f"missing artifact rows: {missing}")
    for atype, art in arts.items():
        if atype == "source_video":
            if art["object_key"] != row["input_object_key"]:
                die("source_video object_key != job.input_object_key")
            if art["checksum_sha256"] != fixture_sha:
                die("source_video checksum != local fixture sha256")
            continue
        # G5.1 D2: generated objects are attempt-scoped; rows pick the winner.
        if not art["object_key"].startswith(f"jobs/{job_id}/attempts/1/"):
            die(f"generated key is not attempt-scoped: {art['object_key']}")
        obj = s3.get_object(Bucket="instascribe-media", Key=art["object_key"])
        blob = obj["Body"].read()
        if hashlib.sha256(blob).hexdigest() != art["checksum_sha256"]:
            die(f"checksum mismatch for {atype}")
        if len(blob) != art["size_bytes"]:
            die(f"size mismatch for {atype}")
        if obj.get("ServerSideEncryption") != "AES256":
            die(f"missing SSE for {atype}")
    evidence["artifact_rows"] = {
        t: {"key": a["object_key"], "sha256": a["checksum_sha256"], "bytes": a["size_bytes"]}
        for t, a in sorted(arts.items())
    }

    step("7. work queue drained — message deleted only after the success commit")
    attrs = sqs.get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
    )["Attributes"]
    if attrs["ApproximateNumberOfMessages"] != "0" or (
        attrs["ApproximateNumberOfMessagesNotVisible"] != "0"
    ):
        die(f"queue not drained after success: {attrs}")

    step("7b. versioned source: an overwrite cannot change the analyzed bytes")
    pinned_version = row["source_version_id"]
    s3.put_object(
        Bucket="instascribe-media",
        Key=row["input_object_key"],
        Body=b"G51-DIFFERENT-BYTES " * 64,
        ContentType="video/mp4",
        ServerSideEncryption="AES256",
    )
    latest = s3.get_object(Bucket="instascribe-media", Key=row["input_object_key"])
    latest_bytes = latest["Body"].read()
    if latest_bytes == body:
        die("overwrite did not change the latest object")
    pinned = s3.get_object(
        Bucket="instascribe-media", Key=row["input_object_key"], VersionId=pinned_version
    )
    pinned_bytes = pinned["Body"].read()
    if pinned_bytes != body:
        die("pinned VersionId no longer serves the analyzed source bytes")
    evidence["source_version_proof"] = {
        "version_id": pinned_version,
        "latest_differs": True,
        "pinned_byte_equal": True,
    }

    step("8. API restart — PostgreSQL job/artifact state survives")
    run(["docker", "compose", "restart", "api"])
    for _ in range(30):
        try:
            if httpx.get(f"{API}/healthz", timeout=2).status_code == 200:
                break
        except Exception:
            pass
        time.sleep(2)
    else:
        die("API did not come back after restart")
    after = httpx.get(f"{API}/api/v1/jobs/{job_id}", headers=TOKEN, timeout=10)
    if after.status_code != 200:
        die(f"job lookup after restart: {after.status_code}")
    if db_row(engine, job_id)["status"] != "READY_FOR_REVIEW":
        die("job state lost across API restart")
    if len(artifact_rows(engine, job_id)) != len(arts):
        die("artifact rows lost across API restart")

    step("9. production image inspection: forbidden assets absent + measurements")
    inspect = json.loads(run(["docker", "image", "inspect", IMAGE]))[0]
    evidence["image"] = {
        "tag": IMAGE,
        "architecture": inspect["Architecture"],
        "os": inspect["Os"],
        "size_bytes": inspect["Size"],
        "size_gb": round(inspect["Size"] / 1e9, 2),
    }
    if inspect["Architecture"] != "amd64":
        die(f"image architecture {inspect['Architecture']} != amd64")
    absent = run(
        [
            "docker",
            "run",
            "--rm",
            "--platform",
            "linux/amd64",
            "--entrypoint",
            "sh",
            IMAGE,
            "-c",
            "set -e; "
            "for p in /app/fixtures /app/g0_smoke.py /app/App /app/modular_pipeline/.env "
            "/app/modular_pipeline/jobs; do "
            '  if [ -e "$p" ]; then echo "present: $p"; exit 1; fi; done; '
            "if find /app \\( -name 'test_*' -o -name '*.mp4' -o -name '.env*' \\) -print "
            "| grep .; then exit 1; fi; "
            "echo forbidden-assets-absent",
        ]
    )
    if "forbidden-assets-absent" not in absent:
        die("image inspection failed")

    step("10. terminal idempotency: duplicate message acknowledged, no rerun")
    before = artifact_rows(engine, job_id)
    message = QueueMessage(
        schema_version=1,
        message_id=row["enqueue_message_id"],
        task_type="ANALYZE",
        job_id=job_id,
        requested_at=row["enqueue_requested_at"].astimezone(UTC),
    )
    sqs.send_message(QueueUrl=queue_url, MessageBody=message.to_body())
    dup_deadline = time.monotonic() + 180
    while True:
        attrs = sqs.get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=[
                "ApproximateNumberOfMessages",
                "ApproximateNumberOfMessagesNotVisible",
            ],
        )["Attributes"]
        if attrs["ApproximateNumberOfMessages"] == "0" and (
            attrs["ApproximateNumberOfMessagesNotVisible"] == "0"
        ):
            break
        if time.monotonic() > dup_deadline:
            die(f"duplicate message not acknowledged: {attrs}")
        time.sleep(2)
    final = db_row(engine, job_id)
    if final["status"] != "READY_FOR_REVIEW" or final["attempt_count"] != 1:
        die(f"duplicate mutated the terminal job: {final['status']}/{final['attempt_count']}")
    after_rows = artifact_rows(engine, job_id)
    if [(r["artifact_type"], r["checksum_sha256"], str(r["created_at"])) for r in before] != [
        (r["artifact_type"], r["checksum_sha256"], str(r["created_at"])) for r in after_rows
    ]:
        die("duplicate consumption rewrote artifact rows")
    logs = run(["docker", "compose", "--profile", "worker", "logs", "--no-color", "worker"])
    if "duplicate_success" not in logs:
        die("worker log lacks the duplicate_success acknowledgement")
    if logs.count('"event": "job_claimed"') != 1:
        die("pipeline reran: more than one claim in worker logs")

    run(["docker", "compose", "--profile", "worker", "stop", "worker"])
    evidence["total_smoke_secs"] = round(time.monotonic() - t0, 1)
    evidence["completed_at_utc"] = datetime.now(UTC).isoformat(timespec="seconds")
    print("\n=== G5 SMOKE PASSED ===")
    print(json.dumps(evidence, indent=2, default=str))


if __name__ == "__main__":
    main()
