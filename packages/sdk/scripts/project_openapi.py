#!/usr/bin/env python3
"""Project the authoritative FastAPI document onto ``x-sdk-public`` operations."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

SDK_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = SDK_ROOT.parents[1]
DEFAULT_INPUT = REPOSITORY_ROOT / "openapi" / "instadescribe-cloud-v1.json"
DEFAULT_OUTPUT = SDK_ROOT / "openapi" / "instadescribe-integration-v1.contract.json"
HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})
INTERNAL_PREFIX = "/api/integrations/v1"
PUBLIC_PREFIX = "/v1"


def _scan_refs(value: Any, refs: set[str]) -> None:
    if isinstance(value, dict):
        reference = value.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/components/"):
            refs.add(reference)
        for nested in value.values():
            _scan_refs(nested, refs)
    elif isinstance(value, list):
        for nested in value:
            _scan_refs(nested, refs)


def _component(source: dict[str, Any], reference: str) -> tuple[str, str, Any]:
    parts = reference.removeprefix("#/components/").split("/", 1)
    if len(parts) != 2:
        raise ValueError(f"unsupported component reference: {reference}")
    section, name = parts
    try:
        value = source["components"][section][name]
    except KeyError:
        raise ValueError(f"missing component reference: {reference}") from None
    return section, name, copy.deepcopy(value)


def project(source: dict[str, Any]) -> dict[str, Any]:
    paths: dict[str, Any] = {}
    security_names: set[str] = set()
    for path, path_item in source.get("paths", {}).items():
        selected: dict[str, Any] = {}
        for key, value in path_item.items():
            if key not in HTTP_METHODS:
                selected[key] = copy.deepcopy(value)
                continue
            if isinstance(value, dict) and value.get("x-sdk-public") is True:
                selected[key] = copy.deepcopy(value)
                for requirement in value.get("security", []):
                    if isinstance(requirement, dict):
                        security_names.update(requirement)
        if any(method in selected for method in HTTP_METHODS):
            if not path.startswith(f"{INTERNAL_PREFIX}/") and path != INTERNAL_PREFIX:
                raise ValueError(f"SDK-public operation is outside the integration router: {path}")
            public_path = f"{PUBLIC_PREFIX}{path.removeprefix(INTERNAL_PREFIX)}"
            paths[public_path] = selected
    if not paths:
        raise ValueError("authoritative contract has no x-sdk-public operations")

    refs: set[str] = set()
    _scan_refs(paths, refs)
    components: dict[str, dict[str, Any]] = {}
    visited: set[str] = set()
    while refs - visited:
        reference = sorted(refs - visited)[0]
        visited.add(reference)
        section, name, value = _component(source, reference)
        components.setdefault(section, {})[name] = value
        _scan_refs(value, refs)
    available_schemes = source.get("components", {}).get("securitySchemes", {})
    if security_names:
        components["securitySchemes"] = {
            name: copy.deepcopy(available_schemes[name]) for name in sorted(security_names)
        }

    return {
        "openapi": source["openapi"],
        "info": {
            **copy.deepcopy(source["info"]),
            "title": "InstaDescribe SDK public projection",
            "description": (
                "Generated from the authoritative FastAPI OpenAPI document; "
                "contains only operations marked x-sdk-public."
            ),
        },
        "paths": paths,
        "servers": [{"url": "https://api.instadescribe.com"}],
        "components": components,
        "x-instadescribe-contract-status": "sdk-public-projection",
        "x-instadescribe-authoritative-source": (
            "repository-root:openapi/instadescribe-cloud-v1.json"
        ),
    }


def rendered(source_path: Path) -> bytes:
    source = json.loads(source_path.read_text(encoding="utf-8"))
    return (json.dumps(project(source), indent=2, sort_keys=True) + "\n").encode()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = rendered(args.input)
    if args.check:
        try:
            actual = args.output.read_bytes()
        except FileNotFoundError:
            print(f"SDK OpenAPI projection is missing: {args.output}")
            return 1
        if actual != expected:
            print("SDK OpenAPI projection is stale; run packages/sdk/scripts/project_openapi.py")
            return 1
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
