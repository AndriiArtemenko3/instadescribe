"""Shared plumbing for the G8 acceptance scripts (memory test + smoke-local).

Both scripts operate ONLY on an explicitly named Compose project so their
`down -v` can never touch the development project's volume, and both refuse
to start while the development stack (or anything else) holds the loopback
ports. All resources they create live inside that named project (containers,
network, pgdata volume) plus one temporary directory, and are destroyed by
tearing the project down.

G8.1 hardening: a host-global per-gate OS lock (stable across worktrees,
keyed by Docker endpoint + Compose project — a second invocation fails
before it can touch the first run's resources); Docker discovery by exact
`com.docker.compose.project` labels including profiled/orphan containers;
CHECKED post-teardown queries that fail closed on daemon errors and assert
zero containers/networks/volumes; three-counter queue checks (visible,
in-flight AND delayed); and image/source binding — every runtime gate
recomputes the production-input digest and compares it with the image label
before any database/queue work.
"""

import fcntl
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import boto3
import httpx
import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).resolve().parent))
from g8_source_digest import production_source_digest  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "packages" / "contracts"))
from instadescribe_contracts.environment import getenv_compat as _getenv_compat  # noqa: E402


def getenv_compat(name: str) -> str | None:
    """Expose the shared canonical/legacy policy to sibling gate scripts."""

    return _getenv_compat(name)


API = "http://localhost:8000"
S3_ENDPOINT = "http://localhost:4566"
TOKEN = {"X-Portfolio-Token": "local-dev-token"}  # local placeholder only
APP_DB = "postgresql+psycopg://instascribe:local-dev-only@127.0.0.1:5432/instascribe"
FIXTURE = REPO / "App" / "public" / "videos" / "sintel-blender-cc.mp4"
DEV_PROJECT = "instascribe-cloud-core"


