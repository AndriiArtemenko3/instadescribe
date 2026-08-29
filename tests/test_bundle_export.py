import shutil
import subprocess
from pathlib import Path

import bundle_export as B
import pytest
from instadescribe_contracts.provider import TTS_BETA_MAX_APPROVED_SCENES_PER_REVIEW


def _scene(state: str, *, scene_id: str = "scene_1", text: str = "A person enters."):
    return {
        "scene_id": scene_id,
        "start": 1.0,
        "end": 3.0,
        "caption": "generated",
        "text": text,
        "review_state": state,
        "voice": "onyx",
        "speed": 1.0,
        "character_ids": [],
    }


def _stub_media(monkeypatch, source: Path):
    calls: dict[str, object] = {}

    def render_line(_text, _voice, destination):
        destination.write_bytes(b"raw")

    def copy_audio(first, destination, *_args):
        destination.write_bytes(first.read_bytes())

    def export(video, blocks, destination):
        calls["blocks"] = blocks
        destination.write_bytes(video.read_bytes() + b"-described")

    def extract(video, destination):
        destination.write_bytes(video.read_bytes() + b"-audio")

    def write_docx(_project_name, _scenes, _entities, destination):
        destination.write_bytes(b"PK\x03\x04test-docx")
        return destination

    monkeypatch.setattr(B, "render_line", render_line)
    monkeypatch.setattr(B, "normalise_audio", copy_audio)
    monkeypatch.setattr(B, "adjust_speed", copy_audio)
    monkeypatch.setattr(B, "get_duration", lambda _path: 1.25)
    monkeypatch.setattr(B, "measure_gap_lufs", lambda *_args: -18.0)
    monkeypatch.setattr(B, "export_with_ad", export)
    monkeypatch.setattr(B, "_extract_mp3", extract)
    monkeypatch.setattr(B, "write_docx", write_docx)
    return calls


def _silent_video(path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x180:r=24:d=4",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )


def _audio_codec(path: Path) -> str:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip()


def test_bundle_writes_all_formats_from_one_review_snapshot(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    calls = _stub_media(monkeypatch, source)
    progress = []

    outputs = B.render_all_deliverables(
        source_video=source,
        scenes=[_scene("approved"), _scene("rejected", scene_id="scene_2", text="omit")],
        entities_by_id={},
        output_dir=tmp_path / "bundle",
        project_name="BIO101",
        default_voice="onyx",
        on_progress=lambda stage, percent: progress.append((stage, percent)),
    )

    assert set(outputs) == {"mp4", "mp3", "srt", "csv", "docx"}
    assert {path.name for path in outputs.values()} == set(B.DELIVERABLE_FILENAMES.values())
    assert all(path.is_file() for path in outputs.values())
    assert len(calls["blocks"]) == 1
    assert "omit" not in outputs["srt"].read_text()
    assert progress[-1] == ("complete", 100)
    assert not (tmp_path / "bundle" / ".tts").exists()


def test_zero_ad_bundle_is_valid_and_never_calls_tts(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    calls = _stub_media(monkeypatch, source)
    monkeypatch.setattr(B, "render_line", lambda *_args: pytest.fail("zero-AD called TTS"))

    outputs = B.render_all_deliverables(
        source_video=source,
        scenes=[_scene("rejected")],
        entities_by_id={},
        output_dir=tmp_path / "zero",
        project_name="No narration required",
        default_voice="onyx",
    )

    assert calls["blocks"] == []
    assert outputs["srt"].read_text() == ""
    assert outputs["csv"].read_text().count("\n") == 2
    assert all(outputs[kind].stat().st_size > 0 for kind in {"mp4", "mp3", "csv", "docx"})


def test_bundle_rejects_excessive_approved_scenes_before_first_tts_call(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    _stub_media(monkeypatch, source)
    monkeypatch.setattr(B, "render_line", lambda *_args: pytest.fail("budget called TTS"))
    scenes = [
        _scene("approved", scene_id=f"scene_{index}")
        for index in range(1, TTS_BETA_MAX_APPROVED_SCENES_PER_REVIEW + 2)
    ]
    output_dir = tmp_path / "over-budget"

    with pytest.raises(ValueError, match="beta TTS synthesis limit"):
        B.render_all_deliverables(
            source_video=source,
            scenes=scenes,
            entities_by_id={},
            output_dir=output_dir,
            project_name="Excessive narration",
            default_voice="onyx",
        )

    assert not any(path.is_file() for path in output_dir.glob("**/*"))


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe missing",
)
@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_video_without_source_audio_still_produces_mp4_and_mp3(tmp_path, monkeypatch, decision):
    import providers.factory as provider_factory

    monkeypatch.setattr(provider_factory, "_OVERRIDE", "fake")

    def write_docx_without_optional_test_dependency(_project_name, _scenes, _entities, destination):
        destination.write_bytes(b"PK\x03\x04test-docx")
        return destination

    monkeypatch.setattr(B, "write_docx", write_docx_without_optional_test_dependency)
    source = tmp_path / "silent-source.mp4"
    _silent_video(source)
    assert _audio_codec(source) == ""

    outputs = B.render_all_deliverables(
        source_video=source,
        scenes=[_scene(decision)],
        entities_by_id={},
        output_dir=tmp_path / decision,
        project_name="Silent source",
        default_voice="onyx",
    )

    assert set(outputs) == {"mp4", "mp3", "srt", "csv", "docx"}
    assert _audio_codec(outputs["mp4"]) == "aac"
    assert _audio_codec(outputs["mp3"]) == "mp3"
    assert all(path.is_file() for path in outputs.values())


@pytest.mark.parametrize("state", ["generated", "edited", None])
def test_incomplete_review_fails_before_render_and_cleans_outputs(tmp_path, monkeypatch, state):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    _stub_media(monkeypatch, source)
    output_dir = tmp_path / "partial"

    with pytest.raises(ValueError, match="approved or rejected"):
        B.render_all_deliverables(
            source_video=source,
            scenes=[_scene(state)],
            entities_by_id={},
            output_dir=output_dir,
            project_name="Unsafe",
            default_voice="onyx",
        )

    assert not any(path.is_file() for path in output_dir.glob("*"))


def test_failure_removes_partial_outputs(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    _stub_media(monkeypatch, source)
    monkeypatch.setattr(B, "_extract_mp3", lambda *_args: (_ for _ in ()).throw(RuntimeError("x")))
    output_dir = tmp_path / "failed"

    with pytest.raises(RuntimeError, match="x"):
        B.render_all_deliverables(
            source_video=source,
            scenes=[_scene("approved")],
            entities_by_id={},
            output_dir=output_dir,
            project_name="Retry",
            default_voice="onyx",
        )

    assert not any(path.is_file() for path in output_dir.glob("*"))
