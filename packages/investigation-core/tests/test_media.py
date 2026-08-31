from __future__ import annotations

import hashlib
import subprocess

import pytest

from instadescribe_investigation_core import inspect_media, perceptual_hash, sha256_file


def test_sha256_and_degraded_metadata_are_deterministic(tmp_path) -> None:
    media = tmp_path / "fixture.mp4"
    media.write_bytes(b"permitted fixture")

    metadata = inspect_media(media, ffprobe_executable="/definitely/missing/ffprobe")

    assert metadata.content_sha256 == hashlib.sha256(b"permitted fixture").hexdigest()
    assert metadata.content_sha256 == sha256_file(media, chunk_size=2)
    assert metadata.size_bytes == len(b"permitted fixture")
    assert metadata.probe_available is False
    assert metadata.warnings == ("ffprobeFailed:FileNotFoundError",)


def test_perceptual_hash_when_pillow_is_available(tmp_path) -> None:
    image_module = pytest.importorskip("PIL.Image")
    image = image_module.new("RGB", (32, 32), "white")
    image.putpixel((0, 0), (0, 0, 0))
    path = tmp_path / "fixture.png"
    image.save(path)

    first = perceptual_hash(path)
    second = perceptual_hash(path)

    assert first == second
    assert len(first) == 16


def test_ffprobe_is_restricted_to_local_file_and_pipe_protocols(monkeypatch, tmp_path) -> None:
    media = tmp_path / "fixture.mp4"
    media.write_bytes(b"permitted fixture")
    commands: list[list[str]] = []

    def run(command, **kwargs):
        commands.append(command)
        kwargs["stdout"].write(b'{"format":{},"streams":[]}')
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", run)

    inspect_media(media, ffprobe_executable="ffprobe")

    command = commands[0]
    index = command.index("-protocol_whitelist")
    assert command[index + 1] == "file,pipe"
    assert index < len(command) - 1
    assert command[command.index("-f") + 1] == "mov"
    assert command[command.index("-enable_drefs") + 1] == "0"
    assert command[command.index("-use_absolute_path") + 1] == "0"
    assert command[command.index("-max_streams") + 1] == "32"


def test_ffprobe_receives_a_minimal_environment_and_bounded_projection(monkeypatch, tmp_path):
    media = tmp_path / "fixture.mp4"
    media.write_bytes(b"permitted fixture")
    observed: dict[str, object] = {}

    def run(command, **kwargs):
        observed.update(kwargs)
        kwargs["stdout"].write(b'{"format":{},"streams":[]}')
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-cross")
    monkeypatch.setenv("HOME", "/private/host-home")
    monkeypatch.setattr(subprocess, "run", run)

    inspect_media(media, ffprobe_executable="ffprobe")

    assert set(observed["env"]) == {"PATH", "LANG"}
    assert "AWS_SECRET_ACCESS_KEY" not in observed["env"]
    assert "HOME" not in observed["env"]
    assert observed["stderr"] is subprocess.DEVNULL


@pytest.mark.parametrize("duration", ("Infinity", "NaN", "1e309", "1e308"))
def test_ffprobe_non_finite_or_overflowing_duration_degrades_safely(
    monkeypatch,
    tmp_path,
    duration,
) -> None:
    media = tmp_path / "fixture.mp4"
    media.write_bytes(b"permitted fixture")

    def run(command, **kwargs):
        kwargs["stdout"].write(('{"format":{"duration":"' + duration + '"},"streams":[]}').encode())
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", run)

    metadata = inspect_media(media, ffprobe_executable="ffprobe")

    assert metadata.duration_ms is None
    assert metadata.probe_available is True
    assert metadata.warnings == ("durationInvalid",)
