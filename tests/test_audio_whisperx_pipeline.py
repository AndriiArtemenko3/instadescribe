"""Regression tests for the isolated faster-whisper subprocess."""

import contextlib
import io
import json
import logging
import subprocess
import sys
import types
from types import SimpleNamespace

# The backend CI intentionally installs a lightweight dependency set without
# NumPy. These tests exercise only the generated subprocess and its wrapper, so
# provide the one annotation attribute needed while importing the module.
try:
    import numpy  # noqa: F401
except ModuleNotFoundError:
    numpy_stub = types.ModuleType("numpy")
    numpy_stub.ndarray = object
    sys.modules["numpy"] = numpy_stub

import audio_whisperx_pipeline as audio_pipeline
import pytest


def _execute_generated_script(monkeypatch, model_type):
    fake_module = types.ModuleType("faster_whisper")
    fake_module.WhisperModel = model_type
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)
    monkeypatch.setattr(
        sys,
        "argv",
        ["transcribe.py", "input.wav", "cpu", "/cache", "medium", "float32", "None"],
    )

    stdout = io.StringIO()
    stderr = io.StringIO()
    namespace = {"__name__": "__main__"}
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        exec(
            compile(audio_pipeline._transcription_subprocess_script(), "<transcribe>", "exec"),
            namespace,
            namespace,
        )
    return json.loads(stdout.getvalue()), stderr.getvalue()


def test_generated_transcription_retries_known_alignment_error_without_word_timestamps(
    monkeypatch,
):
    calls = []

    class WhisperModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, *args, **kwargs):
            calls.append(kwargs["word_timestamps"])
            if kwargs["word_timestamps"]:

                def broken_segments():
                    yield SimpleNamespace(start=0, end=1, text="discard me", words=None)
                    raise IndexError(audio_pipeline._FASTER_WHISPER_WORD_ALIGNMENT_INDEX_ERROR)

                return broken_segments(), SimpleNamespace()

            return (
                iter([SimpleNamespace(start=2, end=4, text="hello world", words=None)]),
                SimpleNamespace(),
            )

    results, stderr = _execute_generated_script(monkeypatch, WhisperModel)

    assert calls == [True, False]
    assert results == [
        {
            "start": 2.0,
            "end": 4.0,
            "text": "hello world",
            "words": [
                {"word": "hello", "start": 2.0, "end": 3.0},
                {"word": "world", "start": 3.0, "end": 4.0},
            ],
        }
    ]
    assert audio_pipeline._TRANSCRIPTION_ALIGNMENT_FALLBACK_MARKER in stderr
    assert "discard me" not in json.dumps(results)


def test_generated_transcription_does_not_retry_unrelated_index_error(monkeypatch):
    calls = []

    class WhisperModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, *args, **kwargs):
            calls.append(kwargs["word_timestamps"])

            def broken_segments():
                raise IndexError("unrelated decoder failure")
                yield

            return broken_segments(), SimpleNamespace()

    with pytest.raises(IndexError, match="unrelated decoder failure"):
        _execute_generated_script(monkeypatch, WhisperModel)

    assert calls == [True]


def test_generated_transcription_normal_word_timestamp_path_is_unchanged(monkeypatch):
    calls = []
    native_words = [
        SimpleNamespace(word="native", start=0.1, end=0.6),
        SimpleNamespace(word="timing", start=0.7, end=1.2),
    ]

    class WhisperModel:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, *args, **kwargs):
            calls.append(kwargs["word_timestamps"])
            return (
                iter(
                    [SimpleNamespace(start=0, end=1.5, text=" native timing ", words=native_words)]
                ),
                SimpleNamespace(),
            )

    results, stderr = _execute_generated_script(monkeypatch, WhisperModel)

    assert calls == [True]
    assert stderr == ""
    assert results == [
        {
            "start": 0.0,
            "end": 1.5,
            "text": "native timing",
            "words": [
                {"word": "native", "start": 0.1, "end": 0.6},
                {"word": "timing", "start": 0.7, "end": 1.2},
            ],
        }
    ]


def test_bounded_transcription_stderr_preserves_terminal_exception():
    stderr = ("trace context\n" + "x" * 3000 + "\nIndexError: terminal alignment failure").encode()

    bounded = audio_pipeline._bounded_transcription_stderr(stderr)

    assert len(bounded) == audio_pipeline._TRANSCRIPTION_STDERR_LIMIT
    assert bounded.startswith("trace context")
    assert "stderr truncated; terminal output follows" in bounded
    assert bounded.endswith("IndexError: terminal alignment failure")


def test_successful_alignment_fallback_emits_sanitized_parent_warning(monkeypatch, caplog):
    completed = subprocess.CompletedProcess(
        args=["python"],
        returncode=0,
        stdout=b"[]",
        stderr=(
            b"untrusted diagnostic detail\n"
            + audio_pipeline._TRANSCRIPTION_ALIGNMENT_FALLBACK_MARKER.encode()
        ),
    )
    monkeypatch.setattr(audio_pipeline.subprocess, "run", lambda *args, **kwargs: completed)

    with caplog.at_level(logging.WARNING, logger=audio_pipeline.__name__):
        result = audio_pipeline._run_transcription_subprocess(
            audio_pipeline.Path("input.wav"),
            "cpu",
            audio_pipeline.Path("cache"),
        )

    assert result == []
    assert "used evenly distributed word timestamps" in caplog.text
    assert "untrusted diagnostic detail" not in caplog.text
