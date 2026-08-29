"""One-line InstaDescribe worker JSON structured logs.

Only fields passed explicitly by call sites are emitted; the worker never
logs the portfolio token/digest, AWS credentials, DSN, presigned URLs,
raw queue bodies, custom prompts, settings JSON, source media, raw
tracebacks or unbounded subprocess output.
"""

import json
import sys
from datetime import UTC, datetime


def log(event: str, *, level: str = "info", **fields) -> None:
    record = {
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "level": level,
        "service": "instadescribe-worker",
        "event": event,
    }
    for key, value in fields.items():
        if value is not None:
            record[key] = str(value) if not isinstance(value, int | float | bool) else value
    sys.stdout.write(json.dumps(record) + "\n")
    sys.stdout.flush()
