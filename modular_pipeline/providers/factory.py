"""Runtime provider selection.

Pick a backend for everything at once with INSTADESCRIBE_BACKEND, or override one
capability at a time:

    INSTADESCRIBE_BACKEND = openai | local | anthropic | gemini | fake (default: openai)
    VISION_PROVIDER / TEXT_PROVIDER / TTS_PROVIDER                     (per capability)

`local` means Ollama for vision + text and Kokoro for TTS. `anthropic` (Claude)
and `gemini` cover vision + text only; their TTS falls back to OpenAI (set
TTS_PROVIDER=local for a keyless Kokoro voice). Model ids default per backend and
can be overridden with VISION_MODEL / TEXT_MODEL / TTS_MODEL (and the per-backend
ANTHROPIC_MODEL / GEMINI_MODEL); OpenAI vision keeps reading JOB_MODEL.
"""

from __future__ import annotations

import os

from environment import getenv_compat

from .base import TextProvider, TTSProvider, VisionProvider

# `local` is the friendly umbrella name; vision + text under it run on Ollama.
_ALIASES = {"local": "ollama"}

VALID_BACKENDS = ("openai", "anthropic", "gemini", "local", "fake")

# Runtime backend override, set from the app's Settings picker (POST /api/providers).
# None means fall back to the INSTADESCRIBE_BACKEND env var / default.
_OVERRIDE: str | None = None


def set_active_backend(name: str) -> None:
    """Set the runtime backend (the in-app picker). Raises on an unknown name."""
    global _OVERRIDE
    normalized = (name or "").strip().lower()
    if normalized not in VALID_BACKENDS:
        raise ValueError(f"Unknown backend: {name!r} (want {'|'.join(VALID_BACKENDS)})")
    _OVERRIDE = normalized


def active_backend() -> str:
    """The effective backend the UI shows and new jobs run under (friendly name)."""
    return _OVERRIDE or getenv_compat("INSTADESCRIBE_BACKEND") or "openai"


def _resolve(capability_env: str, default_backend: str = "openai") -> str:
    backend = (
        os.getenv(capability_env)
        or _OVERRIDE
        or getenv_compat("INSTADESCRIBE_BACKEND")
        or default_backend
    )
    backend = backend.strip().lower()
    return _ALIASES.get(backend, backend)


def get_vision_provider() -> VisionProvider:
    backend = _resolve("VISION_PROVIDER")
    if backend == "openai":
        from .openai_provider import OpenAIVisionProvider

        return OpenAIVisionProvider(model=os.getenv("JOB_MODEL", "gpt-4.1"))
    if backend == "ollama":
        from .local_provider import OllamaVisionProvider

        return OllamaVisionProvider(model=os.getenv("VISION_MODEL", "qwen2.5vl:7b"))
    if backend == "anthropic":
        from .anthropic_provider import AnthropicVisionProvider

        return AnthropicVisionProvider(
            model=_model("VISION_MODEL", "ANTHROPIC_MODEL", "claude-opus-4-8")
        )
    if backend == "gemini":
        from .gemini_provider import GeminiVisionProvider

        return GeminiVisionProvider(
            model=_model("VISION_MODEL", "GEMINI_MODEL", "gemini-2.5-flash")
        )
    if backend == "fake":
        from .fake_provider import FakeVisionProvider

        return FakeVisionProvider()
    raise ValueError(
        f"Unknown vision backend: {backend!r} (want openai|local|anthropic|gemini|fake)"
    )


def get_text_provider() -> TextProvider:
    backend = _resolve("TEXT_PROVIDER")
    if backend == "openai":
        from .openai_provider import OpenAITextProvider

        return OpenAITextProvider(model=os.getenv("TEXT_MODEL", "gpt-4o-mini"))
    if backend == "ollama":
        from .local_provider import OllamaTextProvider

        return OllamaTextProvider(model=os.getenv("TEXT_MODEL", "qwen2.5:7b"))
    if backend == "anthropic":
        from .anthropic_provider import AnthropicTextProvider

        return AnthropicTextProvider(
            model=_model("TEXT_MODEL", "ANTHROPIC_MODEL", "claude-opus-4-8")
        )
    if backend == "gemini":
        from .gemini_provider import GeminiTextProvider

        return GeminiTextProvider(model=_model("TEXT_MODEL", "GEMINI_MODEL", "gemini-2.5-flash"))
    if backend == "fake":
        from .fake_provider import FakeTextProvider

        return FakeTextProvider()
    raise ValueError(f"Unknown text backend: {backend!r} (want openai|local|anthropic|gemini|fake)")


def get_tts_provider() -> TTSProvider:
    backend = _resolve("TTS_PROVIDER")
    if backend in ("ollama", "kokoro"):  # `local` resolves to ollama; local TTS is Kokoro
        from .local_provider import KokoroTTSProvider

        return KokoroTTSProvider()
    if backend == "fake":
        from .fake_provider import FakeTTSProvider

        return FakeTTSProvider()
    # openai + the two vision/text-only cloud backends (anthropic, gemini): use OpenAI
    # TTS. For a keyless voice under those backends, set TTS_PROVIDER=local (Kokoro).
    if backend in ("openai", "anthropic", "gemini"):
        from .openai_provider import OpenAITTSProvider

        return OpenAITTSProvider(model=os.getenv("TTS_MODEL", "tts-1-hd"))
    raise ValueError(f"Unknown TTS backend: {backend!r} (want openai|local|fake)")


def _model(capability_env: str, backend_env: str, default: str) -> str:
    # Per-capability override (VISION_MODEL / TEXT_MODEL) wins, then the per-backend
    # override (ANTHROPIC_MODEL / GEMINI_MODEL), then the default.
    return os.getenv(capability_env) or os.getenv(backend_env) or default


def provider_status() -> list[dict[str, object]]:
    """Per-backend readiness for the in-app picker. Reads env for keys (which stay
    server-side — no key value is ever returned) and probes Ollama for the local
    backend."""
    import importlib.util
    import socket
    import urllib.parse
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")  # app-root .env holds the keys

    def _reachable(url: str) -> bool:
        try:
            parts = urllib.parse.urlparse(url)
            with socket.create_connection(
                (parts.hostname or "localhost", parts.port or 11434), timeout=0.4
            ):
                return True
        except OSError:
            return False

    openai_ready = bool(os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_BASE_URL"))
    anthropic_key = bool(os.getenv("ANTHROPIC_API_KEY"))
    anthropic_sdk = importlib.util.find_spec("anthropic") is not None
    anthropic_ready = anthropic_key and anthropic_sdk
    gemini_ready = bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))
    local_ready = _reachable(os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"))

    return [
        {
            "id": "openai",
            "label": "OpenAI",
            "ready": openai_ready,
            "reason": "" if openai_ready else "Set OPENAI_API_KEY in .env",
        },
        {
            "id": "anthropic",
            "label": "Claude",
            "ready": anthropic_ready,
            "reason": ""
            if anthropic_ready
            else (
                "Set ANTHROPIC_API_KEY in .env"
                if not anthropic_key
                else "pip install -r requirements-providers.txt"
            ),
        },
        {
            "id": "gemini",
            "label": "Gemini",
            "ready": gemini_ready,
            "reason": "" if gemini_ready else "Set GEMINI_API_KEY in .env",
        },
        {
            "id": "local",
            "label": "Local (Ollama + Kokoro)",
            "ready": local_ready,
            "reason": "" if local_ready else "Start Ollama (ollama serve) and pull the models",
        },
        {"id": "fake", "label": "Fake (fixtures, no key)", "ready": True, "reason": ""},
    ]
