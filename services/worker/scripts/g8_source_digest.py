#!/usr/bin/env python3
"""Deterministic, fail-closed production COPY-input binding.

The digest covers path, entry type, POSIX permission bits and file content for
every entry copied into the worker/API production image.  Required roots may
not disappear silently; symlinks and special files are rejected because their
Docker COPY semantics would otherwise make provenance ambiguous.
"""

import hashlib
import stat
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

_INPUTS: dict[str, dict[str, list[str]]] = {
    "worker": {
        "files": [
            "services/worker/Dockerfile",
            "services/worker/requirements.txt",
            "services/worker/requirements.in",
            ".dockerignore",
            "services/api/app/__init__.py",
            "services/api/app/db/__init__.py",
            "services/api/app/db/base.py",
            "services/api/app/core/__init__.py",
            "services/api/app/core/tenancy.py",
            "services/api/app/repositories/__init__.py",
            "services/api/app/repositories/lifecycle.py",
            "services/api/app/services/__init__.py",
            "services/api/app/services/lifecycle.py",
            "services/api/app/services/quota.py",
            "services/api/app/services/tts_previews.py",
        ],
        "trees": [
            "services/worker/instadescribe_worker",
            "packages/contracts/instadescribe_contracts",
            "services/api/app/domain",
            "services/api/app/models",
            "modular_pipeline",
        ],
    },
    "api": {
        "files": [
            "services/api/Dockerfile",
            "services/api/requirements.txt",
            "services/api/requirements.in",
            ".dockerignore",
            "alembic.ini",
        ],
        "trees": [
            "services/api/app",
            "packages/contracts/instadescribe_contracts",
            "migrations",
        ],
    },
}

_EXCLUDED_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
_EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
_FORBIDDEN_CONTEXT_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}
_EXCLUDED_RELATIVE_PARTS = {("modular_pipeline", "jobs"), ("modular_pipeline", "study_logs")}


@dataclass(frozen=True)
class InputEntry:
    path: Path
    kind: str
    mode: int


def _excluded(root: Path, path: Path) -> bool:
    rel = path.relative_to(root)
    if any(name in _EXCLUDED_NAMES for name in rel.parts):
        return True
    # Docker context cache rules are case-sensitive: only canonical generated
    # `.pyc`/`.pyo` names are excluded. Unusual `.PYC`/`.PYO` inputs therefore
    # remain source-bound. Private-key-like suffixes are deliberately
    # case-insensitive in both this digest and the real context policy.
    if path.suffix in _EXCLUDED_SUFFIXES:
        return True
    if path.suffix.lower() in _FORBIDDEN_CONTEXT_SUFFIXES:
        return True
    return any(rel.parts[: len(parts)] == parts for parts in _EXCLUDED_RELATIVE_PARTS)


def _entry(path: Path) -> InputEntry:
    info = path.lstat()
    mode = stat.S_IMODE(info.st_mode)
    if stat.S_ISREG(info.st_mode):
        return InputEntry(path, "file", mode)
    if stat.S_ISDIR(info.st_mode):
        return InputEntry(path, "dir", mode)
    if stat.S_ISLNK(info.st_mode):
        raise ValueError(f"production input symlink is not allowed: {path}")
    raise ValueError(f"production input special file is not allowed: {path}")


def iter_production_entries(root: Path, service: str) -> Iterable[InputEntry]:
    if service not in _INPUTS:
        raise ValueError(f"unknown service {service!r}")
    spec = _INPUTS[service]
    for rel in spec["files"]:
        path = root / rel
        if not path.exists() and not path.is_symlink():
            raise ValueError(f"required production input missing: {rel}")
        entry = _entry(path)
        if entry.kind != "file":
            raise ValueError(f"required production file is not regular: {rel}")
        yield entry
    for rel in spec["trees"]:
        base = root / rel
        if not base.exists() and not base.is_symlink():
            raise ValueError(f"required production tree missing: {rel}")
        root_entry = _entry(base)
        if root_entry.kind != "dir":
            raise ValueError(f"required production tree is not a directory: {rel}")
        yield root_entry
        for path in sorted(base.rglob("*")):
            if _excluded(root, path):
                continue
            yield _entry(path)


def iter_production_inputs(root: Path, service: str) -> Iterable[Path]:
    """Compatibility iterator: regular files that contribute to the digest."""
    for entry in iter_production_entries(root, service):
        if entry.kind == "file":
            yield entry.path


def production_source_digest(root: Path, service: str) -> str:
    outer = hashlib.sha256()
    count = 0
    for entry in sorted(
        iter_production_entries(root, service),
        key=lambda value: value.path.relative_to(root).as_posix(),
    ):
        rel = entry.path.relative_to(root).as_posix()
        header = f"{rel}\0{entry.kind}\0{entry.mode:04o}\0"
        if entry.kind == "file":
            header += hashlib.sha256(entry.path.read_bytes()).hexdigest()
        outer.update((header + "\n").encode())
        count += 1
    if count == 0:
        raise ValueError(f"no production inputs found under {root} for {service!r}")
    return outer.hexdigest()


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in _INPUTS:
        print("usage: g8_source_digest.py {worker|api}", file=sys.stderr)
        sys.exit(2)
    print(production_source_digest(REPO, sys.argv[1]))


if __name__ == "__main__":
    main()
