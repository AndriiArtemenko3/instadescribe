#!/bin/bash
# LocalStack container healthcheck: healthy only when the service responds AND
# every ready.d init script (01-bootstrap, 02-verify) finished SUCCESSFUL —
# so a cold-stack `--wait` success proves the bootstrap ran and asserted clean.
set -euo pipefail
curl -sf http://localhost:4566/_localstack/health >/dev/null
READY_JSON=$(curl -s http://localhost:4566/_localstack/init/ready)
export READY_JSON
python3 <<'PY'
import json
import os

d = json.loads(os.environ["READY_JSON"])
scripts = [s for s in d.get("scripts", []) if s.get("stage") == "READY"]
assert scripts, "no ready.d init scripts registered"
bad = [s for s in scripts if s.get("state") != "SUCCESSFUL"]
assert not bad, f"init scripts not successful: {bad}"
PY
