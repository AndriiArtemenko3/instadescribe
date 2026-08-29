"""Tests for the model-provider seam. Exercises the factory's backend selection
and the fake backend end to end — no API key, no network, no heavy deps."""

import json
import shutil
import subprocess

import pytest
from providers import Frame, get_text_provider, get_tts_provider, get_vision_provider
from providers.fake_provider import FakeTextProvider, FakeTTSProvider, FakeVisionProvider
from schemas import SCENE_SCHEMA

_ENV_KEYS = (
    "INSTADESCRIBE_BACKEND",
    "INSTASCRIBE_BACKEND",  # temporary v0.1 compatibility alias
    "VISION_PROVIDER",
    "TEXT_PROVIDER",
    "TTS_PROVIDER",
)


@pytest.fixture
def clean_env(monkeypatch):
    import providers.factory as factory

    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(factory, "_OVERRIDE", None)  # reset the runtime picker between tests
    return monkeypatch


def test_default_backend_is_openai(clean_env):
    assert get_vision_provider().name == "openai"
    assert get_text_provider().name == "openai"
    assert get_tts_provider().name == "openai"


def test_fake_backend_selected_for_all_capabilities(clean_env):
    clean_env.setenv("INSTADESCRIBE_BACKEND", "fake")
    assert isinstance(get_vision_provider(), FakeVisionProvider)
    assert isinstance(get_text_provider(), FakeTextProvider)
    assert isinstance(get_tts_provider(), FakeTTSProvider)


def test_legacy_backend_alias_is_warned_and_conflicts_fail_closed(clean_env):
    from environment import LegacyEnvironmentWarning

    clean_env.setenv("INSTASCRIBE_BACKEND", "fake")
    with pytest.warns(LegacyEnvironmentWarning):
        assert isinstance(get_vision_provider(), FakeVisionProvider)

    clean_env.setenv("INSTADESCRIBE_BACKEND", "openai")
    with pytest.raises(RuntimeError) as caught:
        get_vision_provider()
    assert "fake" not in str(caught.value) and "openai" not in str(caught.value)


def test_local_alias_maps_to_ollama_and_kokoro(clean_env):
    clean_env.setenv("INSTADESCRIBE_BACKEND", "local")
    assert get_vision_provider().name == "ollama"
    assert get_text_provider().name == "ollama"
    assert get_tts_provider().name == "kokoro"


def test_per_capability_override_beats_global(clean_env):
    clean_env.setenv("INSTADESCRIBE_BACKEND", "openai")
    clean_env.setenv("VISION_PROVIDER", "fake")
    assert get_vision_provider().name == "fake"
    assert get_text_provider().name == "openai"  # untouched by the vision override


def test_anthropic_backend_selected(clean_env):
    clean_env.setenv("INSTADESCRIBE_BACKEND", "anthropic")
    assert get_vision_provider().name == "anthropic"
    assert get_text_provider().name == "anthropic"
    # Anthropic has no TTS of its own; it falls back to OpenAI TTS.
    assert get_tts_provider().name == "openai"


def test_gemini_backend_selected(clean_env):
    clean_env.setenv("INSTADESCRIBE_BACKEND", "gemini")
    assert get_vision_provider().name == "gemini"
    assert get_text_provider().name == "gemini"
    assert get_tts_provider().name == "openai"


def test_mixed_vision_and_text_backends(clean_env):
    clean_env.setenv("VISION_PROVIDER", "anthropic")
    clean_env.setenv("TEXT_PROVIDER", "gemini")
    assert get_vision_provider().name == "anthropic"
    assert get_text_provider().name == "gemini"


def test_selecting_cloud_backends_needs_no_sdk_or_key(clean_env):
    # Choosing anthropic/gemini must construct without importing their SDK or
    # requiring a key — that only happens on the first real call.
    clean_env.setenv("VISION_PROVIDER", "anthropic")
    assert get_vision_provider().name == "anthropic"
    clean_env.setenv("VISION_PROVIDER", "gemini")
    assert get_vision_provider().name == "gemini"


def test_runtime_backend_override(clean_env):
    from providers import active_backend, set_active_backend

    assert active_backend() == "openai"
    set_active_backend("gemini")
    assert active_backend() == "gemini"
    assert get_vision_provider().name == "gemini"  # the picker beats the env default


def test_set_active_backend_rejects_unknown(clean_env):
    from providers import set_active_backend

    with pytest.raises(ValueError):
        set_active_backend("bogus")


def test_provider_status_reports_all_backends(clean_env):
    from providers import provider_status

    status = provider_status()
    assert {s["id"] for s in status} == {"openai", "anthropic", "gemini", "local", "fake"}
    assert next(s for s in status if s["id"] == "fake")["ready"] is True


def test_unknown_backend_raises(clean_env):
    clean_env.setenv("VISION_PROVIDER", "bogus")
    with pytest.raises(ValueError):
        get_vision_provider()


def test_constructing_local_providers_needs_no_server(clean_env):
    # A provider must construct without reaching its backend; the call does that.
    from providers.local_provider import OllamaVisionProvider

    assert OllamaVisionProvider().name == "ollama"


def test_fake_vision_output_conforms_to_scene_schema_shape():
    frames = [Frame(index=i, timestamp=float(i), image_b64="") for i in range(3)]
    result = FakeVisionProvider().caption_chunk(
        developer_prompt="dev", user_text="usr", frames=frames, schema=SCENE_SCHEMA
    )
    data = result.data
    assert all(k in data for k in SCENE_SCHEMA["schema"]["required"])
    assert len(data["scenes"]) == 3
    scene_required = SCENE_SCHEMA["schema"]["properties"]["scenes"]["items"]["required"]
    assert all(k in data["scenes"][0] for k in scene_required)
    assert data["memory_updates"] == {"seen_character_ids": [], "new_characters": []}
    # G5.1: real providers never emit zero-length scenes; the placeholder
    # geometry must match (the worker's artifact contract enforces end > start).
    assert all(s["end"] > s["start"] for s in data["scenes"])


def test_fake_text_respects_word_budget():
    user = (
        "Time budget: 3.0 seconds (~7 words at typical AD pace).\n\n"
        "Current description:\n"
        "A tall man in a dark coat walks slowly across the wet platform to the train.\n\n"
        "Rewrite to fit within the time budget."
    )
    out = FakeTextProvider().rewrite(system="editor", user=user)
    assert len(out.text.split()) <= 7
    assert out.model == "fake"


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe missing",
)
def test_fake_tts_writes_normalisable_valid_mp3_without_app_fixture(tmp_path):
    from tts_render import normalise_audio

    first = tmp_path / "first.mp3"
    second = tmp_path / "second.mp3"
    FakeTTSProvider().synthesize(text="A door opens.", voice="onyx", out_path=first)
    FakeTTSProvider().synthesize(text="A door opens.", voice="nova", out_path=second)

    def probe(path):
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_name:format=duration",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout)

    first_probe = probe(first)
    second_probe = probe(second)
    assert first.stat().st_size > 0
    assert first_probe["streams"][0]["codec_name"] == "mp3"
    assert float(first_probe["format"]["duration"]) > 0
    assert first_probe["format"]["duration"] == second_probe["format"]["duration"]
    assert first.read_bytes() == second.read_bytes()

    normalised = normalise_audio(first, tmp_path / "normalised.mp3")
    assert normalised.stat().st_size > 0
    assert probe(normalised)["streams"][0]["codec_name"] == "mp3"
