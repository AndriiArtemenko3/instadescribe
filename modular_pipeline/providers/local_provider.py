"""Local, keyless providers.

  * Vision and text run on Ollama through its OpenAI-compatible endpoint
    (OLLAMA_BASE_URL, default http://localhost:11434/v1), via the shared `_compat`
    helper. Install Ollama, then:
        ollama pull qwen2.5vl:7b     # vision (scene captioning)
        ollama pull qwen2.5:7b       # text  (Smart Fill rewrite)
  * TTS runs on Kokoro (kokoro-82M, Apache-2.0) in-process; the ~330MB weights
    download once on first use. Requires `pip install -r requirements-local.txt`.

Quality trails the hosted models on ambiguous frames; the human edit-and-approve
loop is the intended mitigation. See docs/local-models.md.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import openai

from . import _compat
from .base import CaptionResult, ProviderError, TextResult

_OLLAMA_HINT = "Is `ollama serve` running and OLLAMA_BASE_URL correct?"


def _ollama_client() -> openai.OpenAI:
    return openai.OpenAI(
        base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
        api_key=os.getenv("OLLAMA_API_KEY", "ollama"),  # Ollama ignores the key
    )


class OllamaVisionProvider:
    name = "ollama"

    def __init__(self, model: str = "qwen2.5vl:7b") -> None:
        self.model = model

    def caption_chunk(
        self, *, developer_prompt, user_text, frames, schema, image_detail="low"
    ) -> CaptionResult:
        return _compat.caption_chunk(
            _ollama_client(),
            self.model,
            developer_prompt=developer_prompt,
            user_text=user_text,
            frames=frames,
            schema=schema,
            service="Ollama",
            hint=_OLLAMA_HINT,
        )


class OllamaTextProvider:
    name = "ollama"

    def __init__(self, model: str = "qwen2.5:7b") -> None:
        self.model = model

    def rewrite(self, *, system, user, temperature=0.4, max_tokens=400) -> TextResult:
        return _compat.rewrite(
            _ollama_client(),
            self.model,
            system=system,
            user=user,
            temperature=temperature,
            max_tokens=max_tokens,
            service="Ollama",
            hint=_OLLAMA_HINT,
        )


# InstaDescribe's provider-neutral voice names -> Kokoro voices (a = American English).
_KOKORO_VOICE_MAP = {
    "onyx": "am_michael",
    "echo": "am_adam",
    "fable": "bm_george",
    "nova": "af_heart",
    "shimmer": "af_bella",
    "alloy": "af_sarah",
}
_DEFAULT_KOKORO_VOICE = "am_michael"


class KokoroTTSProvider:
    name = "kokoro"
    voices = tuple(_KOKORO_VOICE_MAP.keys())

    def __init__(self, lang_code: str = "a") -> None:
        self.lang_code = lang_code
        self._pipeline = None

    def _get_pipeline(self):
        if self._pipeline is None:
            try:
                from kokoro import KPipeline
            except ImportError as exc:
                raise ProviderError(
                    "Kokoro TTS is not installed. Run: pip install -r requirements-local.txt",
                    retryable=False,
                ) from exc
            self._pipeline = KPipeline(lang_code=self.lang_code)
        return self._pipeline

    def synthesize(self, *, text, voice, out_path: Path) -> Path:
        import subprocess
        import tempfile

        import numpy as np
        import soundfile as sf

        out_path.parent.mkdir(parents=True, exist_ok=True)
        pipeline = self._get_pipeline()
        kvoice = _KOKORO_VOICE_MAP.get((voice or "").strip().lower(), _DEFAULT_KOKORO_VOICE)

        chunks: list[Any] = []
        for _graphemes, _phonemes, audio in pipeline(text, voice=kvoice):
            arr = audio.detach().cpu().numpy() if hasattr(audio, "detach") else np.asarray(audio)
            chunks.append(arr)
        if not chunks:
            raise ProviderError("Kokoro produced no audio for the given text", retryable=False)
        audio = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]

        # Kokoro emits 24kHz float32. Write a wav, then transcode to mp3 for parity
        # with the OpenAI path (the mixer downstream expects an mp3 on disk).
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_path = Path(tmp.name)
        try:
            sf.write(str(wav_path), audio, 24000)
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(wav_path), "-b:a", "192k", str(out_path)],
                check=True,
                capture_output=True,
            )
        finally:
            wav_path.unlink(missing_ok=True)
        return out_path
