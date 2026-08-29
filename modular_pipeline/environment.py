"""Environment-name compatibility for standalone pipeline entrypoints.

The API/worker use the equivalent shared-contract helper.  This local copy is
kept dependency-free because the legacy single-origin study image intentionally
ships the pipeline without the cloud service packages.
"""

from __future__ import annotations

import os
import warnings
from collections.abc import MutableMapping


class LegacyEnvironmentWarning(FutureWarning):
    """A deprecated environment name was used without exposing its value."""


def getenv_compat(
    canonical_name: str,
    *,
    legacy_name: str | None = None,
    environ: MutableMapping[str, str] | None = None,
) -> str | None:
    if not canonical_name.startswith("INSTADESCRIBE_"):
        raise ValueError("canonical environment name must use the INSTADESCRIBE_ prefix")
    legacy_name = legacy_name or canonical_name.replace("INSTADESCRIBE_", "INSTASCRIBE_", 1)
    env = os.environ if environ is None else environ
    canonical = env.get(canonical_name)
    legacy = env.get(legacy_name)
    if canonical is not None and legacy is not None and canonical != legacy:
        raise RuntimeError(
            f"Conflicting environment names {canonical_name} and {legacy_name}; "
            "values were not logged"
        )
    if legacy is not None:
        warnings.warn(
            f"Deprecated environment name {legacy_name}; use {canonical_name}. "
            "Values were not logged.",
            LegacyEnvironmentWarning,
            stacklevel=2,
        )
    return canonical if canonical is not None else legacy
