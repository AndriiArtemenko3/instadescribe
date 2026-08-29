"""audio_whisperx_pipeline.py

4-stage Descript-style audio pipeline using faster-whisper and silero-vad directly.
No whisperx dependency.

Stage 1 — VAD: detect speech/silence spans via silero-vad
Stage 2 — Transcription: faster-whisper medium model
Stage 3 — Word timestamps: faster-whisper built-in word_timestamps=True;
           fallback to even time-distribution per word if per-word data missing
Stage 4 — Synthesis: build AudioEvent list and AD placement gaps

Public API (signatures match audio.py):
    load_audio_events(video_path, frames) -> List[AudioEvent]
    calculate_ad_gaps(events)             -> List[Dict]

Additional:
    load_transcript(video_path, frames)   -> List[TranscriptSegment]

pipeline.py handles all file I/O; this module returns data only.
"""

import json
import logging
import os
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from frames import FrameItem

load_dotenv()

_LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

_CANONICAL_CACHE_DIR = Path.home() / ".cache" / "instadescribe"
_LEGACY_CACHE_DIR = Path.home() / ".cache" / "instascribe"
# Reuse already-downloaded v0.1 weights, but all fresh installations use the
# canonical product namespace.
DEFAULT_CACHE_DIR = (
    _LEGACY_CACHE_DIR
    if _LEGACY_CACHE_DIR.exists() and not _CANONICAL_CACHE_DIR.exists()
    else _CANONICAL_CACHE_DIR
)
WHISPER_MODEL_SIZE = "medium"
WHISPER_LANGUAGE = os.environ.get("JOB_WHISPER_LANGUAGE") or None  # None → auto-detect
VAD_THRESHOLD = 0.500  # silero-vad onset probability threshold
MIN_SILENCE_MS = 300  # min silence duration (ms) between speech spans
MIN_AD_GAP_SECONDS = 2.0
SAMPLE_RATE = 16000

_FASTER_WHISPER_WORD_ALIGNMENT_INDEX_ERROR = (
    "boolean index did not match indexed array along dimension 0; "
    "dimension is 0 but corresponding boolean dimension is 1"
)
_TRANSCRIPTION_ALIGNMENT_FALLBACK_MARKER = "INSTADESCRIBE_WORD_ALIGNMENT_FALLBACK"
_TRANSCRIPTION_STDERR_LIMIT = 2000

# Module-level word-event cache: populated by _run_pipeline; consumed by
# calculate_ad_gaps so that gap precision is word-level, not span-level.
_last_word_events: list["WordEvent"] = []

# Module-level pipeline result cache keyed by resolved video path string.
# Prevents re-running the expensive pipeline when pipeline.py calls both
# load_audio_events() and load_transcript() on the same file.
_pipeline_cache: dict[str, tuple[list["AudioEvent"], list["TranscriptSegment"]]] = {}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class WordEvent:
    word: str
    start: float
    end: float
    speaker: str | None = None


@dataclass
class TranscriptSegment:
    text: str
    start: float
    end: float
    words: list[WordEvent] = field(default_factory=list)


@dataclass
class AudioEvent:
    start: float
    end: float
    event_type: str  # "dialogue" | "silence"
    confidence: float
    transcript: str = field(default="")


# ---------------------------------------------------------------------------
# Device detection
# ---------------------------------------------------------------------------


def _get_device() -> str:
    try:
        import torch

        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
    except ImportError:
        pass
    return "cpu"


def _get_compute_type(device: str) -> str:
    # float16 only on CUDA; MPS and CPU require int8 for faster-whisper
    return "float16" if device == "cuda" else "float32"


# ---------------------------------------------------------------------------
# Audio extraction — ffmpeg subprocess only
# ---------------------------------------------------------------------------


