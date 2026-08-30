#!/usr/bin/env python3
"""Fail closed when the production worker lock drifts from its intent.

The worker image is compiled for Linux/amd64 against PyPI plus the PyTorch
CPU wheel index.  A regenerated lock must retain those index declarations,
pin every package exactly, and contain a satisfying exact pin for every
direct requirement.  The explicit setuptools floor is a security contract,
not merely a transitive resolver choice, so it is checked independently.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DEFAULT_INTENT = REPO / "services" / "worker" / "requirements.in"
DEFAULT_LOCK = REPO / "services" / "worker" / "requirements.txt"

PYPI_INDEX = "--index-url https://pypi.org/simple"
PYTORCH_CPU_INDEX = "--extra-index-url https://download.pytorch.org/whl/cpu"
SETUPTOOLS_SECURITY_FLOOR = "83.0.0"

_REQUIREMENT_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?:\[(?P<extras>[A-Za-z0-9._,-]+)\])?"
    r"(?P<specifiers>.*)$"
)
_SPECIFIER_RE = re.compile(r"^(~=|==|!=|<=|>=|<|>)\s*([^\s,]+)$")
_PIN_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.!+_-]*$")
_RELEASE_RE = re.compile(r"^v?(?P<release>\d+(?:\.\d+)*)(?P<local>\+[A-Za-z0-9._-]+)?$")


class LockContractError(ValueError):
    """The worker intent and compiled lock do not satisfy the contract."""


@dataclass(frozen=True)
class LockSummary:
    direct_requirements: int
    locked_requirements: int


@dataclass(frozen=True)
class ParsedRequirement:
    requirement: RequirementSpec
    line_number: int


@dataclass(frozen=True)
class RequirementSpec:
    name: str
    specifiers: tuple[tuple[str, str], ...]
    raw: str


def _canonicalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _parse_requirement(line: str) -> RequirementSpec:
    if ";" in line or " @ " in line:
        raise ValueError("markers and direct URLs are not supported")
    match = _REQUIREMENT_RE.fullmatch(line)
    if match is None:
        raise ValueError("invalid requirement name")
    specifier_text = match.group("specifiers").strip()
    specifiers: list[tuple[str, str]] = []
    if specifier_text:
        for item in specifier_text.split(","):
            specifier_match = _SPECIFIER_RE.fullmatch(item.strip())
            if specifier_match is None:
                raise ValueError(f"unsupported version specifier {item.strip()!r}")
            specifiers.append((specifier_match.group(1), specifier_match.group(2)))
    return RequirementSpec(match.group("name"), tuple(specifiers), line)


def _content_lines(path: Path) -> list[tuple[int, str]]:
    """Return deterministic logical requirement lines with comments removed."""

    lines: list[tuple[int, str]] = []
    continued = ""
    continued_at = 0
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if continued:
            stripped = f"{continued} {stripped}"
            line_number = continued_at
        if stripped.endswith("\\"):
            continued = stripped[:-1].rstrip()
            continued_at = line_number
            continue
        continued = ""
        # Requirement-file inline comments require whitespace before '#'; URL
        # fragments therefore remain intact.
        stripped = re.split(r"\s+#", stripped, maxsplit=1)[0].strip()
        if stripped:
            lines.append((line_number, stripped))
    if continued:
        raise LockContractError(f"{path}:{continued_at}: unterminated line continuation")
    return lines


def _parse_requirements(
    path: Path,
    *,
    allowed_options: set[str],
    exact: bool,
) -> tuple[list[ParsedRequirement], set[str]]:
    parsed: list[ParsedRequirement] = []
    options: set[str] = set()
    for line_number, line in _content_lines(path):
        if line.startswith("-"):
            if line not in allowed_options:
                raise LockContractError(f"{path}:{line_number}: unsupported option {line!r}")
            options.add(line)
            continue
        try:
            requirement = _parse_requirement(line)
        except ValueError as exc:
            raise LockContractError(
                f"{path}:{line_number}: invalid requirement {line!r}: {exc}"
            ) from exc
        if exact and not _is_exact_pin(requirement):
            raise LockContractError(
                f"{path}:{line_number}: lock entry {line!r} must use one exact '==' pin"
            )
        parsed.append(ParsedRequirement(requirement, line_number))
    return parsed, options


def _is_exact_pin(requirement: RequirementSpec) -> bool:
    specifiers = requirement.specifiers
    return (
        len(specifiers) == 1
        and specifiers[0][0] == "=="
        and "*" not in specifiers[0][1]
        and _PIN_VERSION_RE.fullmatch(specifiers[0][1]) is not None
    )


def _pin_version(parsed: ParsedRequirement) -> str:
    return parsed.requirement.specifiers[0][1]


def _release(version: str) -> tuple[int, ...]:
    match = _RELEASE_RE.fullmatch(version)
    if match is None:
        raise ValueError(f"unsupported non-release version {version!r}")
    return tuple(int(part) for part in match.group("release").split("."))


def _compare_releases(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    width = max(len(left), len(right))
    padded_left = left + (0,) * (width - len(left))
    padded_right = right + (0,) * (width - len(right))
    return (padded_left > padded_right) - (padded_left < padded_right)


def _satisfies_specifier(pin: str, operator: str, requested: str) -> bool:
    pin_public, _, pin_local = pin.partition("+")
    requested_public, _, requested_local = requested.partition("+")
    if requested_public.endswith(".*"):
        if operator not in {"==", "!="}:
            raise ValueError(f"wildcard is invalid with {operator}")
        prefix = _release(requested_public[:-2])
        matches = _release(pin_public)[: len(prefix)] == prefix
        return matches if operator == "==" else not matches

    pin_release = _release(pin_public)
    requested_release = _release(requested_public)
    comparison = _compare_releases(pin_release, requested_release)
    if operator == "==":
        matches = comparison == 0 and (not requested_local or pin_local == requested_local)
        return matches
    if operator == "!=":
        matches = comparison == 0 and (not requested_local or pin_local == requested_local)
        return not matches
    if operator == ">=":
        return comparison >= 0
    if operator == ">":
        return comparison > 0
    if operator == "<=":
        return comparison <= 0
    if operator == "<":
        return comparison < 0
    if operator == "~=":
        if comparison < 0:
            return False
        compatible_prefix = (
            requested_release[:-1] if len(requested_release) > 1 else requested_release
        )
        return pin_release[: len(compatible_prefix)] == compatible_prefix
    raise ValueError(f"unsupported operator {operator!r}")


def _satisfies(requirement: RequirementSpec, pin: str) -> bool:
    return all(
        _satisfies_specifier(pin, operator, requested)
        for operator, requested in requirement.specifiers
    )


def _has_setuptools_floor(requirement: RequirementSpec) -> bool:
    security_release = _release(SETUPTOOLS_SECURITY_FLOOR)
    for operator, version in requirement.specifiers:
        if operator not in {">=", ">", "~="}:
            continue
        try:
            if _compare_releases(_release(version), security_release) >= 0:
                return True
        except ValueError:
            continue
    return False


def verify_worker_lock(
    intent_path: Path = DEFAULT_INTENT, lock_path: Path = DEFAULT_LOCK
) -> LockSummary:
    intent_path = Path(intent_path)
    lock_path = Path(lock_path)
    direct, intent_options = _parse_requirements(
        intent_path,
        allowed_options={PYTORCH_CPU_INDEX},
        exact=False,
    )
    locked, lock_options = _parse_requirements(
        lock_path,
        allowed_options={PYPI_INDEX, PYTORCH_CPU_INDEX},
        exact=True,
    )

    if PYTORCH_CPU_INDEX not in intent_options:
        raise LockContractError(
            f"{intent_path}: missing canonical CPU wheel index: {PYTORCH_CPU_INDEX}"
        )
    for required_index in (PYPI_INDEX, PYTORCH_CPU_INDEX):
        if required_index not in lock_options:
            raise LockContractError(f"{lock_path}: missing canonical index: {required_index}")

    pins: dict[str, tuple[str, int]] = {}
    for parsed in locked:
        name = _canonicalize_name(parsed.requirement.name)
        if name in pins:
            previous_line = pins[name][1]
            raise LockContractError(
                f"{lock_path}:{parsed.line_number}: duplicate pin for {name!r} "
                f"(first declared on line {previous_line})"
            )
        pins[name] = (_pin_version(parsed), parsed.line_number)

    direct_names: set[str] = set()
    setuptools_intents: list[RequirementSpec] = []
    for parsed in direct:
        requirement = parsed.requirement
        name = _canonicalize_name(requirement.name)
        direct_names.add(name)
        if name == "setuptools":
            setuptools_intents.append(requirement)
        pin = pins.get(name)
        if pin is None:
            raise LockContractError(
                f"{lock_path}: no exact pin for direct requirement "
                f"{intent_path}:{parsed.line_number} ({requirement.raw})"
            )
        version, lock_line = pin
        try:
            satisfies = _satisfies(requirement, version)
        except ValueError as exc:
            raise LockContractError(
                f"cannot compare {lock_path}:{lock_line} against "
                f"{intent_path}:{parsed.line_number}: {exc}"
            ) from exc
        if not satisfies:
            raise LockContractError(
                f"{lock_path}:{lock_line}: {name}=={version} does not satisfy "
                f"{intent_path}:{parsed.line_number} ({requirement.raw})"
            )

    if not setuptools_intents or not any(
        _has_setuptools_floor(requirement) for requirement in setuptools_intents
    ):
        raise LockContractError(
            f"{intent_path}: setuptools must remain a direct requirement with a security floor "
            f"of at least {SETUPTOOLS_SECURITY_FLOOR}"
        )
    setuptools_pin = pins.get("setuptools")
    if setuptools_pin is None:
        raise LockContractError(f"{lock_path}: missing exact setuptools pin")
    try:
        setuptools_below_floor = (
            _compare_releases(_release(setuptools_pin[0]), _release(SETUPTOOLS_SECURITY_FLOOR)) < 0
        )
    except ValueError as exc:
        raise LockContractError(f"{lock_path}:{setuptools_pin[1]}: {exc}") from exc
    if setuptools_below_floor:
        raise LockContractError(
            f"{lock_path}:{setuptools_pin[1]}: setuptools pin {setuptools_pin[0]} is below "
            f"security floor {SETUPTOOLS_SECURITY_FLOOR}"
        )

    return LockSummary(
        direct_requirements=len(direct_names),
        locked_requirements=len(pins),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intent", type=Path, default=DEFAULT_INTENT)
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    args = parser.parse_args(argv)
    try:
        summary = verify_worker_lock(args.intent, args.lock)
    except (LockContractError, OSError) as exc:
        print(f"worker lock contract failed: {exc}", file=sys.stderr)
        return 1
    print(
        "worker lock contract OK: "
        f"{summary.direct_requirements} direct requirements, "
        f"{summary.locked_requirements} exact pins"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
