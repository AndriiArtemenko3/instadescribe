"""Bounded local media fingerprint and metadata helpers."""

from __future__ import annotations

import hashlib
import json
import math
import mimetypes
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any

_MAX_PROBE_BYTES = 1_000_000
_MAX_STREAMS = 32
_MAX_DIMENSION = 8_192
_MAX_FRAME_PIXELS = 33_177_600
_MAX_DURATION_MS = 2**63 - 1
_VIDEO_DEMUXERS = {".mp4": "mov", ".mov": "mov", ".webm": "matroska,webm"}


@dataclass(frozen=True, slots=True)
class MediaMetadata:
    path: str
    content_sha256: str
    size_bytes: int
    media_type: str
    container: str | None = None
    duration_ms: int | None = None
    width: int | None = None
    height: int | None = None
    frame_rate: float | None = None
    video_streams: int = 0
    audio_streams: int = 0
    perceptual_hash: str | None = None
    probe_available: bool = False
    warnings: tuple[str, ...] = ()


def _local_file(path: Path) -> Path:
    resolved = path.expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"media path must be a regular file: {resolved}")
    return resolved


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Hash a file with bounded memory."""

    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    resolved = _local_file(path)
    digest = hashlib.sha256()
    with resolved.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def perceptual_hash(path: Path, *, hash_size: int = 8, high_frequency_factor: int = 4) -> str:
    """Compute a DCT pHash when Pillow is installed.

    Pillow remains optional; importing this module never loads it. The function raises
    a clear RuntimeError when the media extra is unavailable.
    """

    if hash_size <= 1 or high_frequency_factor <= 0:
        raise ValueError("hash_size must exceed one and high_frequency_factor must be positive")
    try:
        from PIL import Image
    except ImportError as error:  # pragma: no cover - exercised in minimal installations
        raise RuntimeError("perceptual_hash requires the optional Pillow dependency") from error

    resolved = _local_file(path)
    sample_size = hash_size * high_frequency_factor
    with Image.open(resolved) as image:
        resized = image.convert("L").resize((sample_size, sample_size))
        if hasattr(resized, "get_flattened_data"):
            pixels = list(resized.get_flattened_data())
        else:  # Pillow < 12.1
            pixels = list(resized.getdata())

    coefficients: list[float] = []
    denominator = 2 * sample_size
    for vertical_frequency in range(hash_size):
        for horizontal_frequency in range(hash_size):
            coefficient = 0.0
            for y in range(sample_size):
                vertical = math.cos(math.pi * (2 * y + 1) * vertical_frequency / denominator)
                offset = y * sample_size
                for x in range(sample_size):
                    horizontal = math.cos(
                        math.pi * (2 * x + 1) * horizontal_frequency / denominator
                    )
                    coefficient += pixels[offset + x] * horizontal * vertical
            coefficients.append(coefficient)

    threshold = median(coefficients[1:])
    bits = 0
    for coefficient in coefficients:
        bits = (bits << 1) | int(coefficient > threshold)
    width = math.ceil(hash_size * hash_size / 4)
    return f"{bits:0{width}x}"


def _parse_fraction(value: str | None) -> float | None:
    if not value or value in {"0/0", "N/A"}:
        return None
    numerator, separator, denominator = value.partition("/")
    try:
        if separator:
            divisor = float(denominator)
            return float(numerator) / divisor if divisor else None
        return float(value)
    except ValueError:
        return None


def _probe(path: Path, executable: str, timeout_seconds: float) -> dict[str, Any]:
    extension = path.suffix.lower()
    demuxer = _VIDEO_DEMUXERS.get(extension)
    if demuxer is None:
        raise ValueError("ffprobe accepts only MP4, MOV or WebM containers")
    environment = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }
    with tempfile.TemporaryFile() as output:
        subprocess.run(
            [
                executable,
                "-max_alloc",
                "67108864",
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-max_streams",
                str(_MAX_STREAMS),
                "-f",
                demuxer,
                *(
                    ["-enable_drefs", "0", "-use_absolute_path", "0"]
                    if extension in {".mp4", ".mov"}
                    else []
                ),
                "-show_entries",
                "format=duration,format_name:stream=codec_type,width,height,avg_frame_rate",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            stdout=output,
            stderr=subprocess.DEVNULL,
            env=environment,
            timeout=timeout_seconds,
        )
        output.seek(0)
        body = output.read(_MAX_PROBE_BYTES + 1)
    if len(body) > _MAX_PROBE_BYTES:
        raise ValueError("ffprobe output exceeds safety bounds")
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise ValueError("ffprobe output must be an object")
    return parsed


def inspect_media(
    path: Path,
    *,
    ffprobe_executable: str | None = None,
    timeout_seconds: float = 15,
    include_perceptual_hash: bool = True,
) -> MediaMetadata:
    """Inspect a local file without a shell or a network-capable URL input."""

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    resolved = _local_file(path)
    media_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
    warnings: list[str] = []
    image_hash: str | None = None
    if include_perceptual_hash and media_type.startswith("image/"):
        try:
            image_hash = perceptual_hash(resolved)
        except (RuntimeError, OSError, ValueError) as error:
            warnings.append(f"perceptualHashUnavailable:{type(error).__name__}")

    executable = ffprobe_executable or shutil.which("ffprobe")
    if executable is None:
        warnings.append("ffprobeUnavailable")
        return MediaMetadata(
            path=str(resolved),
            content_sha256=sha256_file(resolved),
            size_bytes=resolved.stat().st_size,
            media_type=media_type,
            perceptual_hash=image_hash,
            warnings=tuple(warnings),
        )

    try:
        payload = _probe(resolved, executable, timeout_seconds)
    except (
        json.JSONDecodeError,
        OSError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        ValueError,
    ) as error:
        warnings.append(f"ffprobeFailed:{type(error).__name__}")
        payload = {}

    streams = payload.get("streams") if isinstance(payload.get("streams"), list) else []
    if len(streams) > _MAX_STREAMS or any(not isinstance(stream, dict) for stream in streams):
        warnings.append("streamMetadataUnsafe")
        streams = []
    video = next(
        (
            stream
            for stream in streams
            if isinstance(stream, dict) and stream.get("codec_type") == "video"
        ),
        None,
    )
    video_streams = sum(
        isinstance(stream, dict) and stream.get("codec_type") == "video" for stream in streams
    )
    audio_streams = sum(
        isinstance(stream, dict) and stream.get("codec_type") == "audio" for stream in streams
    )
    raw_format = payload.get("format") if isinstance(payload.get("format"), dict) else {}
    duration: int | None = None
    try:
        if raw_format.get("duration") not in {None, "N/A"}:
            duration_seconds = float(raw_format["duration"])
            duration_milliseconds = duration_seconds * 1000
            if (
                not math.isfinite(duration_seconds)
                or not math.isfinite(duration_milliseconds)
                or duration_milliseconds > _MAX_DURATION_MS
            ):
                raise ValueError("duration is outside finite storage bounds")
            duration = max(0, round(duration_milliseconds))
    except (OverflowError, TypeError, ValueError):
        warnings.append("durationInvalid")

    def positive_int(value: object) -> int | None:
        return (
            value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None
        )

    width = positive_int(video.get("width")) if video else None
    height = positive_int(video.get("height")) if video else None
    if (
        width is not None
        and height is not None
        and (
            width > _MAX_DIMENSION or height > _MAX_DIMENSION or width * height > _MAX_FRAME_PIXELS
        )
    ):
        warnings.append("videoDimensionsUnsafe")
        width = None
        height = None

    return MediaMetadata(
        path=str(resolved),
        content_sha256=sha256_file(resolved),
        size_bytes=resolved.stat().st_size,
        media_type=media_type,
        container=raw_format.get("format_name")
        if isinstance(raw_format.get("format_name"), str)
        else None,
        duration_ms=duration,
        width=width,
        height=height,
        frame_rate=_parse_fraction(video.get("avg_frame_rate")) if video else None,
        video_streams=video_streams,
        audio_streams=audio_streams,
        perceptual_hash=image_hash,
        probe_available=bool(payload),
        warnings=tuple(warnings),
    )


def fingerprint_media(path: Path) -> MediaMetadata:
    """Return environment-independent metadata for deterministic fake runs."""

    resolved = _local_file(path)
    return MediaMetadata(
        path=str(resolved),
        content_sha256=sha256_file(resolved),
        size_bytes=resolved.stat().st_size,
        media_type=mimetypes.guess_type(resolved.name)[0] or "application/octet-stream",
    )