def die(msg: str) -> None:
    print(f"FAILED: {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


def run(cmd: list[str], timeout: int = 1800, env: dict | None = None) -> str:
    print(f"$ {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=timeout, env=env)
    if proc.returncode != 0:
        die(f"{cmd[0]} failed rc={proc.returncode}: {proc.stderr[-2000:]}")
    return proc.stdout


G8_COMPOSE_FILES = ["-f", "docker-compose.yml", "-f", "docker-compose.g8.yml"]


def compose(
    project: str,
    *args: str,
    env: dict | None = None,
    timeout: int = 1800,
    g8_images: bool = False,
) -> str:
    """`g8_images=True` applies the G8 override so api/migrate run the EXACT
    source-bound API image (INSTADESCRIBE_API_IMAGE) instead of an implicit
    local rebuild; plain compose behavior is untouched otherwise."""
    files = G8_COMPOSE_FILES if g8_images else []
    return run(["docker", "compose", "-p", project, *files, *args], timeout=timeout, env=env)


def checked_query(cmd: list[str], runner=None) -> str:
    """A Docker query whose FAILURE is a failure — never silently empty.
    Returns stripped stdout; raises CleanupError on nonzero rc or a dead
    daemon (fail closed, category only — no raw endpoint/credential text)."""
    runner = runner or (
        lambda c: subprocess.run(c, cwd=REPO, capture_output=True, text=True, timeout=120)
    )
    try:
        proc = runner(cmd)
    except Exception as exc:
        raise CleanupError(f"docker-query-error:{type(exc).__name__}") from None
    if proc.returncode != 0:
        raise CleanupError("docker-query-nonzero")
    return proc.stdout.strip()


class CleanupError(RuntimeError):
    """Cleanup/verification failure by safe category (no raw output)."""


class GateLockError(RuntimeError):
    """Another invocation of the same gate already holds the lock."""


_LOCK_FDS: dict[str, int] = {}  # held for the WHOLE run, incl. teardown


def cleanup_command(cmd: list[str], category: str, runner=None, timeout: int = 300) -> str:
    """Run cleanup without `die()` and expose only a fixed safe category."""
    runner = runner or (
        lambda command: subprocess.run(
            command, cwd=REPO, capture_output=True, text=True, timeout=timeout
        )
    )
    try:
        proc = runner(cmd)
    except BaseException as exc:
        raise CleanupError(f"{category}:exception:{type(exc).__name__}") from None
    if proc.returncode != 0:
        raise CleanupError(f"{category}:nonzero")
    return proc.stdout.strip()


def preserve_primary_cleanup(cleanup, primary: BaseException | None, reporter=None) -> None:
    """Run every cleanup after a body and never replace its primary failure.

    Call from a `finally` block with `sys.exc_info()[1]`.  Cleanup failures on
    a successful body fail the gate; cleanup failures while another exception
    is active are reported as a sanitized secondary category and the caller's
    original exception remains authoritative.
    """
    reporter = reporter or (lambda msg: print(msg, file=sys.stderr, flush=True))
    try:
        cleanup()
    except BaseException as exc:
        safe = (
            exc
            if isinstance(exc, CleanupError)
            else CleanupError(f"cleanup-error:{type(exc).__name__}")
        )
        if primary is not None:
            reporter(f"secondary cleanup failure: {safe}")
            return
        raise safe from None


def cleanup_compose_project(project: str, env: dict | None = None, g8_images: bool = True) -> None:
    """Attempt teardown and all three residue queries, aggregating safe errors."""
    files = G8_COMPOSE_FILES if g8_images else []
    failures: list[str] = []
    try:
        cleanup_command(
            [
                "docker",
                "compose",
                "-p",
                project,
                *files,
                "--profile",
                "worker",
                "down",
                "-v",
                "--remove-orphans",
            ],
            "compose-down",
            runner=(
                None
                if env is None
                else lambda command: subprocess.run(
                    command,
                    cwd=REPO,
                    capture_output=True,
                    text=True,
                    timeout=300,
                    env=env,
                )
            ),
        )
    except CleanupError as exc:
        failures.append(str(exc))
    try:
        assert_no_residue(project)
    except CleanupError as exc:
        failures.append(str(exc))
    if failures:
        raise CleanupError("cleanup-failed:" + ",".join(failures))


def gate_lock_path(project: str, lock_dir: Path | None = None) -> Path:
    """Stable across worktrees/checkouts: keyed by the active Docker
    endpoint/context plus the exact Compose project name, under the system
    temp dir — never under a worktree."""
    endpoint = os.environ.get("DOCKER_HOST", "")
    if not endpoint:
        try:
            endpoint = subprocess.run(
                ["docker", "context", "show"], capture_output=True, text=True, timeout=30
            ).stdout.strip()
        except Exception:
            endpoint = "default"
    key = hashlib.sha256(f"{endpoint}\0{project}".encode()).hexdigest()[:24]
    base = lock_dir or Path(tempfile.gettempdir())
    return base / f"instadescribe-gate-{key}.lock"


def acquire_gate_lock(project: str, lock_dir: Path | None = None) -> int:
    """Non-blocking host-global atomic lock for the named gate. The fd is
    retained for the complete run (including teardown); a second invocation
    raises GateLockError BEFORE it can stop or delete anything."""
    path = gate_lock_path(project, lock_dir)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise GateLockError(f"another invocation of gate {project!r} holds the lock") from None
    os.ftruncate(fd, 0)
    os.write(fd, f"pid={os.getpid()}\n".encode())
    _LOCK_FDS[str(path)] = fd  # deliberately never closed until process exit
    return fd


def project_containers(project: str, runner=None) -> list[str]:
    """ALL containers (running or not, any profile, orphans included) that
    carry the exact Compose project label — never another project's."""
    out = checked_query(
        ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"],
        runner,
    )
    return [line for line in out.splitlines() if line]


def assert_no_residue(project: str, runner=None) -> None:
    """Post-`down -v` verification with CHECKED queries: zero containers,
    networks and volumes carrying the exact project label; any query error
    fails closed."""
    containers = project_containers(project, runner)
    if containers:
        raise CleanupError(f"container-residue:{len(containers)}")
    networks = checked_query(
        [
            "docker",
            "network",
            "ls",
            "-q",
            "--filter",
            f"label=com.docker.compose.project={project}",
        ],
        runner,
    )
    if networks:
        raise CleanupError(f"network-residue:{len(networks.splitlines())}")
    volumes = checked_query(
        ["docker", "volume", "ls", "-q", "--filter", f"label=com.docker.compose.project={project}"],
        runner,
    )
    if volumes:
        raise CleanupError(f"volume-residue:{len(volumes.splitlines())}")


def verify_image_source_binding(image: str, service: str) -> dict:
    """G8.1 B1: recompute the production-input digest from the working tree
    and compare it with the image's label — a stale but compatible tag
    fails BEFORE any database/queue work. Also returns the label-exposed
    base digest / model revision for evidence."""
    current = production_source_digest(REPO, service)
    inspect = json.loads(run(["docker", "image", "inspect", image]))[0]
    labels = inspect["Config"].get("Labels") or {}
    labeled = labels.get("io.instadescribe.source-digest", "")
    if labeled != current:
        die(
            f"image {image} is stale: label source-digest "
            f"{labeled[:16] or '(missing)'}… != current {current[:16]}… — rebuild first"
        )
    return {
        "image_id": inspect["Id"],
        "source_digest": current,
        "base_digest_label": labels.get("io.instadescribe.base-digest", ""),
        "model_revision_label": labels.get("io.instadescribe.whisper-revision", ""),
    }


def preflight(project: str, *, env: dict | None = None, g8_images: bool = False) -> None:
    """Gate order (G8.1 C): acquire the host-global lock FIRST — before any
    cleanup or port check — then refuse to run while the dev project (found
    by its exact Compose label, profiles/orphans included) or any other
    listener holds the loopback ports. Never stop or drain resources this
    run does not own."""
    try:
        acquire_gate_lock(project)
    except GateLockError as exc:
        die(str(exc))
    try:
        dev = project_containers(DEV_PROJECT)
    except CleanupError as exc:
        die(f"cannot inspect Docker state ({exc}) — refusing to run blind")
    if dev:
        die(
            f"the development stack ({DEV_PROJECT}) is running; stop it first with "
            "`docker compose --profile worker down` (its volume is preserved)"
        )
    for port in (8000, 5432, 4566):
        probe = subprocess.run(["nc", "-z", "127.0.0.1", str(port)], capture_output=True)
        if probe.returncode == 0:
            die(f"port {port} is already in use by a process this run does not own")
    # A stale copy of OUR project from an aborted run is ours to clean —
    # discovered by ITS exact label, under OUR held lock.
    cleanup_compose_project(project, env=env, g8_images=g8_images)


def teardown_and_assert_clean(project: str) -> None:
    """`down -v` for OUR project, then CHECKED label-scoped queries that
    fail closed (CleanupError) on residue or any Docker query error."""
    cleanup_compose_project(project, g8_images=False)


def aws(service):
    return boto3.client(
        service,
        region_name="eu-west-2",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )


def queue_attrs(sqs, queue_name: str) -> tuple[str, str, str]:
    """Visible, in-flight AND delayed counts (G8.1 D2) — a delayed message
    is queue residue and must fail the gate."""
    url = sqs.get_queue_url(QueueName=queue_name)["QueueUrl"]
    attrs = sqs.get_queue_attributes(
        QueueUrl=url,
        AttributeNames=[
            "ApproximateNumberOfMessages",
            "ApproximateNumberOfMessagesNotVisible",
            "ApproximateNumberOfMessagesDelayed",
        ],
    )["Attributes"]
    return (
        attrs["ApproximateNumberOfMessages"],
        attrs["ApproximateNumberOfMessagesNotVisible"],
        attrs.get("ApproximateNumberOfMessagesDelayed", "0"),
    )


def db_engine():
    return sa.create_engine(APP_DB)


def db_row(engine, job_id: str) -> dict:
    with engine.connect() as conn:
        row = (
            conn.execute(
                sa.text(
                    "SELECT status, progress, stage, attempt_count, worker_id, error_code, "
                    "input_object_key, source_etag, source_version_id, duration_secs, "
                    "enqueue_message_id, enqueue_requested_at, started_at, completed_at "
                    "FROM jobs WHERE id = :id"
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


def create_job_and_upload(name: str, video: Path, duration: float) -> tuple[str, str]:
    """Protected create + browser-style presigned POST + upload-complete.
    Returns (project_id, job_id)."""
    body = video.read_bytes()
    with httpx.Client(timeout=120) as client:
        created = client.post(
            f"{API}/api/v1/jobs",
            headers=TOKEN,
            json={
                "name": name,
                "durationSecs": duration,
                "fileName": video.name,
                "contentType": "video/mp4",
                "fileSizeBytes": len(body),
                "settings": {"audioExtraction": True},
            },
        )
        if created.status_code != 201:
            die(f"create failed: {created.status_code} {created.text[:500]}")
        payload = created.json()
        if payload["projectId"] == payload["jobId"]:
            die("projectId and jobId are not distinct")
        upload = payload["upload"]
        posted = client.post(
            upload["url"],
            data=upload["fields"],
            files={"file": (video.name, body, "video/mp4")},
        )
        if posted.status_code not in (201, 204):
            die(f"presigned POST failed: {posted.status_code} {posted.text[:500]}")
        done = client.post(f"{API}/api/v1/jobs/{payload['jobId']}/upload-complete", headers=TOKEN)
        if done.status_code != 202:
            die(f"upload-complete failed: {done.status_code} {done.text[:500]}")
    return payload["projectId"], payload["jobId"]


def await_terminal(
    engine, job_id: str, worker_ct: str, deadline_secs: int
) -> tuple[list, float, float]:
    """Poll until READY_FOR_REVIEW/FAILED; returns (transitions, wall_secs,
    docker-stats memory peak in MiB)."""
    seen = [db_row(engine, job_id)["status"]]
    peak_mib = 0.0
    t0 = time.monotonic()
    deadline = t0 + deadline_secs
    while True:
        row = db_row(engine, job_id)
        if row["status"] != seen[-1]:
            seen.append(row["status"])
            print(f"  status -> {row['status']} (t+{time.monotonic() - t0:.0f}s)", flush=True)
        stats = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", worker_ct],
            capture_output=True,
            text=True,
        ).stdout.strip()
        if stats:
            raw = stats.split("/")[0].strip()
            for unit, mult in (("GiB", 1024.0), ("MiB", 1.0), ("KiB", 1 / 1024)):
                if raw.endswith(unit):
                    peak_mib = max(peak_mib, float(raw[: -len(unit)]) * mult)
                    break
        if row["status"] == "READY_FOR_REVIEW":
            return seen, time.monotonic() - t0, peak_mib
        if row["status"] == "FAILED":
            die(f"job failed: {row['error_code']}")
        if time.monotonic() > deadline:
            die(f"timed out awaiting READY_FOR_REVIEW; last={row['status']} seen={seen}")
        time.sleep(2)


def cgroup_memory(worker_ct: str) -> dict:
    peak = run(["docker", "exec", worker_ct, "cat", "/sys/fs/cgroup/memory.peak"]).strip()
    limit = run(["docker", "exec", worker_ct, "cat", "/sys/fs/cgroup/memory.max"]).strip()
    events = run(["docker", "exec", worker_ct, "cat", "/sys/fs/cgroup/memory.events"]).strip()
    parsed = dict(line.split() for line in events.splitlines())
    return {"peak_bytes": int(peak), "limit_bytes": int(limit), "events": parsed}


def container_state(worker_ct: str) -> dict:
    state = json.loads(run(["docker", "inspect", worker_ct]))[0]
    return {
        "oom_killed": state["State"].get("OOMKilled"),
        "restart_count": state.get("RestartCount", state["State"].get("RestartCount", 0)),
        "image": state["Image"],
    }
