# Model providers and running locally

> **Legacy audio-description documentation:** the providers and commands on this
> page apply to the retained audio-description pipeline. They do not describe a
> working video-investigation model path. The current investigation end-to-end proof
> uses an explicit deterministic fixture with no model inference; live Ollama/Qwen
> execution fails closed pending a parent-validated proposal handshake and separate
> runtime/resource verification.

InstaDescribe calls a model for exactly three things: **vision** (describe a chunk
of frames as structured JSON), **text** (rewrite one description to fit a time
budget, the Smart Fill), and **TTS** (speak a description to audio). Speech
detection and transcription already run locally (silero-vad + faster-whisper) and
never touched a hosted API.

Each of the three is a small interface in `modular_pipeline/providers/`, and the
backend is chosen at runtime. No call site imports a vendor SDK, so swapping a
model is a config change, not a code change.

## Choosing a backend

Set one environment variable:

| `INSTADESCRIBE_BACKEND` | Vision | Text | TTS | Key |
|---|---|---|---|---|
| `openai` (default) | gpt-4.1 | gpt-4o-mini | tts-1-hd | `OPENAI_API_KEY` |
| `anthropic` | claude-opus-4-8 | claude-opus-4-8 | → OpenAI TTS | `ANTHROPIC_API_KEY` |
| `gemini` | gemini-2.5-flash | gemini-2.5-flash | → OpenAI TTS | `GEMINI_API_KEY` |
| `local` | Ollama qwen2.5vl:7b | Ollama qwen2.5:7b | Kokoro | none |
| `fake` | placeholder JSON | deterministic trim | silence | none |

`INSTADESCRIBE_BACKEND` is canonical. The legacy `INSTASCRIBE_BACKEND` alias is
accepted only during the v0.1 rollback window; conflicting old and new values fail
closed.

Override one stage at a time with `VISION_PROVIDER`, `TEXT_PROVIDER`, or
`TTS_PROVIDER` (each `openai | anthropic | gemini | local | fake`). For example,
run Claude vision but speak with a keyless local voice:
`VISION_PROVIDER=anthropic TTS_PROVIDER=local`.

`anthropic` and `gemini` cover vision + text only; their TTS falls back to OpenAI
(so an OpenAI key is still needed for speech) unless you set `TTS_PROVIDER=local`
for keyless Kokoro. `fake` makes no network call and needs no model.

`fake` makes no network call and needs no model; it powers the test suite and a
keyless server smoke run.

## Running fully local (no API key)

1. Install [Ollama](https://ollama.com) and pull the models:

   ```bash
   ollama pull qwen2.5vl:7b   # vision — scene captioning (~6GB)
   ollama pull qwen2.5:7b     # text  — Smart Fill rewrite (~5GB)
   ```

2. Install the local TTS dependency (Kokoro; weights download on first use):

   ```bash
   pip install -r requirements-local.txt
   ```

3. Point InstaDescribe at the local backend and run it:

   ```bash
   INSTADESCRIBE_BACKEND=local python modular_pipeline/server.py
   ```

The pipeline now runs end to end with no key and no data leaving the machine.

## Cloud alternatives: Claude and Gemini

Set `INSTADESCRIBE_BACKEND=anthropic` or `gemini` (or the per-capability
`VISION_PROVIDER` / `TEXT_PROVIDER`).

- **Claude** uses the official `anthropic` SDK (`pip install -r requirements-providers.txt`,
  `ANTHROPIC_API_KEY`). Scene captioning gets schema-valid JSON through a strict
  tool call; the Smart Fill rewrite is a plain message. Default `claude-opus-4-8`;
  set `ANTHROPIC_MODEL` (`claude-haiku-4-5` is a cheaper rewrite).
- **Gemini** needs no extra package — it reuses the `openai` client against
  Gemini's OpenAI-compatibility endpoint (`GEMINI_API_KEY`, default
  `gemini-2.5-flash`, set `GEMINI_MODEL`). The same `_compat` code path also
  serves Ollama, vLLM, LM Studio, and OpenRouter.

Neither ships TTS here, so speech uses OpenAI TTS unless you set `TTS_PROVIDER=local`.

## Model choices and the quality tradeoff

- **Vision — Qwen2.5-VL-7B** (Apache-2.0). Strong open vision-language model that
  handles the strict-JSON scene schema through Ollama's structured-output mode. It
  trails GPT-4.1 on ambiguous or cluttered frames — a real, visible gap. The
  human edit-and-approve loop is the intended mitigation: a description a blind
  listener relies on is reviewed before it ships regardless of which model drafted
  it. Larger Qwen2.5-VL variants close the gap at a higher memory cost.
- **Text — Qwen2.5-7B** (Apache-2.0). Smart Fill is a short, constrained rewrite;
  the local gap here is small.
- **TTS — Kokoro-82M** (Apache-2.0). Runs in-process on CPU near real time. Its
  even, neutral delivery suits audio-description narration. The gap to hosted TTS
  is narrower than the vision gap.
- **ASR — faster-whisper** stays as-is; it is already the local standard.

These model choices avoid known model-license blockers for commercial evaluation,
but every deploy must verify the exact downloaded weights and dependency versions.
Avoided as defaults: Llama 3.2 Vision (restrictive license), XTTS-v2
(non-commercial), F5-TTS (CC-BY-NC).

## The `OPENAI_BASE_URL` shortcut

The OpenAI backend also honors `OPENAI_BASE_URL`, so you can point the standard
client at any OpenAI-compatible server (Ollama, vLLM, LM Studio, OpenRouter)
without touching the `local` provider. This covers vision and text; hosted TTS
has no local-compatible endpoint, so a keyless setup still uses Kokoro for speech.

## Why a hand-rolled seam and not LiteLLM

A gateway like LiteLLM would abstract vision and text, but its transcription
wrapper only covers hosted ASR, so it cannot touch the existing local
faster-whisper stage, and it adds a dependency and an indirection layer for three
call sites. Three small typed interfaces (`VisionProvider`, `TextProvider`,
`TTSProvider`) plus a factory keep the seam readable in one file, let the demo run
through a `fake` provider with no special-casing, and leave the door open to point
the OpenAI client at a compatible server for free. The interface is the part that
matters; the specific model behind it is one line of config.
