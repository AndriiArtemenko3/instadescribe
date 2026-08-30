#!/usr/bin/env python3
"""G8 Part D — production worker image provenance/content proof.

Asserts, against the FRESH image tag (INSTADESCRIBE_WORKER_IMAGE, default
instadescribe-worker:g8): linux/amd64; pinned base digest and Whisper snapshot
revision baked and resolvable offline; HF_HUB_OFFLINE=1; the exercised
worker dependency behaviours (including TorchAudio and bundled Silero JIT);
non-root UID 10001; forbidden assets absent
(fixture, smoke script, tests, .env, media, job data, handoffs); the current
API model/domain copy imports from inside the image; exact image ID, created
time and unpacked/compressed sizes. Exits nonzero on the first violation and
prints a JSON evidence block on success.
"""

import json
import re
import subprocess
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dependency_runtime_smoke import parse_smoke_output  # noqa: E402
from g8_source_digest import production_source_digest  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "packages" / "contracts"))
from instadescribe_contracts.environment import getenv_compat  # noqa: E402

IMAGE = getenv_compat("INSTADESCRIBE_WORKER_IMAGE") or "instadescribe-worker:g8"
DOCKERFILE = REPO / "services" / "worker" / "Dockerfile"
DEPENDENCY_SMOKE = REPO / "services" / "worker" / "scripts" / "dependency_runtime_smoke.py"

# A conservative image-reference shape: name[:tag][@digest] with no shell
# metacharacters — rejected BEFORE any subprocess runs (G8.1 E).
IMAGE_REF_RE = re.compile(r"^[a-z0-9]+(?:[._/:@-][a-z0-9]+)*$", re.IGNORECASE)


def compressed_image_size(image: str, popen=subprocess.Popen) -> int:
    """gzip-equivalent compressed size of `docker save` output, computed
    WITHOUT a shell: argument-list subprocess, streamed through zlib, and
    the exporter's return code is CHECKED — an upstream failure can never
    produce a "successful" size (G8.1 E)."""
    if not IMAGE_REF_RE.fullmatch(image):
        raise ValueError("invalid image reference")
    proc = popen(["docker", "save", image], stdout=subprocess.PIPE)
    compressor = zlib.compressobj(6, zlib.DEFLATED, 31)  # 31 = gzip container
    total = 0
    assert proc.stdout is not None
    while True:
        chunk = proc.stdout.read(1024 * 1024)
        if not chunk:
            break
        total += len(compressor.compress(chunk))
    total += len(compressor.flush())
    if proc.wait() != 0:
        raise RuntimeError(f"docker save failed rc={proc.returncode}")
    if total == 0:
        raise RuntimeError("docker save produced no output")
    return total


evidence: dict = {"image_tag": IMAGE}


def die(msg: str) -> None:
    print(f"IMAGE PROOF FAILED: {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


def run(cmd: list[str], timeout: int = 900) -> str:
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        die(f"{' '.join(cmd[:4])} rc={proc.returncode}: {proc.stderr[-800:]}")
    return proc.stdout


def in_image(shell: str, timeout: int = 300) -> str:
    return run(
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
            shell,
        ],
        timeout=timeout,
    )


def dependency_smoke_in_image(timeout: int = 600) -> str:
    """Run the current worker smoke against the final image with no network.

    The proof script is bind-mounted read-only rather than shipped in the
    production image.  Its worker profile exercises only packages that belong
    to the exact production lock; SoundFile remains a local/Kokoro dependency.
    """

    return run(
        [
            "docker",
            "run",
            "--rm",
            "--platform",
            "linux/amd64",
            "--network",
            "none",
            "--mount",
            f"type=bind,source={DEPENDENCY_SMOKE},target=/tmp/dependency_runtime_smoke.py,readonly",
            "--entrypoint",
            "python",
            IMAGE,
            "/tmp/dependency_runtime_smoke.py",
            "--profile",
            "worker",
        ],
        timeout=timeout,
    )


