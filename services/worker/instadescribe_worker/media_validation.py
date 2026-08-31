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
import os
import shutil
import subprocess
import tempfile
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
_EXTENSION_DEMUXERS = {".mp4": "mov", ".mov": "mov", ".webm": "matroska,webm"}
_MAX_PROBE_BYTES = 1_000_000
_MAX_STREAMS = 32
_MAX_DIMENSION = 8_192
_MAX_FRAME_PIXELS = 33_177_600  # 8K UHD, before bounded keyframe scaling.


def _probe_environment() -> dict[str, str]:
    """No application credentials or host HOME reach the media parser."""

    return {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
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
    probe_environment = _probe_environment()
    probe_executable = shutil.which("ffprobe", path=probe_environment["PATH"])
    if probe_executable is None:
        raise JobFailure(FailureCode.PIPELINE_FAILED, "media probe is unavailable")
    try:
        with tempfile.TemporaryFile() as output:
            probe = subprocess.run(
                [
                    probe_executable,
                    "-max_alloc",
                    "67108864",
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-max_streams",
                    str(_MAX_STREAMS),
                    "-f",
                    _EXTENSION_DEMUXERS[extension],
                    *(
                        ["-enable_drefs", "0", "-use_absolute_path", "0"]
                        if extension in {".mp4", ".mov"}
                        else []
                    ),
                    "-print_format",
                    "json",
                    "-show_entries",
                    "format=duration,format_name:stream=codec_type,width,height",
                    str(path),
                ],
                stdout=output,
                stderr=subprocess.DEVNULL,
                env=probe_environment,
                timeout=60,
            )
            output.seek(0)
            probe_body = output.read(_MAX_PROBE_BYTES + 1)
    except OSError:
        raise JobFailure(FailureCode.PIPELINE_FAILED, "media probe is unavailable") from None
    except subprocess.TimeoutExpired:
        raise JobFailure(FailureCode.INVALID_MEDIA, "media probe timed out") from None
    if probe.returncode != 0:
        raise JobFailure(
            FailureCode.INVALID_MEDIA,
            "media container is not a readable video file",
        )
    if len(probe_body) > _MAX_PROBE_BYTES:
        raise JobFailure(FailureCode.INVALID_MEDIA, "media probe output exceeds safety bounds")
    try:
        data = json.loads(probe_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise JobFailure(FailureCode.INVALID_MEDIA, "media probe output unreadable") from None
    if not isinstance(data, dict) or not isinstance(data.get("format"), dict):
        raise JobFailure(FailureCode.INVALID_MEDIA, "media probe output unreadable")

    streams = data.get("streams", [])
    if (
        not isinstance(streams, list)
        or len(streams) > _MAX_STREAMS
        or any(not isinstance(stream, dict) for stream in streams)
    ):
        raise JobFailure(FailureCode.INVALID_MEDIA, "media stream count exceeds safety bounds")
    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    if not video_streams:
        raise JobFailure(FailureCode.INVALID_MEDIA, "no video stream present")
    for stream in video_streams:
        width, height = stream.get("width"), stream.get("height")
        if (
            isinstance(width, bool)
            or isinstance(height, bool)
            or not isinstance(width, int)
            or not isinstance(height, int)
            or width <= 0
            or height <= 0
            or width > _MAX_DIMENSION
            or height > _MAX_DIMENSION
            or width * height > _MAX_FRAME_PIXELS
        ):
            raise JobFailure(FailureCode.INVALID_MEDIA, "video dimensions exceed safety bounds")

    try:
        duration = float(data["format"].get("duration", ""))
    except (TypeError, ValueError):
        raise JobFailure(FailureCode.INVALID_MEDIA, "media duration unreadable") from None
    if not duration > 0:
        raise JobFailure(FailureCode.INVALID_MEDIA, "media duration must be positive")
    if duration > max_duration_secs:
        raise JobFailure(
            FailureCode.INVALID_MEDIA,
            f"media exceeds the {max_duration_secs}s portfolio limit",
        )

    format_name = data["format"].get("format_name")
    if not isinstance(format_name, str):
        raise JobFailure(FailureCode.INVALID_MEDIA, "media container format is unreadable")
    format_tokens = set(format_name.split(","))
    if not (format_tokens & expected_tokens):
        raise JobFailure(
            FailureCode.INVALID_MEDIA,
            "container format does not match the stored filename extension",
        )
    return duration
