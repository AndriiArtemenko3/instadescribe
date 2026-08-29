#!/usr/bin/env python3
"""G8.1 F — production API image provenance/content/smoke proof.

Against the fresh tag (INSTADESCRIBE_API_IMAGE, default instadescribe-api:g8):
linux/amd64; source-input digest label matches the CURRENT tree; base
digest label matches the Dockerfile pin; non-root UID 10001 and the
production uvicorn CMD; locked dependencies (`pip check`); migration tree +
packaged head resolvable in-image; shared contract imports; forbidden
assets absent. Then a LIVE smoke on a private Docker network (no host-port
collision with the dev stack): `alembic upgrade head` from the exact image
against a run-owned PostgreSQL, health 200, schema-aware readiness 200,
readiness degrading to a sanitized 503 while PostgreSQL is stopped, and
recovery to 200. No AWS, no ECR, no push.
"""

import json
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from g8_common import (  # noqa: E402
    CleanupError,
    acquire_gate_lock,
    die,
    getenv_compat,
    preserve_primary_cleanup,
    run,
)
from g8_owned_resources import cleanup_owned_resources, label_args  # noqa: E402
from g8_source_digest import production_source_digest  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
IMAGE = getenv_compat("INSTADESCRIBE_API_IMAGE") or "instadescribe-api:g8"
DOCKERFILE = REPO / "services" / "api" / "Dockerfile"
NET = "instadescribe-g8-apiproof"
PG_NAME = "instadescribe-g8-apiproof-pg"
API_NAME = "instadescribe-g8-apiproof-api"
UID_NAME = "instadescribe-g8-apiproof-uid"
CHECKS_NAME = "instadescribe-g8-apiproof-checks"
HEADS_NAME = "instadescribe-g8-apiproof-heads"
MIGRATE_NAME = "instadescribe-g8-apiproof-migrate"
PG_VOLUME = "instadescribe-g8-apiproof-pgdata"
API_PORT = "127.0.0.1:18000"
DB_URL = "postgresql+psycopg://instadescribe:local-dev-only@apiproof-pg:5432/instadescribe"  # gitleaks:allow

evidence: dict = {"image_tag": IMAGE}
OWNER = "instadescribe-g8-apiproof"
RUN_ID = uuid.uuid4().hex
PROOF_CONTAINERS = [API_NAME, PG_NAME, UID_NAME, CHECKS_NAME, HEADS_NAME, MIGRATE_NAME]


def owned_container_command(name: str, *args: str) -> list[str]:
    """Build the only permitted API-proof container creation command."""
    if name not in PROOF_CONTAINERS:
        raise ValueError("container is outside the API-proof inventory")
    return ["docker", "run", "--name", name, *label_args(OWNER, RUN_ID), *args]


def cleanup(current_run: bool = False) -> None:
    cleanup_owned_resources(
        owner=OWNER,
        containers=PROOF_CONTAINERS,
        network=NET,
        volume=PG_VOLUME,
        run_id=RUN_ID if current_run else None,
    )


def wait_http(url: str, want: int, deadline_secs: int = 60) -> httpx.Response:
    last = None
    deadline = time.monotonic() + deadline_secs
    while time.monotonic() < deadline:
        try:
            res = httpx.get(url, timeout=2)
            last = res
            if res.status_code == want:
                return res
        except Exception:
            last = None
        time.sleep(1)
    die(f"{url} never returned {want} (last: {last.status_code if last else 'no response'})")


