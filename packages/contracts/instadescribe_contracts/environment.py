"""Safe compatibility helpers for the InstaDescribe environment namespace.

``INSTADESCRIBE_*`` is the canonical namespace.  The former
``INSTASCRIBE_*`` names remain a temporary v0.1 compatibility surface: an
old-only value is accepted with a value-free warning, equal old/new values
are accepted, and conflicting values fail closed before configuration is
constructed.
"""

from __future__ import annotations

import os
import threading
import warnings
from collections.abc import Iterable, Iterator, MutableMapping
from contextlib import contextmanager

CANONICAL_PREFIX = "INSTADESCRIBE_"
LEGACY_PREFIX = "INSTASCRIBE_"
_BRIDGE_LOCK = threading.RLock()


class LegacyEnvironmentWarning(FutureWarning):
    """A deprecated environment name was used without exposing its value."""


class LegacyEnvironmentConflictError(RuntimeError):
    """Canonical and legacy names were configured with different values."""


def _canonical_name(legacy_name: str) -> str:
    if not legacy_name.startswith(LEGACY_PREFIX):
        raise ValueError("legacy environment name must use the INSTASCRIBE_ prefix")
    return f"{CANONICAL_PREFIX}{legacy_name.removeprefix(LEGACY_PREFIX)}"


def _warn(legacy_names: list[str]) -> None:
    if not legacy_names:
        return
    names = ", ".join(sorted(legacy_names))
    warnings.warn(
        f"Deprecated InstaScribe environment name(s) detected: {names}; "
        "use the matching INSTADESCRIBE_* name(s). Values were not logged.",
        LegacyEnvironmentWarning,
        stacklevel=3,
    )


def getenv_compat(
    canonical_name: str,
    *,
    legacy_name: str | None = None,
    environ: MutableMapping[str, str] | None = None,
) -> str | None:
    """Return one configuration value using the canonical/legacy policy."""

    if not canonical_name.startswith(CANONICAL_PREFIX):
        raise ValueError("canonical environment name must use the INSTADESCRIBE_ prefix")
    legacy_name = legacy_name or canonical_name.replace(CANONICAL_PREFIX, LEGACY_PREFIX, 1)
    env = os.environ if environ is None else environ
    canonical = env.get(canonical_name)
    legacy = env.get(legacy_name)
    if canonical is not None and legacy is not None and canonical != legacy:
        raise LegacyEnvironmentConflictError(
            f"Conflicting environment names {canonical_name} and {legacy_name}; values were not logged"
        )
    if legacy is not None:
        _warn([legacy_name])
    return canonical if canonical is not None else legacy


@contextmanager
def bridged_environment(
    environ: MutableMapping[str, str] | None = None,
    *,
    canonical_names: Iterable[str] | None = None,
) -> Iterator[None]:
    """Temporarily expose old-only variables under their canonical names.

    ``pydantic-settings`` reads directly from ``os.environ``.  The bridge is
    intentionally temporary so constructing settings cannot leak canonical
    copies into later tests or child processes.
    """

    env = os.environ if environ is None else environ
    with _BRIDGE_LOCK:
        if canonical_names is None:
            names = {_canonical_name(name) for name in env if name.startswith(LEGACY_PREFIX)}
        else:
            names = set(canonical_names)
            if any(not name.startswith(CANONICAL_PREFIX) for name in names):
                raise ValueError("bridged environment names must use INSTADESCRIBE_ prefixes")

        inserted: list[tuple[str, str]] = []
        legacy_names: list[str] = []
        for canonical_name in sorted(names):
            legacy_name = canonical_name.replace(CANONICAL_PREFIX, LEGACY_PREFIX, 1)
            if legacy_name not in env:
                continue
            legacy_value = env[legacy_name]
            canonical_value = env.get(canonical_name)
            if canonical_value is not None and canonical_value != legacy_value:
                raise LegacyEnvironmentConflictError(
                    f"Conflicting environment names {canonical_name} and {legacy_name}; "
                    "values were not logged"
                )
            legacy_names.append(legacy_name)
            if canonical_value is None:
                env[canonical_name] = legacy_value
                inserted.append((canonical_name, legacy_value))
        _warn(legacy_names)
        try:
            yield
        finally:
            for canonical_name, inserted_value in reversed(inserted):
                if env.get(canonical_name) == inserted_value:
                    env.pop(canonical_name, None)