def _load_audio_ffmpeg(video_path: Path) -> tuple[np.ndarray, Path]:
    """Extract mono 16 kHz audio via ffmpeg.

    Returns (audio_array, wav_path). wav_path is a temp WAV file the caller
    must delete after use. The numpy array is for VAD (silero); the WAV file
    is for transcription (faster-whisper).
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    wav_path = Path(tmp.name)
    tmp.close()

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "-f",
        "wav",
        str(wav_path),
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except FileNotFoundError:
        raise RuntimeError("ffmpeg not found. Install it and ensure it is on PATH.") from None

    stderr_text = result.stderr.decode(errors="replace")
    if result.returncode != 0:
        stderr_lower = stderr_text.lower()
        if "no audio" in stderr_lower or "does not contain any stream" in stderr_lower:
            wav_path.unlink(missing_ok=True)
            raise ValueError("no_audio")
        if not wav_path.exists() or wav_path.stat().st_size == 0:
            raise RuntimeError(f"ffmpeg failed (exit {result.returncode}):\n{stderr_text[:300]}")

    if not wav_path.exists() or wav_path.stat().st_size == 0:
        raise ValueError("no_audio")

    # Read WAV back as float32 PCM for silero-vad (needs numpy array)
    cmd_pcm = ["ffmpeg", "-y", "-i", str(wav_path), "-f", "f32le", "pipe:1"]
    result2 = subprocess.run(cmd_pcm, capture_output=True)
    audio = np.frombuffer(result2.stdout, dtype=np.float32).copy()
    return audio, wav_path


# ---------------------------------------------------------------------------
# Span utility
# ---------------------------------------------------------------------------


def _invert_spans(
    spans: list[dict[str, float]],
    total_duration: float,
    min_gap: float = 0.05,
) -> list[dict[str, float]]:
    """Return the complement of `spans` within [0, total_duration]."""
    if not spans:
        return [{"start": 0.0, "end": total_duration}]

    sorted_spans = sorted(spans, key=lambda s: s["start"])
    gaps: list[dict[str, float]] = []
    cursor = 0.0

    for span in sorted_spans:
        s_start = max(span["start"], 0.0)
        s_end = min(span["end"], total_duration)
        if s_start > cursor + min_gap:
            gaps.append({"start": cursor, "end": s_start})
        cursor = max(cursor, s_end)

    if cursor < total_duration - min_gap:
        gaps.append({"start": cursor, "end": total_duration})

    return gaps


# ---------------------------------------------------------------------------
# Stage 1 — VAD (silero-vad)
# ---------------------------------------------------------------------------


def _run_vad(audio: np.ndarray) -> tuple[list[dict], list[dict]]:
    """Detect speech spans using silero-vad.

    Returns (speech_spans, silence_spans) where each span is
    {"start": float_seconds, "end": float_seconds}.

    On failure, treats the entire audio as a single speech span.
    """
    total_duration = float(len(audio)) / SAMPLE_RATE

    try:
        import torch
        from silero_vad import get_speech_timestamps, load_silero_vad

        audio_tensor = torch.from_numpy(audio)
        model = load_silero_vad()

        raw = get_speech_timestamps(
            audio_tensor,
            model,
            sampling_rate=SAMPLE_RATE,
            threshold=VAD_THRESHOLD,
            min_silence_duration_ms=MIN_SILENCE_MS,
            return_seconds=True,
        )

        speech_spans: list[dict] = [
            {"start": float(s["start"]), "end": float(s["end"])} for s in raw
        ]

        if not speech_spans:
            _LOG.warning("Stage 1: VAD found no speech — treating full audio as speech")
            speech_spans = [{"start": 0.0, "end": total_duration}]

        silence_spans = _invert_spans(speech_spans, total_duration)

        _LOG.info(
            "Stage 1: %d speech spans, %d silence spans (total %.1fs)",
            len(speech_spans),
            len(silence_spans),
            total_duration,
        )
        return speech_spans, silence_spans

    except Exception as exc:
        _LOG.warning(
            "Stage 1: VAD failed (%s: %s) — treating full audio as speech",
            type(exc).__name__,
            exc,
        )
        return [{"start": 0.0, "end": total_duration}], []


# ---------------------------------------------------------------------------
# Stage 2 + 3 — Transcription with word timestamps (faster-whisper)
# ---------------------------------------------------------------------------


def _distribute_words_evenly(text: str, start: float, end: float) -> list[WordEvent]:
    """Split text into tokens and distribute time evenly.

    Used as Stage 3 fallback when faster-whisper returns no per-word data.
    """
    tokens = text.split()
    if not tokens:
        return []
    duration = end - start
    step = duration / len(tokens)
    words: list[WordEvent] = []
    for i, token in enumerate(tokens):
        w_start = start + i * step
        w_end = start + (i + 1) * step
        words.append(WordEvent(word=token, start=round(w_start, 3), end=round(w_end, 3)))
    return words


def _run_transcription(
    wav_path: Path,
    device: str,
    cache_dir: Path,
) -> list[dict]:
    """Transcribe audio with faster-whisper, returning word-aligned raw segments.

    Each entry: {"start": float, "end": float, "text": str,
                 "words": [{"word": str, "start": float, "end": float}]}

    Stage 3 (word timestamps) is built-in via word_timestamps=True.
    If a segment has no per-word data, words are evenly distributed (fallback).
    """
    from faster_whisper import WhisperModel

    model = WhisperModel(
        WHISPER_MODEL_SIZE,
        device=device,
        compute_type=_get_compute_type(device),
        cpu_threads=4,
        download_root=str(cache_dir),
    )

    segments_gen, info = model.transcribe(
        str(wav_path),
        language=WHISPER_LANGUAGE,
        beam_size=1,
        word_timestamps=True,
        vad_filter=False,  # Stage 1 already handled VAD
    )

    detected_lang = getattr(info, "language", None) or WHISPER_LANGUAGE or "en"
    _LOG.info("Stage 2+3: detected language=%s", detected_lang)

    raw_segments: list[dict] = []
    for seg in segments_gen:
        seg_start = float(seg.start)
        seg_end = float(seg.end)
        seg_text = (seg.text or "").strip()

        if seg.words:
            words = [
                {"word": w.word, "start": float(w.start), "end": float(w.end)} for w in seg.words
            ]
        else:
            # Stage 3 fallback: distribute time evenly across whitespace-split tokens
            _LOG.debug("Stage 3 fallback: no word data for segment [%.2f–%.2f]", seg_start, seg_end)
            word_events = _distribute_words_evenly(seg_text, seg_start, seg_end)
            words = [{"word": w.word, "start": w.start, "end": w.end} for w in word_events]

        raw_segments.append(
            {
                "start": seg_start,
                "end": seg_end,
                "text": seg_text,
                "words": words,
            }
        )

    _LOG.info("Stage 2+3: %d segments transcribed", len(raw_segments))
    return raw_segments


def _transcription_subprocess_script() -> str:
    """Return the isolated faster-whisper runner used by the subprocess."""
    return textwrap.dedent(f"""\
        import sys, json
        from faster_whisper import WhisperModel

        KNOWN_WORD_ALIGNMENT_INDEX_ERROR = {_FASTER_WHISPER_WORD_ALIGNMENT_INDEX_ERROR!r}
        ALIGNMENT_FALLBACK_MARKER = {_TRANSCRIPTION_ALIGNMENT_FALLBACK_MARKER!r}

        wav_path, device, cache_dir, model_size, compute_type, language_arg = sys.argv[1:]
        language = None if language_arg == "None" else language_arg

        model = WhisperModel(
            model_size, device=device, compute_type=compute_type,
            cpu_threads=4, download_root=cache_dir,
        )
        def transcribe(word_timestamps):
            segments_gen, _info = model.transcribe(
                wav_path, language=language, beam_size=1,
                word_timestamps=word_timestamps, vad_filter=False,
            )

            results = []
            for seg in segments_gen:
                seg_start = float(seg.start)
                seg_end   = float(seg.end)
                seg_text  = (seg.text or "").strip()
                if seg.words:
                    words = [
                        {{"word": w.word, "start": float(w.start), "end": float(w.end)}}
                        for w in seg.words
                    ]
                else:
                    tokens = seg_text.split()
                    if tokens:
                        duration = seg_end - seg_start
                        step = duration / len(tokens)
                        words = [
                            {{
                                "word":  t,
                                "start": round(seg_start + i * step, 3),
                                "end":   round(seg_start + (i + 1) * step, 3),
                            }}
                            for i, t in enumerate(tokens)
                        ]
                    else:
                        words = []
                results.append({{
                    "start": seg_start, "end": seg_end,
                    "text": seg_text, "words": words,
                }})
            return results

        try:
            results = transcribe(word_timestamps=True)
        except IndexError as exc:
            if str(exc) != KNOWN_WORD_ALIGNMENT_INDEX_ERROR:
                raise
            print(ALIGNMENT_FALLBACK_MARKER, file=sys.stderr)
            results = transcribe(word_timestamps=False)

        print(json.dumps(results))
    """)


def _run_transcription_subprocess(
    wav_path: Path,
    device: str,
    cache_dir: Path,
) -> list[dict]:
    """Run faster-whisper transcription in a clean subprocess.

    Avoids OpenMP conflict between torch (loaded by silero-vad in Stage 1)
    and ctranslate2 (used by faster-whisper) on Intel Mac.

    Writes a small Python script to a temp file, runs it with sys.executable,
    reads JSON from stdout, cleans up.
    """
    script = _transcription_subprocess_script()

    tmp = tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False)
    tmp.write(script)
    tmp.close()
    script_path = Path(tmp.name)

    # KMP_DUPLICATE_LIB_OK suppresses the macOS OMP conflict when libiomp5.dylib
    # is already loaded by the parent process (via torch/numpy).
    subprocess_env = {**os.environ, "KMP_DUPLICATE_LIB_OK": "TRUE"}

    try:
        result = subprocess.run(
            [
                sys.executable,
                str(script_path),
                str(wav_path),
                device,
                str(cache_dir),
                WHISPER_MODEL_SIZE,
                _get_compute_type(device),
                str(WHISPER_LANGUAGE) if WHISPER_LANGUAGE else "None",
            ],
            capture_output=True,
            env=subprocess_env,
        )
    finally:
        script_path.unlink(missing_ok=True)

    if result.returncode != 0:
        stderr_text = _bounded_transcription_stderr(result.stderr)
        raise RuntimeError(
            f"Transcription subprocess failed (exit {result.returncode}):\n{stderr_text}"
        )

    if _TRANSCRIPTION_ALIGNMENT_FALLBACK_MARKER.encode() in result.stderr:
        _LOG.warning(
            "Stage 2+3: faster-whisper word alignment failed; "
            "used evenly distributed word timestamps"
        )

    raw_segments = json.loads(result.stdout.decode())
    _LOG.info("Stage 2+3: %d segments transcribed (subprocess)", len(raw_segments))
    return raw_segments


def _bounded_transcription_stderr(stderr: bytes) -> str:
    """Bound subprocess stderr while retaining its terminal exception."""
    text = stderr.decode(errors="replace").strip()
    if not text:
        return "<no stderr output>"
    if len(text) <= _TRANSCRIPTION_STDERR_LIMIT:
        return text

    marker = "\n...[stderr truncated; terminal output follows]...\n"
    head_size = 400
    tail_size = _TRANSCRIPTION_STDERR_LIMIT - head_size - len(marker)
    return f"{text[:head_size]}{marker}{text[-tail_size:]}"


# ---------------------------------------------------------------------------
# Stage 4a — Build TranscriptSegment list
# ---------------------------------------------------------------------------


def _build_transcript_segments(
    raw_segments: list[dict],
) -> tuple[list[WordEvent], list[TranscriptSegment]]:
    """Convert raw segment dicts into TranscriptSegment dataclasses.

    Returns (all_word_events, transcript_segments).
    """
    all_words: list[WordEvent] = []
    transcript_segments: list[TranscriptSegment] = []

    for seg in raw_segments:
        words: list[WordEvent] = []
        for w in seg.get("words", []):
            we = WordEvent(
                word=w["word"],
                start=float(w["start"]),
                end=float(w["end"]),
            )
            words.append(we)
            all_words.append(we)

        transcript_segments.append(
            TranscriptSegment(
                text=seg["text"],
                start=float(seg["start"]),
                end=float(seg["end"]),
                words=words,
            )
        )

    return all_words, transcript_segments


# ---------------------------------------------------------------------------
# Stage 4b — Build AudioEvent list
# ---------------------------------------------------------------------------


def _build_audio_events(
    speech_spans: list[dict],
    silence_spans: list[dict],
    transcript_segments: list[TranscriptSegment],
) -> list[AudioEvent]:
    events: list[AudioEvent] = []

    for span in speech_spans:
        span_start = span["start"]
        span_end = span["end"]
        # Collect text from all segments overlapping this span
        texts = [
            seg.text for seg in transcript_segments if seg.end > span_start and seg.start < span_end
        ]
        events.append(
            AudioEvent(
                start=span_start,
                end=span_end,
                event_type="dialogue",
                confidence=0.90,
                transcript=" ".join(t for t in texts if t),
            )
        )

    for span in silence_spans:
        events.append(
            AudioEvent(
                start=span["start"],
                end=span["end"],
                event_type="silence",
                confidence=1.0,
                transcript="",
            )
        )

    events.sort(key=lambda e: e.start)
    return events


# ---------------------------------------------------------------------------
# Stage 4c — Build word-level AD placement gaps
# ---------------------------------------------------------------------------


def _build_ad_gaps(word_events: list[WordEvent], total_duration: float = 0.0) -> list[dict]:
    """Derive AD placement gaps from inter-word silences >= MIN_AD_GAP_SECONDS."""
    if not word_events:
        return []

    sorted_words = sorted(word_events, key=lambda w: w.start)
    gaps: list[dict] = []

    # Leading silence — before first word
    leading = sorted_words[0].start
    if leading >= MIN_AD_GAP_SECONDS:
        gaps.append(
            {
                "start": 0.0,
                "end": round(leading, 3),
                "duration_seconds": round(leading, 3),
                "midpoint": round(leading / 2, 3),
                "recommended_ad_start": 0.25,
                "recommended": True,
            }
        )

    # Inter-word gaps
    for i in range(len(sorted_words) - 1):
        gap_start = sorted_words[i].end
        gap_end = sorted_words[i + 1].start
        duration = gap_end - gap_start

        if duration >= MIN_AD_GAP_SECONDS:
            gaps.append(
                {
                    "start": round(gap_start, 3),
                    "end": round(gap_end, 3),
                    "duration_seconds": round(duration, 3),
                    "midpoint": round((gap_start + gap_end) / 2, 3),
                    "recommended_ad_start": round(gap_start + 0.25, 3),
                    "recommended": True,
                }
            )

    # Trailing silence — after last word
    if total_duration > 0:
        trailing = total_duration - sorted_words[-1].end
        if trailing >= MIN_AD_GAP_SECONDS:
            trail_start = sorted_words[-1].end
            gaps.append(
                {
                    "start": round(trail_start, 3),
                    "end": round(total_duration, 3),
                    "duration_seconds": round(trailing, 3),
                    "midpoint": round((trail_start + total_duration) / 2, 3),
                    "recommended_ad_start": round(trail_start + 0.25, 3),
                    "recommended": True,
                }
            )

    return gaps


# ---------------------------------------------------------------------------
# Core pipeline runner
# ---------------------------------------------------------------------------


def _run_pipeline(
    video_path: Path,
    frames: list[FrameItem],
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> tuple[list[AudioEvent], list[TranscriptSegment]]:
    """Run all 4 stages; return (audio_events, transcript_segments).

    Populates _last_word_events for calculate_ad_gaps.
    Returns ([], []) if the video has no audio track.
    """
    global _last_word_events

    import os

    hf_token = os.environ.get("HF_TOKEN") or None
    if not hf_token:
        _LOG.info("HF_TOKEN not set — diarization unavailable (not used in this pipeline)")

    cache_dir.mkdir(parents=True, exist_ok=True)
    device = _get_device()
    _LOG.info("Using device: %s  compute_type: %s", device, _get_compute_type(device))

    try:
        audio, wav_path = _load_audio_ffmpeg(video_path)
    except ValueError as exc:
        if "no_audio" in str(exc):
            _LOG.warning("No audio track found in %s — skipping audio", video_path)
            _last_word_events = []
            return [], []
        raise

    _LOG.info("Audio loaded: %d samples (%.1fs)", len(audio), len(audio) / SAMPLE_RATE)

    try:
        # Stage 1 — VAD
        speech_spans, silence_spans = _run_vad(audio)

        # Stage 2 + 3 — Transcription with word timestamps
        raw_segments = _run_transcription_subprocess(wav_path, device, cache_dir)
    finally:
        wav_path.unlink(missing_ok=True)

    # Stage 4a — Dataclass conversion
    all_words, transcript_segments = _build_transcript_segments(raw_segments)
    _last_word_events = sorted(all_words, key=lambda w: w.start)

    # Stage 4b — AudioEvent list
    audio_events = _build_audio_events(speech_spans, silence_spans, transcript_segments)

    _LOG.info(
        "Pipeline complete: %d events, %d transcript segments, %d word events",
        len(audio_events),
        len(transcript_segments),
        len(_last_word_events),
    )
    return audio_events, transcript_segments


def _run_pipeline_cached(
    video_path: Path,
    frames: list[FrameItem],
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> tuple[list[AudioEvent], list[TranscriptSegment]]:
    """Cached wrapper around _run_pipeline.

    Prevents the pipeline from running twice when pipeline.py calls both
    load_audio_events() and load_transcript() on the same file.
    """
    key = str(video_path.resolve())
    if key not in _pipeline_cache:
        _pipeline_cache[key] = _run_pipeline(video_path, frames, cache_dir)
    return _pipeline_cache[key]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_audio_events(video_path: Path, frames: list[FrameItem]) -> list[AudioEvent]:
    """Extract audio from video_path and return classified AudioEvent list.

    Returns an empty list if the video has no audio track or does not exist.
    The caller (pipeline.py) should handle the empty case gracefully.
    """
    if not video_path.exists():
        _LOG.warning("video file not found at %s — skipping audio", video_path)
        return []

    events, _ = _run_pipeline_cached(video_path, frames)
    return events


def load_transcript(video_path: Path, frames: list[FrameItem]) -> list[TranscriptSegment]:
    """Return word-aligned transcript for the video.

    Returns an empty list if the video has no audio track or does not exist.
    """
    if not video_path.exists():
        _LOG.warning("video file not found at %s — skipping transcript", video_path)
        return []

    _, segments = _run_pipeline_cached(video_path, frames)
    return segments


def calculate_ad_gaps(events: list[AudioEvent]) -> list[dict]:
    """Find non-dialogue spans and return them as AD placement opportunities.

    Uses word-level timestamps from the most recent pipeline run for maximum
    precision. Falls back to speech-span boundary merging if no word data
    is available.

    Base keys (schema-compatible with audio.py): start, end, duration_seconds,
    recommended. Additional keys: midpoint, recommended_ad_start.
    """
    if _last_word_events:
        return _build_ad_gaps(_last_word_events)

    # Fallback: merge consecutive non-dialogue events (matches audio.py logic)
    gaps: list[dict] = []
    span_start: float | None = None
    span_end: float | None = None

    for e in sorted(events, key=lambda x: x.start):
        if e.event_type != "dialogue":
            if span_start is None:
                span_start = e.start
            span_end = e.end
        else:
            if span_start is not None:
                dur = span_end - span_start
                gaps.append(
                    {
                        "start": round(span_start, 3),
                        "end": round(span_end, 3),
                        "duration_seconds": round(dur, 3),
                        "midpoint": round((span_start + span_end) / 2, 3),
                        "recommended_ad_start": round(span_start + 0.25, 3),
                        "recommended": dur >= MIN_AD_GAP_SECONDS,
                    }
                )
                span_start = None
                span_end = None

    if span_start is not None:
        dur = span_end - span_start
        gaps.append(
            {
                "start": round(span_start, 3),
                "end": round(span_end, 3),
                "duration_seconds": round(dur, 3),
                "midpoint": round((span_start + span_end) / 2, 3),
                "recommended_ad_start": round(span_start + 0.25, 3),
                "recommended": dur >= MIN_AD_GAP_SECONDS,
            }
        )

    return gaps


# ---------------------------------------------------------------------------
# Serialisation helpers (used by pipeline.py when writing JSON output files)
# ---------------------------------------------------------------------------


def audio_event_to_dict(e: AudioEvent) -> dict:
    return {
        "start": round(e.start, 3),
        "end": round(e.end, 3),
        "event_type": e.event_type,
        "confidence": round(e.confidence, 4),
        "transcript": e.transcript,
    }


def transcript_segment_to_dict(s: TranscriptSegment) -> dict:
    return {
        "text": s.text,
        "start": round(s.start, 3),
        "end": round(s.end, 3),
        "words": [
            {
                "word": w.word,
                "start": round(w.start, 3),
                "end": round(w.end, 3),
                **({"speaker": w.speaker} if w.speaker else {}),
            }
            for w in s.words
        ],
    }


# ---------------------------------------------------------------------------
# __main__ — standalone test entry-point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    if len(sys.argv) < 2:
        print("Usage: python audio_whisperx_pipeline.py <video_path>")
        sys.exit(1)

    _video_path = Path(sys.argv[1])
    if not _video_path.exists():
        print(f"Error: file not found: {_video_path}")
        sys.exit(1)

    print(f"Processing: {_video_path}")

    # Single pipeline run — both events and word cache are populated together
    _events = load_audio_events(_video_path, frames=[])

    print("\nTranscript preview — first 10 words:")
    print(f"{'Word':<20} {'Start':>8} {'End':>8}")
    print("-" * 40)
    for _w in _last_word_events[:10]:
        print(f"{_w.word:<20} {_w.start:>8.3f} {_w.end:>8.3f}")

    if not _last_word_events:
        print("  (no word-level timestamps available)")

    _gaps = calculate_ad_gaps(_events)

    if not _gaps:
        print("\nNo AD placement gaps found.")
    else:
        import statistics as _stats

        _durations = [g["duration_seconds"] for g in _gaps]
        _rec = [g for g in _gaps if g["recommended"]]
        print("\nGap summary:")
        print(f"  Total gaps:              {len(_gaps)}")
        print(f"  Recommended (>={MIN_AD_GAP_SECONDS}s):    {len(_rec)}")
        print(f"  Shortest:                {min(_durations):.3f}s")
        print(f"  Longest:                 {max(_durations):.3f}s")
        print(f"  Median:                  {_stats.median(_durations):.3f}s")
        if _rec:
            print("\n  Recommended windows:")
            for _g in _rec:
                print(
                    f"    {_g['start']:.3f}s – {_g['end']:.3f}s"
                    f"  ({_g['duration_seconds']}s)"
                    f"  AD start: {_g['recommended_ad_start']:.3f}s"
                )
