"""The unchanged subprocess pipeline adapter (B7, hardened by G5.1 B1).

Launches `[sys.executable, run_job.py, job_id, settings_path]` in a NEW
process session/group with the pipeline directory as cwd and a minimal
explicit child environment (no AWS credentials, DSN, or client-chosen
provider — INSTADESCRIBE_BACKEND stays pinned to fake). stdout/stderr go to
workspace files so pipes cannot deadlock; only a bounded diagnostic tail is
retained. The child's exit status is authoritative even when status.json is
absent, stale, or says legacy 'ready'.

Process-tree guarantee: ONE idempotent helper (`terminate_tree`) sends TERM
to the whole process group, waits the bounded grace, sends KILL if needed and
always reaps the direct child. It runs on normal timeout, sticky
SIGTERM/SIGINT shutdown, progress-callback/DB exceptions, and every other
exit from the executor — the `finally` path — so a live grandchild (FFmpeg)
can never outlive the run. The caller only cleans the workspace AFTER
`run_pipeline` returns/raises, i.e. after the tree is gone.
"""

import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass

from instadescribe_contracts.provider import (
    OPENAI_BETA_MAX_PROVIDER_CALLS,
    PROVIDER_ALLOWLIST,
    ProviderName,
)

from instadescribe_worker.workspace import Workspace

_TAIL_BYTES = 2000
# The worker has one sequential compute loop, so at most one analysis/render
# child exists. These pointer/boolean assignments are deliberately lock-free:
# Python invokes signal handlers on the main thread between bytecodes, and a
# handler must never try to reacquire a lock held by the interrupted registry
# update. A sticky shutdown flag closes both sides of the Popen→register race.
_current_child: subprocess.Popen | None = None
_shutdown_requested = False


class WorkerShutdownRequested(Exception):
    """Graceful worker shutdown; never classify the active job as failed."""


def current_child() -> subprocess.Popen | None:
    return _current_child


def request_shutdown() -> None:
    global _shutdown_requested
    _shutdown_requested = True


def shutdown_requested() -> bool:
    return _shutdown_requested


def raise_if_shutdown_requested() -> None:
    if _shutdown_requested:
        raise WorkerShutdownRequested


def reset_shutdown_state() -> None:
    """Reset only at process startup or between isolated tests."""

    global _shutdown_requested
    child = _current_child
    if child is not None and child.poll() is None:
        raise RuntimeError("cannot reset shutdown while a worker child is live")
    _shutdown_requested = False


def register_current_child(child: subprocess.Popen) -> None:
    """Register the worker's one live process-group leader.

    Analysis, render and preview cycles are sequential. Sharing this exact
    registry lets the normal ``finally`` path terminate whichever production
    child owns compute after the SIGTERM/SIGINT hook sets the sticky latch.
    """

    global _current_child
    existing = _current_child
    if existing is not None and existing.poll() is None:
        raise RuntimeError("a worker child process is already registered")
    _current_child = child


def unregister_current_child(child: subprocess.Popen) -> None:
    """Clear only the exact registered child; safe after signal-path cleanup."""

    global _current_child
    if _current_child is child:
        _current_child = None


@dataclass
class ExecResult:
    exit_code: int
    timed_out: bool
    stderr_tail: str


def _child_env(
    provider: ProviderName = "fake",
    *,
    openai_api_key: str | None = None,
    max_provider_calls: int = 6,
    max_provider_output_tokens: int = 8000,
) -> dict[str, str]:
    """Build the complete subprocess environment from an explicit allowlist.

    This is intentionally not a copy/filter of ``os.environ``: AWS
    credentials, the database DSN, portfolio token, OPENAI_BASE_URL and every
    other ambient value are absent. Fake mode never inherits or forwards an
    OpenAI key even if the worker task environment contains one.
    """
    if provider not in PROVIDER_ALLOWLIST:
        raise ValueError("provider is not allowlisted")
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", "/tmp"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "INSTADESCRIBE_BACKEND": provider,
    }
    if os.environ.get("HF_HUB_OFFLINE"):
        env["HF_HUB_OFFLINE"] = os.environ["HF_HUB_OFFLINE"]
    if provider == "openai":
        if not openai_api_key or openai_api_key != openai_api_key.strip():
            raise ValueError("OpenAI credential unavailable")
        # Values are already bounded by WorkerSettings. Re-check the scalar
        # shape here so this lower-level seam also fails closed in isolation.
        if not 1 <= max_provider_calls <= OPENAI_BETA_MAX_PROVIDER_CALLS:
            raise ValueError("provider call bound is invalid")
        if not 1 <= max_provider_output_tokens <= 8000:
            raise ValueError("provider output-token bound is invalid")
        env["OPENAI_API_KEY"] = openai_api_key
        env["INSTADESCRIBE_MAX_PROVIDER_CALLS"] = str(max_provider_calls)
        env["INSTADESCRIBE_MAX_PROVIDER_OUTPUT_TOKENS"] = str(max_provider_output_tokens)
    return env


