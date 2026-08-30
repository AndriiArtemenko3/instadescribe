#!/usr/bin/env python3
"""Export the FastAPI contract deterministically, even when docs are disabled.

CI uses ``--check`` to make API changes explicit.  The full application spec is
authoritative; SDK generation may select only operations carrying
``x-sdk-public: true`` rather than maintaining a second handwritten API shape.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = API_ROOT.parents[1]
CONTRACTS_ROOT = REPOSITORY_ROOT / "packages" / "contracts"
DEFAULT_OUTPUT = REPOSITORY_ROOT / "openapi" / "instadescribe-cloud-v1.json"

for import_root in (CONTRACTS_ROOT, API_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from app.main import app  # noqa: E402


def rendered_contract() -> bytes:
    return (json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n").encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = rendered_contract()
    if args.check:
        try:
            actual = args.output.read_bytes()
        except FileNotFoundError:
            print(f"OpenAPI snapshot is missing: {args.output}", file=sys.stderr)
            return 1
        if actual != expected:
            print(
                "OpenAPI snapshot is stale; run services/api/scripts/export_openapi.py",
                file=sys.stderr,
            )
            return 1
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
