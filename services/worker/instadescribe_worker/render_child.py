"""Isolated InstaDescribe entrypoint for the production five-format renderer.

The database-owning worker starts this module in a new process session.  This
child deliberately knows nothing about PostgreSQL, S3, leases, or publication:
it reads one worker-authored request file and writes only into the attempt's
disposable output directory.  The parent remains the sole authority for the
deadline, fence and atomic publication.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _required_string(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"render request field {name} is invalid")
    return value


def run(request_path: Path) -> None:
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("render request must be an object")

    pipeline_source = Path(_required_string(payload, "pipelineSource")).resolve()
    contracts_source = Path(_required_string(payload, "contractsSource")).resolve()
    if not pipeline_source.is_dir() or not contracts_source.is_dir():
        raise ValueError("render source is unavailable")
    sys.path.insert(0, str(contracts_source))
    sys.path.insert(0, str(pipeline_source))

    from providers.factory import set_active_backend

    set_active_backend(_required_string(payload, "provider"))
    from bundle_export import render_all_deliverables

    scenes = payload.get("scenes")
    entities_by_id = payload.get("entitiesById")
    if not isinstance(scenes, list) or not isinstance(entities_by_id, dict):
        raise ValueError("render snapshot is invalid")

    render_all_deliverables(
        source_video=Path(_required_string(payload, "sourceVideo")),
        scenes=scenes,
        entities_by_id=entities_by_id,
        output_dir=Path(_required_string(payload, "outputDir")),
        project_name=_required_string(payload, "projectName"),
        default_voice=_required_string(payload, "defaultVoice"),
    )


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        return 2
    try:
        run(Path(args[0]))
    except Exception as exc:
        # The parent retains only this bounded category. Reviewed narration,
        # paths, provider responses and credentials must never reach logs.
        print(f"render child failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