def _signal_group(child: subprocess.Popen, sig: int) -> None:
    """Signal the child's whole process group (it is a session leader via
    start_new_session); fall back to the direct child if the group is gone."""
    try:
        os.killpg(child.pid, sig)
    except (ProcessLookupError, PermissionError):
        try:
            child.send_signal(sig)
        except (ProcessLookupError, PermissionError):
            pass


def terminate_tree(child: subprocess.Popen | None, grace_secs: int) -> None:
    """Idempotent: TERM the process group, wait the bounded grace, KILL the
    group if needed, and ALWAYS reap the direct child. Safe to call after the
    child has already exited or been reaped."""
    if child is None:
        return
    if child.poll() is None:
        _signal_group(child, signal.SIGTERM)
        try:
            child.wait(timeout=grace_secs)
        except subprocess.TimeoutExpired:
            _signal_group(child, signal.SIGKILL)
        child.wait()  # always reap the direct child
    # Best-effort sweep for group members that survived the child's own exit;
    # after TERM/KILL above this is a no-op (the group is gone).
    _signal_group_if_alive(child)


def _signal_group_if_alive(child: subprocess.Popen) -> None:
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def terminate_current(grace_secs: int) -> None:
    """Synchronously terminate/reap the registered tree outside a handler."""
    terminate_tree(_current_child, grace_secs)


def read_status_defensively(workspace: Workspace) -> dict | None:
    """status.json is written non-atomically by the child — partial reads are
    transient, never fatal."""
    try:
        return json.loads(workspace.status_path.read_text())
    except Exception:
        return None


def run_pipeline(
    workspace: Workspace,
    job_id: str,
    *,
    timeout_secs: int,
    grace_secs: int,
    on_progress: Callable[[str, int], None],
    on_tick: Callable[[], None] | None = None,
    provider: ProviderName = "fake",
    openai_api_key: str | None = None,
    max_provider_calls: int = 6,
    max_provider_output_tokens: int = 8000,
) -> ExecResult:
    raise_if_shutdown_requested()
    stdout_path = workspace.job_dir / "stdout.log"
    stderr_path = workspace.job_dir / "stderr.log"
    deadline = time.monotonic() + timeout_secs
    timed_out = False
    exit_code: int | None = None
    with open(stdout_path, "wb") as out, open(stderr_path, "wb") as err:
        child = subprocess.Popen(
            [sys.executable, "run_job.py", job_id, str(workspace.settings_path)],
            cwd=str(workspace.pipeline_dir),
            env=_child_env(
                provider,
                openai_api_key=openai_api_key,
                max_provider_calls=max_provider_calls,
                max_provider_output_tokens=max_provider_output_tokens,
            ),
            stdout=out,
            stderr=err,
            start_new_session=True,  # own session/group: the WHOLE tree is signalable
        )
        registered = False
        try:
            register_current_child(child)
            registered = True
            raise_if_shutdown_requested()
            while True:
                raise_if_shutdown_requested()
                # The v0.2 lease/SQS heartbeat is driven synchronously from
                # this loop.  Any ownership or infrastructure failure raises,
                # and the existing finally destroys and reaps the whole child
                # process tree before control returns to the caller.
                if on_tick is not None:
                    on_tick()
                exit_code = child.poll()
                status = read_status_defensively(workspace)
                if status is not None:
                    stage = status.get("stage")
                    progress = status.get("progress")
                    if isinstance(stage, str) and isinstance(progress, int):
                        # A raising callback (e.g. DB down) propagates; the
                        # finally below still destroys the process tree.
                        on_progress(stage[:80], progress)
                if exit_code is not None:
                    break
                if time.monotonic() > deadline:
                    timed_out = True
                    break
                time.sleep(0.2)
        finally:
            # Every exit path — normal, timeout, callback exception,
            # interruption — leaves no live process tree behind, and the
            # child is reaped before the registry is cleared.
            terminate_tree(child, grace_secs)
            if registered:
                unregister_current_child(child)
        raise_if_shutdown_requested()
        if exit_code is None:
            exit_code = child.returncode
    tail = ""
    try:
        raw = stderr_path.read_bytes()[-_TAIL_BYTES:]
        tail = raw.decode("utf-8", errors="replace")
    except Exception:
        pass
    return ExecResult(exit_code=exit_code, timed_out=timed_out, stderr_tail=tail)
