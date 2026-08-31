"""Process-isolated execution for local investigation inference."""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

from instadescribe_investigation_core import (
    BeliefConfig,
    InvestigationKind,
    LocalRunExpectation,
    SourceRecord,
    local_run_result_from_primitive,
    to_primitive,
)
from instadescribe_investigation_core import (
    __version__ as investigation_core_version,
)

from instadescribe_worker.config import WorkerSettings
from instadescribe_worker.executor import (
    WorkerShutdownRequested,
    raise_if_shutdown_requested,
    register_current_child,
    shutdown_requested,
    terminate_tree,
    unregister_current_child,
)
from instadescribe_worker.failures import FailureCode, JobFailure
from instadescribe_worker.investigation_runtime import (
    InvestigationRuntimeSettings,
    fixture_candidates,
    fixture_model_provenance,
)

_MAX_RESULT_BYTES = 5_000_000


@contextmanager
def host_inference_lease(
    settings: WorkerSettings,
    *,
    on_tick: Callable[[], None],
):
    """Serialize heavy local inference across workers sharing one host root."""

    lease_root = Path(settings.workspace_root or tempfile.gettempdir()).resolve()
    lease_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(
        lease_root / ".instadescribe-investigation-heavy.lock",
        os.O_RDWR | os.O_CREAT,
        0o600,
    )
    deadline = time.monotonic() + settings.subprocess_timeout_secs
    acquired = False
    try:
        while not acquired:
            raise_if_shutdown_requested()
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise JobFailure(
                        FailureCode.PIPELINE_TIMEOUT,
                        "local inference host lease exceeded its time budget",
                    ) from None
                on_tick()
                time.sleep(0.2)
        yield
    finally:
        if acquired:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _import_roots(module_file: Path | None = None) -> tuple[Path, Path, Path]:
    """Resolve the source-checkout or flattened production-image imports.

    The checkout module lives below ``services/worker`` while the production
    image copies it directly below ``/app``.  Derive both layouts through
    parent traversal instead of indexing a fixed-depth ``parents`` sequence:
    ``/app/instadescribe_worker/...`` has no fourth parent and must never fail
    before the explicit production fallback can run.
    """

    resolved_module = (module_file or Path(__file__)).resolve()
    worker_package = resolved_module.parent
    worker_root = worker_package.parent

    repository = worker_root.parent.parent
    source_core = repository / "packages" / "investigation-core" / "src"
    source_contracts = repository / "packages" / "contracts"
    if (
        (worker_root / "instadescribe_worker").is_dir()
        and source_core.is_dir()
        and source_contracts.is_dir()
    ):
        return worker_root, source_core, source_contracts

    # The production image copies the worker and both top-level packages
    # directly beneath /app. Require every package root so a partial/stale
    # image fails before child launch instead of importing ambient modules.
    if all(
        (worker_root / package).is_dir()
        for package in (
            "instadescribe_worker",
            "instadescribe_investigation_core",
            "instadescribe_contracts",
        )
    ):
        return worker_root, worker_root, worker_root

    raise JobFailure(
        FailureCode.PIPELINE_FAILED,
        "local investigation runtime packages are unavailable",
    )


def _child_environment(workspace: Path) -> dict[str, str]:
    child_home = workspace / ".home"
    child_tmp = workspace / ".tmp"
    child_cache = workspace / ".cache"
    child_home.mkdir(mode=0o700, exist_ok=True)
    child_tmp.mkdir(mode=0o700, exist_ok=True)
    child_cache.mkdir(mode=0o700, exist_ok=True)
    return {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(child_home),
        "TMPDIR": str(child_tmp),
        "XDG_CACHE_HOME": str(child_cache),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "PYTHONUNBUFFERED": "1",
    }


