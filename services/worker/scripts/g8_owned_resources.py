"""Exact-name, label-owned Docker resources for the API image proof."""

import json
import subprocess
from dataclasses import dataclass

from g8_common import REPO, CleanupError, cleanup_command

OWNER_LABEL = "io.instadescribe.gate-owner"
RUN_LABEL = "io.instadescribe.gate-run"


@dataclass(frozen=True)
class Resource:
    kind: str
    name: str
    identifier: str


def _default_runner(command: list[str]):
    return subprocess.run(command, cwd=REPO, capture_output=True, text=True, timeout=120)


def _canonical_not_found(kind: str, name: str, stderr: str) -> bool:
    """Recognize only Docker's exact, name-bound absence diagnostics.

    Generic text such as ``daemon endpoint not found`` is an operational
    failure, not proof that the requested resource is absent.
    """
    expected = {
        "container": f"Error response from daemon: No such container: {name}",
        "network": f"Error response from daemon: network {name} not found",
        "volume": f"Error response from daemon: get {name}: no such volume",
    }
    pattern = expected.get(kind)
    return pattern is not None and stderr.splitlines() == [pattern]


def _expected_inspected_name(kind: str, name: str) -> str:
    return f"/{name}" if kind == "container" else name


def inspect_exact(
    kind: str,
    name: str,
    owner: str,
    *,
    run_id: str | None = None,
    runner=None,
) -> Resource | None:
    if kind not in {"container", "network", "volume"}:
        raise CleanupError("resource-query:unsupported-kind")
    runner = runner or _default_runner
    command = ["docker", kind, "inspect", name]
    try:
        proc = runner(command)
    except BaseException as exc:
        raise CleanupError(f"{kind}-query:exception:{type(exc).__name__}") from None
    if proc.returncode != 0:
        if proc.returncode == 1 and _canonical_not_found(kind, name, proc.stderr or ""):
            return None
        raise CleanupError(f"{kind}-query:nonzero")
    try:
        decoded = json.loads(proc.stdout)
        if not isinstance(decoded, list) or len(decoded) != 1 or not isinstance(decoded[0], dict):
            raise ValueError
        item = decoded[0]
        config = item.get("Config")
        if config is not None and not isinstance(config, dict):
            raise ValueError
        labels = (config or {}).get("Labels") if kind == "container" else item.get("Labels")
        labels = labels or {}
        inspected_name = item.get("Name")
        identifier = inspected_name if kind == "volume" else item.get("Id") or item.get("ID")
    except (AttributeError, KeyError, IndexError, TypeError, ValueError):
        raise CleanupError(f"{kind}-query:malformed") from None
    if not isinstance(labels, dict):
        raise CleanupError(f"{kind}-query:malformed")
    if labels.get(OWNER_LABEL) != owner:
        raise CleanupError(f"{kind}-collision:unowned")
    if run_id is not None and labels.get(RUN_LABEL) != run_id:
        raise CleanupError(f"{kind}-collision:wrong-run")
    if inspected_name != _expected_inspected_name(kind, name):
        raise CleanupError(f"{kind}-query:name-mismatch")
    if not isinstance(identifier, str) or not identifier:
        raise CleanupError(f"{kind}-query:missing-id")
    return Resource(kind, name, identifier)


def cleanup_owned_resources(
    *,
    owner: str,
    containers: list[str],
    network: str,
    volume: str,
    run_id: str | None = None,
    runner=None,
) -> None:
    """Inspect ownership, remove by immutable IDs, and verify exact absence.

    Every inspect/remove/verification is attempted.  Unowned collisions are
    never deleted. Docker volumes expose names rather than immutable IDs, so
    their exact inspected name is the removal target.
    """
    runner = runner or _default_runner
    failures: list[str] = []
    resolved: list[Resource] = []
    for kind, names in (
        ("container", containers),
        ("network", [network]),
        ("volume", [volume]),
    ):
        for name in names:
            try:
                resource = inspect_exact(kind, name, owner, run_id=run_id, runner=runner)
                if resource is not None:
                    resolved.append(resource)
            except CleanupError as exc:
                failures.append(str(exc))

    for resource in resolved:
        target = resource.name if resource.kind == "volume" else resource.identifier
        command = ["docker", resource.kind, "rm"]
        if resource.kind == "container":
            command.append("-f")
        command.append(target)
        try:
            cleanup_command(command, f"{resource.kind}-remove", runner=runner)
        except CleanupError as exc:
            failures.append(str(exc))

    for kind, names in (
        ("container", containers),
        ("network", [network]),
        ("volume", [volume]),
    ):
        for name in names:
            try:
                leftover = inspect_exact(kind, name, owner, run_id=None, runner=runner)
                if leftover is not None:
                    failures.append(f"{kind}-residue")
            except CleanupError as exc:
                failures.append(str(exc))
    if failures:
        raise CleanupError("owned-cleanup-failed:" + ",".join(failures))


def label_args(owner: str, run_id: str) -> list[str]:
    return ["--label", f"{OWNER_LABEL}={owner}", "--label", f"{RUN_LABEL}={run_id}"]
