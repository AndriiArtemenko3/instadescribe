"""Isolated child entrypoint for the local investigation runtime."""

from __future__ import annotations

import json
import os
import resource
import sys
from pathlib import Path


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _set_hard_limit(kind: int, maximum: int) -> None:
    _soft, hard = resource.getrlimit(kind)
    target = maximum if hard == resource.RLIM_INFINITY else min(maximum, hard)
    resource.setrlimit(kind, (target, target))


def _apply_resource_limits() -> None:
    """Bound native decoders spawned by this already-isolated child.

    These POSIX limits are defense in depth; they do not replace a container,
    chroot or syscall sandbox for future adversarial public uploads.
    """

    _set_hard_limit(resource.RLIMIT_CORE, 0)
    _set_hard_limit(resource.RLIMIT_NOFILE, 128)
    _set_hard_limit(resource.RLIMIT_FSIZE, 64 * 1024 * 1024)
    _set_hard_limit(resource.RLIMIT_CPU, 1_200)


def main() -> int:
    if len(sys.argv) != 7:
        return 2
    os.umask(0o077)
    _apply_resource_limits()
    request_path = Path(sys.argv[1]).resolve()
    result_path = Path(sys.argv[2]).resolve()
    worker_import_root = Path(sys.argv[3]).resolve()
    core_import_root = Path(sys.argv[4]).resolve()
    contracts_import_root = Path(sys.argv[5]).resolve()
    package_version = sys.argv[6]
    sys.path.insert(0, str(worker_import_root))
    sys.path.insert(1, str(core_import_root))
    sys.path.insert(2, str(contracts_import_root))

    from instadescribe_investigation_core import local_run_result_to_primitive

    from instadescribe_worker.investigation_runtime import (
        InvestigationRunExpectationPayload,
        InvestigationRuntimeSettings,
        run_local_observation,
    )

    try:
        payload = json.loads(request_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or set(payload) != {
            "schemaVersion",
            "mediaPath",
            "workspacePath",
            "durationSeconds",
            "expectation",
            "runtime",
        }:
            return 3
        if payload["schemaVersion"] != 2:
            return 3
        workspace = Path(payload["workspacePath"]).resolve()
        media = Path(payload["mediaPath"]).resolve()
        if (
            not workspace.is_dir()
            or not media.is_file()
            or not _inside(media, workspace)
            or not _inside(request_path, workspace)
            or not _inside(result_path, workspace)
        ):
            return 3
        duration = payload["durationSeconds"]
        if (
            isinstance(duration, bool)
            or not isinstance(duration, int | float)
            or not 0 < duration <= 3600
        ):
            return 3
        expectation_payload = payload["expectation"]
        if not isinstance(expectation_payload, dict):
            return 3
        expectation = InvestigationRunExpectationPayload.model_validate(
            expectation_payload
        ).to_core()
        runtime_payload = payload["runtime"]
        if not isinstance(runtime_payload, dict):
            return 3
        runtime = InvestigationRuntimeSettings.model_validate(runtime_payload)

        result = run_local_observation(
            media,
            workspace,
            source=expectation.source,
            duration_seconds=float(duration),
            settings=runtime,
            investigation_id=expectation.investigation_id,
            trace_id=expectation.trace_id,
            kind=expectation.kind,
            expected_candidates=expectation.candidates,
            expected_model_provenance=expectation.model_provenance,
            belief_config=expectation.belief_config,
        )
        encoded = json.dumps(
            {
                "schemaVersion": 1,
                "coreVersion": package_version,
                "result": local_run_result_to_primitive(result),
            },
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        if len(encoded) > 5_000_000:
            return 4
        temporary = result_path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, result_path)
        return 0
    except Exception:
        # The parent logs only this process' exit class; never echo model
        # output, paths, prompts or raw exceptions onto inherited streams.
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
