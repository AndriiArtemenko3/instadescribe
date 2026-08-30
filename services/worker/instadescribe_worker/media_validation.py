"""ffprobe validation (B6, hardened by G5.1 C3): machine-readable JSON,
before any model work.

Defense in depth mirroring the API-side pairing rule: the stored filename
EXTENSION must pair exactly with the stored content type, and the probed
container must match the tokens of THAT extension — each value being
individually allowlisted is not enough (a `.webm` name with `video/mp4` and
an MP4 container is inconsistent, not acceptable). The probed duration is
returned as the AUTHORITATIVE value; the client-declared duration is only an
untrusted upload hint.
"""

import json
import subprocess
from pathlib import Path

from instadescribe_contracts.settings import EXTENSION_CONTENT_TYPES

from instadescribe_worker.failures import FailureCode, JobFailure

# format_name is a comma-separated list; require an intersection with the
# tokens of the EXTENSION-specific container family.
_EXTENSION_FORMAT_TOKENS = {
    ".mp4": {"mp4", "mov", "m4a", "3gp", "3g2", "mj2"},
    ".mov": {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"},
    ".webm": {"webm", "matroska"},
}


def validate_media(
    path: Path, content_type: str, max_duration_secs: int, *, source_name: str
) -> float:
    """Returns the probed (authoritative) duration in seconds; raises
    JobFailure otherwise. `source_name` is the persisted object key/filename
    whose extension anchors the pairing policy."""
    extension = "." + source_name.rpartition(".")[2].lower() if "." in source_name else ""
    expected_type = EXTENSION_CONTENT_TYPES.get(extension)
    if expected_type is None:
        raise JobFailure(FailureCode.INVALID_SETTINGS, "stored filename extension is not supported")
    if (content_type or "").lower() != expected_type:
        raise JobFailure(
            FailureCode.INVALID_SETTINGS,
            "stored content type does not pair with the stored filename extension",
        )
    expected_tokens = _EXTENSION_FORMAT_TOKENS[extension]
    try:
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        raise JobFailure(FailureCode.INVALID_MEDIA, "media probe timed out") from None
    if probe.returncode != 0:
        raise JobFailure(FailureCode.INVALID_MEDIA, "media is not a readable video file")
    try:
        data = json.loads(probe.stdout)
    except json.JSONDecodeError:
        raise JobFailure(FailureCode.INVALID_MEDIA, "media probe output unreadable") from None

    streams = data.get("streams", [])
    if not any(s.get("codec_type") == "video" for s in streams):
        raise JobFailure(FailureCode.INVALID_MEDIA, "no video stream present")

    try:
        duration = float(data.get("format", {}).get("duration", ""))
    except (TypeError, ValueError):
        raise JobFailure(FailureCode.INVALID_MEDIA, "media duration unreadable") from None
    if not duration > 0:
        raise JobFailure(FailureCode.INVALID_MEDIA, "media duration must be positive")
    if duration > max_duration_secs:
        raise JobFailure(
            FailureCode.INVALID_MEDIA,
            f"media exceeds the {max_duration_secs}s portfolio limit",
        )

    format_tokens = set((data.get("format", {}).get("format_name") or "").split(","))
    if not (format_tokens & expected_tokens):
        raise JobFailure(
            FailureCode.INVALID_MEDIA,
            "container format does not match the stored filename extension",
        )
    return duration
