"""Worker entrypoint: deterministic --once mode for tests/smoke, and a
bounded-shutdown continuous mode for Compose/ECS.

Each loop is deliberately fair and bounded: it consumes at most one SQS
analysis message, then polls at most one database-authoritative five-format
render and one per-scene TTS preview. ``--once`` performs exactly that single
combined cycle; it does not wait for any work kind to appear a second time.

G5.1 B1/B3 hardening: SIGTERM/SIGINT terminate and reap the whole owned
process TREE (not just the direct child); invalid configuration exits nonzero
with a category-only event (no raw Pydantic traceback, no input values);
infrastructure failures in the receive/claim path surface as the sanitized
`infra_error` outcome — continuous mode applies bounded backoff instead of a
hot crash/restart loop, and --once exits nonzero after one such failure.
"""

import argparse
import signal
import sys
import time

from pydantic import ValidationError

from instadescribe_worker import executor
from instadescribe_worker.config import get_worker_settings
from instadescribe_worker.consumer import run_once
from instadescribe_worker.logging import log
from instadescribe_worker.preview import run_preview_once
from instadescribe_worker.render import run_render_once

BACKOFF_INITIAL_SECS = 1.0
BACKOFF_MAX_SECS = 30.0
SHUTDOWN_SKIPPED_OUTCOME = "shutdown_skipped"


def _shutdown_requested() -> bool:
    return executor.shutdown_requested()


def _wait_for_shutdown(timeout_secs: float) -> None:
    """Bound backoff while polling the signal-safe sticky shutdown latch."""

    deadline = time.monotonic() + timeout_secs
    while not _shutdown_requested():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.2, remaining))


def _run_work_cycle(settings) -> tuple[str, str, str]:
    """Run sequential work kinds without starting new work after shutdown."""

    if _shutdown_requested():
        return (SHUTDOWN_SKIPPED_OUTCOME,) * 3
    outcome = run_once(settings)
    if _shutdown_requested():
        return outcome, SHUTDOWN_SKIPPED_OUTCOME, SHUTDOWN_SKIPPED_OUTCOME
    render_outcome = run_render_once(settings)
    if _shutdown_requested():
        return outcome, render_outcome, SHUTDOWN_SKIPPED_OUTCOME
    return outcome, render_outcome, run_preview_once(settings)


def _handle_sigterm(signum, frame) -> None:
    # A Python signal handler may interrupt code while an internal lock is
    # held. Keep this path to one lock-free scalar assignment: the active
    # analysis/render poller observes the sticky latch and its ordinary
    # ``finally`` path performs bounded TERM -> KILL -> reap cleanup.
    executor.request_shutdown()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="instadescribe-worker")
    parser.add_argument(
        "--once",
        action="store_true",
        help="run one cycle: at most one analysis, one render and one TTS preview",
    )
    args = parser.parse_args(argv)
    # main() runs once in production; explicit reset keeps repeated unit-test
    # invocations isolated without weakening the sticky in-process flag.
    executor.reset_shutdown_state()
    try:
        settings = get_worker_settings()  # fails fast on invalid configuration
    except ValidationError as exc:
        # Category + field COUNT only — hide_input_in_errors already strips
        # values, and we never echo the error body at all.
        log(
            "worker_config_invalid",
            level="error",
            category="config",
            error_count=exc.error_count(),
        )
        return 1
    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)
    log(
        "worker_started",
        worker_label=settings.worker_id,
        once=args.once,
        long_poll_secs=settings.long_poll_secs,
    )
    if args.once:
        outcome, render_outcome, preview_outcome = _run_work_cycle(settings)
        log(
            "worker_once_complete",
            outcome=outcome,
            render_outcome=render_outcome,
            preview_outcome=preview_outcome,
        )
        return 1 if "infra_error" in {outcome, render_outcome, preview_outcome} else 0
    backoff = BACKOFF_INITIAL_SECS
    while not _shutdown_requested():
        outcome, render_outcome, preview_outcome = _run_work_cycle(settings)
        if outcome != "empty" or render_outcome != "empty" or preview_outcome != "empty":
            log(
                "worker_cycle",
                outcome=outcome,
                render_outcome=render_outcome,
                preview_outcome=preview_outcome,
            )
        if "infra_error" in {outcome, render_outcome, preview_outcome}:
            # Bounded backoff, interruptible by shutdown — never a hot loop.
            _wait_for_shutdown(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX_SECS)
        else:
            backoff = BACKOFF_INITIAL_SECS
    log("worker_stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