def main() -> None:
    # Lock before the proof's first Docker lookup/run. Every subsequent
    # container, including one-shot assertions, is exact-named, labelled and
    # retained until the fail-closed inventory cleanup below.
    try:
        acquire_gate_lock(NET)
    except Exception as exc:
        die(str(exc))
    try:
        try:
            cleanup()  # stale remnants are removed only after ownership proof
        except CleanupError as exc:
            die(f"pre-clean failed: {exc}")

        labels = label_args(OWNER, RUN_ID)

        dockerfile = DOCKERFILE.read_text()
        pinned = re.search(r"python:3\.12-slim@(sha256:[0-9a-f]{64})", dockerfile)
        if not pinned:
            die("API Dockerfile base is not digest-pinned")
        evidence["dockerfile_base_digest"] = pinned.group(1)

        inspect = json.loads(run(["docker", "image", "inspect", IMAGE]))[0]
        if inspect["Architecture"] != "amd64" or inspect["Os"] != "linux":
            die(f"image is {inspect['Os']}/{inspect['Architecture']}, not linux/amd64")
        image_labels = inspect["Config"].get("Labels") or {}
        current_digest = production_source_digest(REPO, "api")
        if image_labels.get("io.instadescribe.source-digest") != current_digest:
            die(
                "stale API image: label source-digest "
                f"{(image_labels.get('io.instadescribe.source-digest') or '(missing)')[:16]}… != "
                f"current {current_digest[:16]}… — rebuild with make g8-api-build"
            )
        if image_labels.get("io.instadescribe.base-digest") != pinned.group(1):
            die("API image base-digest label disagrees with the Dockerfile pin")
        cmd = inspect["Config"].get("Cmd") or []
        if cmd[:2] != ["uvicorn", "app.main:app"]:
            die(f"CMD is not the production uvicorn entrypoint: {cmd}")
        evidence["image_id"] = inspect["Id"]
        evidence["created"] = inspect["Created"]
        evidence["source_digest"] = current_digest
        evidence["unpacked_size_bytes"] = inspect["Size"]
        evidence["cmd"] = cmd

        uid = run(
            owned_container_command(
                UID_NAME,
                "--platform",
                "linux/amd64",
                "--entrypoint",
                "sh",
                IMAGE,
                "-c",
                "id -u; id -un",
            )
        ).split()
        if uid[0] != "10001" or uid[1] != "api":
            die(f"runtime user is {uid}, expected uid 10001 'api'")
        evidence["runtime_uid"] = 10001

        checks = run(
            owned_container_command(
                CHECKS_NAME,
                "--platform",
                "linux/amd64",
                "--entrypoint",
                "sh",
                IMAGE,
                "-c",
                "pip check && "
                "ls /srv/migrations/versions | head -1 >/dev/null && "
                'python -c "import app.main, app.models, app.domain.states, '
                "instadescribe_contracts.queue; print('imports-ok')\" && "
                "if find /srv \\( -name 'test_*' -o -name '*.mp4' -o -name '.env*' "
                "-o -name '*HANDOFF*' -o -name '*.tfstate*' -o -iname '*.pem' "
                "-o -iname '*.key' -o -iname '*.p12' -o -iname '*.pfx' \\) -print | grep .; then "
                "exit 1; fi; echo proof-ok",
            ),
            timeout=300,
        )
        if "imports-ok" not in checks or "proof-ok" not in checks:
            die("in-image dependency/migration/forbidden-asset checks failed")
        heads = run(
            owned_container_command(
                HEADS_NAME,
                "--platform",
                "linux/amd64",
                "--entrypoint",
                "alembic",
                IMAGE,
                "-c",
                "/srv/alembic.ini",
                "heads",
            ),
            timeout=120,
        ).strip()
        if not heads:
            die("packaged migration head not resolvable in-image")
        evidence["packaged_migration_head"] = heads.splitlines()[-1]

        # ── Live smoke on a private network (no dev-stack port collision) ──
        run(["docker", "network", "create", *labels, NET])
        run(["docker", "volume", "create", *labels, PG_VOLUME])
        run(
            owned_container_command(
                PG_NAME,
                "-d",
                "--network",
                NET,
                "--network-alias",
                "apiproof-pg",
                "-e",
                "POSTGRES_USER=instadescribe",
                "-e",
                "POSTGRES_PASSWORD=local-dev-only",
                "-e",
                "POSTGRES_DB=instadescribe",
                "-v",
                f"{PG_VOLUME}:/var/lib/postgresql/data",
                "postgres:16.14",
            )
        )
        for _ in range(30):
            ready = subprocess.run(
                ["docker", "exec", PG_NAME, "pg_isready", "-U", "instadescribe"],
                capture_output=True,
                cwd=REPO,
            )
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            die("run-owned PostgreSQL never became ready")

        # Migration succeeds FROM THE EXACT IMAGE.
        run(
            owned_container_command(
                MIGRATE_NAME,
                "--network",
                NET,
                "--platform",
                "linux/amd64",
                "-e",
                f"DATABASE_URL={DB_URL}",
                "--entrypoint",
                "alembic",
                IMAGE,
                "-c",
                "/srv/alembic.ini",
                "upgrade",
                "head",
            ),
            timeout=300,
        )
        evidence["migration_from_image"] = "upgrade head ok"

        run(
            owned_container_command(
                API_NAME,
                "-d",
                "--network",
                NET,
                "--platform",
                "linux/amd64",
                "-p",
                f"{API_PORT}:8000",
                "-e",
                f"DATABASE_URL={DB_URL}",
                "-e",
                "PORTFOLIO_TOKEN_SHA256="
                "0cd48eed49739dfdd99efaef37d251aae5a80866e5954763c6597943fff5ce9b",
                "-e",
                "INSTADESCRIBE_PIPELINE_REVISION=dev",
                "-e",
                "INSTADESCRIBE_MEDIA_BUCKET=instascribe-media",
                "-e",
                "INSTADESCRIBE_S3_ENDPOINT_INTERNAL=http://apiproof-pg:1",
                "-e",
                "INSTADESCRIBE_S3_ENDPOINT_PUBLIC=http://localhost:1",
                "-e",
                "INSTADESCRIBE_SQS_ENDPOINT_INTERNAL=http://apiproof-pg:1",
                "-e",
                "AWS_DEFAULT_REGION=eu-west-2",
                "-e",
                "AWS_ACCESS_KEY_ID=test",
                "-e",
                "AWS_SECRET_ACCESS_KEY=test",
                IMAGE,
            )
        )
        wait_http("http://localhost:18000/healthz", 200)
        ready = wait_http("http://localhost:18000/api/readyz", 200)
        evidence["readiness_at_head"] = ready.json()

        # Readiness must degrade to a SANITIZED 503 without PostgreSQL...
        run(["docker", "stop", PG_NAME])
        degraded = wait_http("http://localhost:18000/api/readyz", 503, 60)
        body = degraded.text
        for leak in ("local-dev-only", "apiproof-pg", "postgresql+psycopg", "Traceback"):
            if leak in body:
                die(f"degraded readiness leaks internals ({leak!r})")
        evidence["readiness_degraded"] = degraded.json()

        # ...and RECOVER to 200 once PostgreSQL returns.
        run(["docker", "start", PG_NAME])
        wait_http("http://localhost:18000/api/readyz", 200, 90)
        evidence["readiness_recovered"] = True
    finally:
        preserve_primary_cleanup(lambda: cleanup(current_run=True), sys.exc_info()[1])

    print(json.dumps(evidence, indent=2))
    print("G8 API IMAGE PROOF PASSED", flush=True)


if __name__ == "__main__":
    main()
