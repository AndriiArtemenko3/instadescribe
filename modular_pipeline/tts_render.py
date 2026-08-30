"""
TTS render + AD mix utilities for the InstaDescribe server.

Extracted from tts_test.py / tts_test2.py. The CLI scripts stay for
ad-hoc experiments; the server imports from here.

Public functions:
  render_line(text, voice, out_path)         → mp3 bytes on disk
  normalise_audio(in_path, out_path)         → loudnorm-normalised mp3
  measure_gap_lufs(video, start, end)        → background LUFS in a window
  build_filter_complex(ad_blocks)            → ffmpeg filter graph
  export_with_ad(video, blocks, out_path)    → final mp4 with AD overlay
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

TARGET_LUFS = -23.0  # EBU R128 broadcast standard
DUCK_THRESHOLD = -35.0  # gaps louder than this get ducking
DUCK_LEVEL = 0.30  # 30% volume of the source during narration
AD_GAIN = 1.6  # lift the (already loudnorm'd) AD voice over the bed
SILENCE_FLOOR = -60.0  # treat <= this as silence
FFPROBE_TIMEOUT_SECS = 30
SHORT_AUDIO_TIMEOUT_SECS = 300
FULL_MEDIA_TIMEOUT_SECS = 10800


@dataclass
class AdBlock:
    """One narration block to overlay on the video."""

    scene_id: str
    start_secs: float  # where the TTS should begin in the mix
    text: str
    voice: str  # openai voice name lowercased: onyx/nova/alloy/shimmer
    tts_path: Path  # where the rendered (and normalised) TTS file lives
    tts_duration_secs: float  # measured from the rendered file
    background_lufs: float  # measured between start and start+tts_duration
    apply_duck: bool


def render_line(text: str, voice: str, out_path: Path) -> Path:
    """Render a single AD line to mp3 via the configured TTS provider (OpenAI
    tts-1-hd by default; Kokoro when TTS_PROVIDER=local). Writes to out_path."""
    from providers import get_tts_provider

    return get_tts_provider().synthesize(text=text, voice=voice, out_path=out_path)


def get_duration(path: Path) -> float:
    """Audio/video duration in seconds via ffprobe."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=FFPROBE_TIMEOUT_SECS,
    )
    return float(result.stdout.strip())


def video_codec(path: Path) -> str:
    """Return the video stream's codec name (e.g. 'h264', 'av1', 'hevc')."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=FFPROBE_TIMEOUT_SECS,
        )
        return result.stdout.strip().lower()
    except Exception:
        return ""


def has_audio_stream(path: Path) -> bool:
    """Return whether ffprobe finds a real source audio stream."""

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=index",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=FFPROBE_TIMEOUT_SECS,
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


# Codecs that QuickTime / Safari / most NLEs decode without re-wrap. Anything
# outside this set (av1, vp9, hevc-in-some-tools) gets transcoded to h264.
COMPAT_VIDEO_CODECS = {"h264", "avc1"}


def _video_output_args(source: Path) -> list[str]:
    """Pick ffmpeg `-c:v` args — copy when source is QuickTime-friendly, else
    transcode to baseline-compatible h264 + yuv420p."""
    if video_codec(source) in COMPAT_VIDEO_CODECS:
        return ["-c:v", "copy"]
    return [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
    ]


def normalise_audio(src: Path, dst: Path, target_lufs: float = TARGET_LUFS) -> Path:
    """Two-pass EBU R128 loudnorm. Returns dst."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    pass1 = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-af",
            f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11:print_format=json",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=SHORT_AUDIO_TIMEOUT_SECS,
    )
    stderr = pass1.stderr
    j_start, j_end = stderr.rfind("{"), stderr.rfind("}") + 1
    if j_start == -1 or j_end == 0:
        raise RuntimeError(f"loudnorm pass 1 failed for {src}")
    stats = json.loads(stderr[j_start:j_end])
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-af",
            (
                f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11"
                f":measured_I={stats['input_i']}"
                f":measured_TP={stats['input_tp']}"
                f":measured_LRA={stats['input_lra']}"
                f":measured_thresh={stats['input_thresh']}"
                f":offset={stats['target_offset']}"
                f":linear=true:print_format=summary"
            ),
            str(dst),
        ],
        check=True,
        capture_output=True,
        timeout=SHORT_AUDIO_TIMEOUT_SECS,
    )
    return dst


MIN_SPEED = 0.5
MAX_SPEED = 2.5


def clamp_speed(speed: float | int | str | None) -> float:
    """Coerce + clamp a speed coefficient. 1.0 means no change."""
    try:
        v = float(speed) if speed is not None else 1.0
    except (TypeError, ValueError):
        return 1.0
    return max(MIN_SPEED, min(MAX_SPEED, v))