def execute_local_investigation(
    settings: WorkerSettings,
    *,
    media_path: Path,
    workspace: Path,
    source: SourceRecord,
    duration_seconds: float,
    investigation_id: str,
    trace_id: str,
    kind: InvestigationKind,
    on_tick: Callable[[], None],
):
    """Run one local investigation child under the worker's shutdown latch."""

    raise_if_shutdown_requested()
    request_path = workspace / "investigation-request.json"
    result_path = workspace / "investigation-result.json"
    runtime = InvestigationRuntimeSettings.model_validate(
        {
            "investigation_runtime": settings.investigation_runtime,
            "investigation_test_fixture_enabled": settings.investigation_test_fixture_enabled,
            "investigation_test_fixture_scenario": settings.investigation_test_fixture_scenario,
            "investigation_model": settings.investigation_model,
            "investigation_ollama_url": settings.investigation_ollama_url,
            "investigation_timeout_secs": settings.investigation_timeout_secs,
            "investigation_max_keyframes": settings.investigation_max_keyframes,
            "investigation_batch_size": settings.investigation_batch_size,
            "investigation_image_long_edge": settings.investigation_image_long_edge,
        }
    )
    if runtime.investigation_runtime != "fixture":
        # The loopback Ollama adapter is present and unit-tested, but a live
        # child cannot yet invent candidates/model provenance and still meet
        # the Apache core's parent-owned LocalRunExpectation contract. Actual
        # execution remains unavailable until a separate validated proposal
        # handshake makes those values parent inputs before final-run launch.
        raise JobFailure(
            FailureCode.INVALID_SETTINGS,
            "live Ollama investigation requires a parent-validated proposal handshake",
        )
    if kind is not InvestigationKind.GEOLOCATE_PROVENANCE:
        raise JobFailure(
            FailureCode.INVALID_SETTINGS,
            "only geolocateProvenance local investigations are available",
        )
    candidates = fixture_candidates(runtime.investigation_test_fixture_scenario)
    model = fixture_model_provenance(runtime.investigation_test_fixture_scenario)
    expectation = LocalRunExpectation(
        source=source,
        investigation_id=investigation_id,
        trace_id=trace_id,
        candidates=candidates,
        model_provenance=(model,),
        belief_config=BeliefConfig(),
        kind=kind,
    )
    request = {
        "schemaVersion": 2,
        "mediaPath": str(media_path.resolve()),
        "workspacePath": str(workspace.resolve()),
        "durationSeconds": duration_seconds,
        "expectation": to_primitive(expectation),
        "runtime": runtime.model_dump(),
    }
    request_body = json.dumps(
        request,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    request_descriptor = os.open(
        request_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    with os.fdopen(request_descriptor, "wb") as request_handle:
        request_handle.write(request_body)
        request_handle.flush()
        os.fsync(request_handle.fileno())
    worker_root, core_root, contracts_root = _import_roots()
    child_path = Path(__file__).with_name("investigation_child.py")
    command = [
        sys.executable,
        "-I",
        str(child_path),
        str(request_path),
        str(result_path),
        str(worker_root),
        str(core_root),
        str(contracts_root),
        investigation_core_version,
    ]
    child: subprocess.Popen | None = None
    deadline = time.monotonic() + settings.subprocess_timeout_secs
    try:
        child = subprocess.Popen(
            command,
            cwd=workspace,
            env=_child_environment(workspace),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        register_current_child(child)
        raise_if_shutdown_requested()
        while child.poll() is None:
            if shutdown_requested():
                raise WorkerShutdownRequested
            if time.monotonic() >= deadline:
                raise JobFailure(
                    FailureCode.PIPELINE_TIMEOUT,
                    "local investigation exceeded its time budget",
                )
            on_tick()
            time.sleep(0.2)
        if shutdown_requested():
            raise WorkerShutdownRequested
        if child.returncode != 0:
            raise JobFailure(FailureCode.PIPELINE_FAILED, "local investigation failed")
    finally:
        terminate_tree(child, settings.grace_secs)
        if child is not None:
            unregister_current_child(child)

    try:
        with result_path.open("rb") as handle:
            body = handle.read(_MAX_RESULT_BYTES + 1)
        if len(body) > _MAX_RESULT_BYTES:
            raise ValueError("result too large")
        envelope = json.loads(body)
        if (
            not isinstance(envelope, dict)
            or set(envelope) != {"schemaVersion", "coreVersion", "result"}
            or envelope["schemaVersion"] != 1
            or envelope["coreVersion"] != investigation_core_version
        ):
            raise ValueError("result envelope mismatch")
        return local_run_result_from_primitive(
            envelope["result"],
            expected=expectation,
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        raise JobFailure(
            FailureCode.PIPELINE_FAILED, "local investigation result is invalid"
        ) from error