def main() -> None:
    dockerfile = DOCKERFILE.read_text()
    base_digest = re.search(r"python:3\.12-slim@(sha256:[0-9a-f]{64})", dockerfile)
    whisper_rev = re.search(r"ARG WHISPER_REVISION=([0-9a-f]{40})", dockerfile)
    if not base_digest or not whisper_rev:
        die("Dockerfile no longer pins the base digest or Whisper revision")
    evidence["dockerfile_base_digest"] = base_digest.group(1)
    evidence["dockerfile_whisper_revision"] = whisper_rev.group(1)

    if not IMAGE_REF_RE.fullmatch(IMAGE):
        die(f"invalid image reference {IMAGE!r}")
    inspect = json.loads(run(["docker", "image", "inspect", IMAGE]))[0]
    if inspect["Architecture"] != "amd64" or inspect["Os"] != "linux":
        die(f"image is {inspect['Os']}/{inspect['Architecture']}, not linux/amd64")
    env = inspect["Config"]["Env"]
    if "HF_HUB_OFFLINE=1" not in env:
        die("HF_HUB_OFFLINE=1 missing from the image environment")

    # G8.1 B1: the image must be bound to the CURRENT tree — recomputed
    # digest vs the baked label; the base digest and model revision labels
    # must agree with the Dockerfile pins.
    labels = inspect["Config"].get("Labels") or {}
    current_digest = production_source_digest(REPO, "worker")
    if labels.get("io.instadescribe.source-digest") != current_digest:
        die(
            "stale image: label source-digest "
            f"{(labels.get('io.instadescribe.source-digest') or '(missing)')[:16]}… != "
            f"current {current_digest[:16]}… — rebuild with make g8-build"
        )
    if labels.get("io.instadescribe.base-digest") != base_digest.group(1):
        die("image base-digest label disagrees with the Dockerfile pin")
    if labels.get("io.instadescribe.whisper-revision") != whisper_rev.group(1):
        die("image whisper-revision label disagrees with the Dockerfile pin")
    evidence["source_digest"] = current_digest
    evidence["labels_verified"] = True
    evidence["image_id"] = inspect["Id"]
    evidence["created"] = inspect["Created"]
    evidence["unpacked_size_bytes"] = inspect["Size"]
    evidence["unpacked_size_gb"] = round(inspect["Size"] / 1e9, 2)
    evidence["repo_digests"] = inspect.get("RepoDigests", [])
    evidence["config_user"] = inspect["Config"].get("User", "")

    uid = in_image("id -u; id -un").split()
    if uid[0] != "10001" or uid[1] != "worker":
        die(f"runtime user is {uid}, expected uid 10001 'worker'")
    evidence["runtime_uid"] = 10001

    # Whisper snapshot is baked at the pinned revision and resolves OFFLINE.
    rev = whisper_rev.group(1)
    snap = in_image(
        "cat /home/worker/.cache/instadescribe/models--Systran--faster-whisper-medium/refs/main; "
        "echo; ls /home/worker/.cache/instadescribe/models--Systran--faster-whisper-medium/snapshots"
    ).split()
    if snap[0] != rev or rev not in snap[1:]:
        die(f"baked whisper snapshot mismatch: {snap} != {rev}")
    offline = in_image(
        'python -c "'
        "import os; assert os.environ['HF_HUB_OFFLINE'] == '1'; "
        "from faster_whisper.utils import download_model; "
        "p = download_model('medium', cache_dir='/home/worker/.cache/instadescribe'); "
        "print('offline-resolve-ok', p)\""
    )
    if "offline-resolve-ok" not in offline:
        die("offline model resolution failed inside the image")
    evidence["whisper_offline_resolution"] = "ok"

    # The final image must remain resolver-consistent, then pass real worker
    # dependency behaviours with Docker networking disabled.  This covers the
    # audio ABI, bundled Silero JIT, settings, boto3 and SQLAlchemy seams.
    in_image("pip check", timeout=600)
    try:
        dependency_smoke = parse_smoke_output(dependency_smoke_in_image())
    except (json.JSONDecodeError, ValueError) as exc:
        die(f"dependency runtime smoke returned invalid evidence: {exc}")
    if dependency_smoke.get("status") != "ok" or dependency_smoke.get("profile") != "worker":
        die(f"dependency runtime smoke returned unexpected evidence: {dependency_smoke}")
    evidence["dependency_runtime_smoke"] = dependency_smoke["checks"]

    absent = in_image(
        "set -e; "
        "for p in /app/fixtures /app/g0_smoke.py /app/App /app/modular_pipeline/.env "
        "/app/modular_pipeline/jobs /app/modular_pipeline/study_logs; do "
        '  if [ -e "$p" ]; then echo "present: $p"; exit 1; fi; done; '
        "if find /app \\( -name 'test_*' -o -name '*.mp4' -o -name '.env*' "
        "-o -name '*HANDOFF*' -o -name '*.pem' -o -name '*.key' "
        "-o -name '*.p12' -o -name '*.pfx' \\) -print | grep .; then exit 1; fi; "
        "echo forbidden-assets-absent"
    )
    if "forbidden-assets-absent" not in absent:
        die("forbidden assets present in the production image")
    evidence["forbidden_assets"] = "absent"

    # The CURRENT API model/domain copy imports from inside the image.
    imports = in_image(
        'python -c "import instadescribe_worker.main, instadescribe_worker.consumer, '
        "instadescribe_worker.render, instadescribe_contracts.queue, app.models, "
        "app.domain.states, app.services.lifecycle; "
        "from app.models import Artifact, Job; print('api-copy-import-ok')\""
    )
    if "api-copy-import-ok" not in imports:
        die("API model/domain copy failed to import inside the image")
    evidence["api_model_copy_import"] = "ok"

    # Compressed transfer size — shell-free, checked (G8.1 E); recorded, not
    # claimed byte-reproducible across zlib/gzip versions.
    try:
        compressed = compressed_image_size(IMAGE)
    except (RuntimeError, ValueError) as exc:
        die(f"compressed-size proof failed: {exc}")
    evidence["compressed_size_bytes"] = compressed
    evidence["compressed_size_gb"] = round(compressed / 1e9, 2)

    print(json.dumps(evidence, indent=2))
    print("G8 IMAGE PROOF PASSED", flush=True)


if __name__ == "__main__":
    main()
