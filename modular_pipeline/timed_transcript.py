"""Strict, dependency-free WebVTT/SRT ingestion for provided transcripts."""

from __future__ import annotations

import math
import re
from typing import Any, Literal

TranscriptFormat = Literal["vtt", "srt"]
MAX_TRANSCRIPT_BYTES = 10 * 1024 * 1024

_VTT_TIME = re.compile(r"^(?:(?P<h>\d{2,}):)?(?P<m>\d{2}):(?P<s>\d{2})\.(?P<ms>\d{3})$")
_SRT_TIME = re.compile(r"^(?P<h>\d{2,}):(?P<m>\d{2}):(?P<s>\d{2}),(?P<ms>\d{3})$")


class TimedTranscriptError(ValueError):
    """Safe validation error; never contains transcript text."""


def _seconds(value: str, transcript_format: TranscriptFormat) -> float:
    match = (_VTT_TIME if transcript_format == "vtt" else _SRT_TIME).fullmatch(value)
    if match is None:
        raise TimedTranscriptError("transcript timestamp is invalid")
    hours = int(match.group("h") or 0)
    minutes = int(match.group("m"))
    seconds = int(match.group("s"))
    milliseconds = int(match.group("ms"))
    if minutes > 59 or seconds > 59:
        raise TimedTranscriptError("transcript timestamp is invalid")
    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000


def _normalise_lines(text: str) -> list[str]:
    if "\x00" in text:
        raise TimedTranscriptError("transcript contains a null byte")
    return text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _blocks(lines: list[str]) -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.strip():
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def _parse_vtt(text: str) -> list[tuple[float, float, str]]:
    lines = _normalise_lines(text)
    if not lines or not lines[0].startswith("WEBVTT"):
        raise TimedTranscriptError("WebVTT header is missing")
    cues: list[tuple[float, float, str]] = []
    for block in _blocks(lines[1:]):
        if block[0].startswith("NOTE"):
            continue
        if block[0].startswith(("STYLE", "REGION")):
            raise TimedTranscriptError("WebVTT style and region blocks are not supported")
        timing_index = 0 if "-->" in block[0] else 1
        if timing_index >= len(block) or "-->" not in block[timing_index]:
            raise TimedTranscriptError("WebVTT cue timing is missing")
        left, right = (part.strip() for part in block[timing_index].split("-->", 1))
        right_time = right.split(maxsplit=1)[0]
        body = " ".join(part.strip() for part in block[timing_index + 1 :] if part.strip())
        if not body:
            raise TimedTranscriptError("transcript cue text is empty")
        cues.append((_seconds(left, "vtt"), _seconds(right_time, "vtt"), body))
    return cues


def _parse_srt(text: str) -> list[tuple[float, float, str]]:
    cues: list[tuple[float, float, str]] = []
    for block in _blocks(_normalise_lines(text)):
        timing_index = 0
        if "-->" not in block[0]:
            if not block[0].strip().isdigit():
                raise TimedTranscriptError("SRT cue index is invalid")
            timing_index = 1
        if timing_index >= len(block) or "-->" not in block[timing_index]:
            raise TimedTranscriptError("SRT cue timing is missing")
        left, right = (part.strip() for part in block[timing_index].split("-->", 1))
        body = " ".join(part.strip() for part in block[timing_index + 1 :] if part.strip())
        if not body:
            raise TimedTranscriptError("transcript cue text is empty")
        cues.append((_seconds(left, "srt"), _seconds(right, "srt"), body))
    return cues


def parse_timed_transcript(
    text: str,
    transcript_format: TranscriptFormat,
    *,
    video_duration_seconds: float,
) -> list[dict[str, Any]]:
    """Validate and normalise timed cues to canonical transcript JSON.

    Overlapping cues are folded into one dialogue span so downstream gap
    calculation cannot accidentally place narration between simultaneous cues.
    """
    if transcript_format not in {"vtt", "srt"}:
        raise TimedTranscriptError("transcript format is unsupported")
    if not math.isfinite(video_duration_seconds) or video_duration_seconds <= 0:
        raise TimedTranscriptError("video duration is invalid")
    cues = _parse_vtt(text) if transcript_format == "vtt" else _parse_srt(text)
    if not cues:
        raise TimedTranscriptError("transcript has no cues")

    validated: list[tuple[float, float, str]] = []
    for start, end, body in cues:
        if not all(math.isfinite(bound) and bound >= 0 for bound in (start, end)):
            raise TimedTranscriptError("transcript cue timestamp is invalid")
        if end <= start:
            raise TimedTranscriptError("transcript cue end must exceed start")
        if end > video_duration_seconds + 0.05:
            raise TimedTranscriptError("transcript cue exceeds video duration")
        validated.append((start, min(end, video_duration_seconds), body))

    merged: list[dict[str, Any]] = []
    for start, end, body in sorted(validated, key=lambda cue: (cue[0], cue[1])):
        if merged and start <= merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], end)
            merged[-1]["text"] = f"{merged[-1]['text']} {body}"
            continue
        merged.append({"text": body, "start": start, "end": end, "words": []})
    return merged


def parse_timed_transcript_bytes(
    body: bytes,
    transcript_format: TranscriptFormat,
    *,
    video_duration_seconds: float,
) -> list[dict[str, Any]]:
    if len(body) > MAX_TRANSCRIPT_BYTES:
        raise TimedTranscriptError("transcript exceeds the 10 MiB limit")
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise TimedTranscriptError("transcript must be UTF-8") from None
    return parse_timed_transcript(
        text,
        transcript_format,
        video_duration_seconds=video_duration_seconds,
    )


def transcript_audio_events(
    transcript: list[dict[str, Any]], *, video_duration_seconds: float
) -> list[dict[str, Any]]:
    """Build a complete dialogue/silence timeline from canonical cues."""
    events: list[dict[str, Any]] = []
    cursor = 0.0
    for cue in transcript:
        start = float(cue["start"])
        end = float(cue["end"])
        if start > cursor:
            events.append(
                {
                    "start": cursor,
                    "end": start,
                    "event_type": "silence",
                    "confidence": 1.0,
                    "transcript": "",
                }
            )
        events.append(
            {
                "start": start,
                "end": end,
                "event_type": "dialogue",
                "confidence": 1.0,
                "transcript": cue["text"],
            }
        )
        cursor = end
    if cursor < video_duration_seconds:
        events.append(
            {
                "start": cursor,
                "end": video_duration_seconds,
                "event_type": "silence",
                "confidence": 1.0,
                "transcript": "",
            }
        )
    return events


def transcript_ad_gaps(
    transcript: list[dict[str, Any]], *, video_duration_seconds: float
) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for event in transcript_audio_events(transcript, video_duration_seconds=video_duration_seconds):
        if event["event_type"] != "silence":
            continue
        start = float(event["start"])
        end = float(event["end"])
        duration = end - start
        gaps.append(
            {
                "start": round(start, 3),
                "end": round(end, 3),
                "duration_seconds": round(duration, 3),
                "midpoint": round((start + end) / 2, 3),
                "recommended_ad_start": round(start + 0.25, 3),
                "recommended": duration >= 2.0,
            }
        )
    return gaps