def adjust_speed(src: Path, dst: Path, speed: float) -> Path:
    """ffmpeg atempo — change tempo without changing pitch.
    speed=1.0 is a no-op (just copies)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    s = clamp_speed(speed)
    if abs(s - 1.0) < 0.01:
        if src.resolve() != dst.resolve():
            shutil.copy2(str(src), str(dst))
        return dst
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(src), "-filter:a", f"atempo={s}", str(dst)],
        check=True,
        capture_output=True,
        timeout=SHORT_AUDIO_TIMEOUT_SECS,
    )
    return dst


def measure_gap_lufs(video: Path, start: float, end: float) -> float:
    """Integrated LUFS of the video's audio between start..end, capped at SILENCE_FLOOR."""
    duration = max(0.05, end - start)
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            str(start),
            "-t",
            str(duration),
            "-i",
            str(video),
            "-af",
            "ebur128=peak=true",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=SHORT_AUDIO_TIMEOUT_SECS,
    )
    match = re.search(r"Summary:.*?I:\s+([-\d.]+|-inf)\s+LUFS", result.stderr, re.DOTALL)
    if not match:
        return SILENCE_FLOOR
    raw = match.group(1)
    return SILENCE_FLOOR if raw == "-inf" else max(float(raw), SILENCE_FLOOR)


def build_filter_complex(
    blocks: list[AdBlock], *, bed_input_index: int = 0, tts_input_offset: int = 1
) -> tuple[str, str]:
    """
    Build ffmpeg filter_complex that lays every AD block over [0:a] in one mix.
    Returns (filter_string, output_label).
    By default input 0 supplies the source audio and inputs 1..N are TTS.
    Callers can instead point ``bed_input_index`` at a generated silent bed
    and shift ``tts_input_offset`` when a valid source video has no audio.

    The background (video audio) ducks DOWN to DUCK_LEVEL inside each AD window
    and plays at full level everywhere else; each AD voice is delayed to its
    start and lifted by AD_GAIN so the loudnorm'd narration sits clearly above
    the bed. Every stream is summed by a single amix with normalize=0. The old
    chained amix used the default normalize=1, which averaged each pair and
    halved the AD on every step (~-6 dB), leaving the narration inaudible. A
    final alimiter catches any peaks the summation pushes past full scale.
    """
    parts: list[str] = []

    # 1) Background bed: one timeline-gated volume drop per ducked AD window.
    #    Each `enable` fires only inside its own window (volume is timeline-
    #    capable), so outside every window the bed passes through untouched.
    duck_chain = "".join(
        f"volume={DUCK_LEVEL}:enable='between(t,{b.start_secs},"
        f"{b.start_secs + b.tts_duration_secs})',"
        for b in blocks
        if b.apply_duck
    )
    parts.append(f"[{bed_input_index}:a]{duck_chain}aresample=async=1[bed]")

    # 2) Each AD voice: delay to its start, then lift over the bed.
    mix_labels = ["[bed]"]
    for i, b in enumerate(blocks):
        delay_ms = int(b.start_secs * 1000)
        ad = f"[ad{i}]"
        parts.append(f"[{i + tts_input_offset}:a]adelay={delay_ms}|{delay_ms},volume={AD_GAIN}{ad}")
        mix_labels.append(ad)

    # 3) Sum (normalize=0 — do NOT average) so the AD keeps its level, then
    #    limit peaks to avoid clipping from the summation.
    n = len(mix_labels)
    parts.append(
        f"{''.join(mix_labels)}amix=inputs={n}:normalize=0:"
        f"duration=first:dropout_transition=0[mixed]"
    )
    parts.append("[mixed]alimiter=limit=0.95[aout]")

    return ";".join(parts), "[aout]"


def export_with_ad(
    video: Path,
    blocks: list[AdBlock],
    out_path: Path,
) -> Path:
    """Run ffmpeg to mix all AD blocks into a single mp4. Returns out_path.
    Always emits a QuickTime-friendly h264 stream when the source codec
    isn't already h264 (e.g. AV1 from YouTube)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    video_args = _video_output_args(video)
    source_has_audio = has_audio_stream(video)

    if not blocks:
        # No active scenes to narrate; remux (or transcode if needed) the source.
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(video),
        ]
        if not source_has_audio:
            cmd += [
                "-f",
                "lavfi",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000",
            ]
        cmd += [
            "-map",
            "0:v:0",
            "-map",
            "0:a:0" if source_has_audio else "1:a:0",
            *video_args,
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            "-shortest",
            str(out_path),
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=FULL_MEDIA_TIMEOUT_SECS,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg passthrough failed: {result.stderr[-2000:]}")
        return out_path

    bed_input_index = 0 if source_has_audio else 1
    tts_input_offset = 1 if source_has_audio else 2
    filter_str, final_label = build_filter_complex(
        blocks,
        bed_input_index=bed_input_index,
        tts_input_offset=tts_input_offset,
    )
    cmd = ["ffmpeg", "-y", "-i", str(video)]
    if not source_has_audio:
        cmd += [
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
        ]
    for b in blocks:
        cmd += ["-i", str(b.tts_path)]
    cmd += [
        "-filter_complex",
        filter_str,
        "-map",
        "0:v",
        "-map",
        final_label,
        *video_args,
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        "-shortest",
        str(out_path),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=FULL_MEDIA_TIMEOUT_SECS,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg mix failed: {result.stderr[-2000:]}")
    return out_path
