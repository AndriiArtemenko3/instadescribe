# InstaScribe

A human-in-the-loop tool for authoring audio descriptions for video. It drafts a
description for every scene with AI, lets a person edit and approve each one, and
mixes the spoken description into the video's natural gaps between dialogue.

[![CI](https://github.com/AndriiArtemenko3/InstaScribe_Video_Description_Pipeline/actions/workflows/ci.yml/badge.svg)](https://github.com/AndriiArtemenko3/InstaScribe_Video_Description_Pipeline/actions/workflows/ci.yml)
[![Core: BUSL-1.1](https://img.shields.io/badge/core-BUSL--1.1-orange.svg)](./LICENSE)
[![SDK / CLI: MIT](https://img.shields.io/badge/SDK%20%2F%20CLI-MIT-blue.svg)](./LICENSING.md)

> Tested with 10 participants: drafts rated accurate (4.4/5) and useful (4.2/5),
> and trusted enough to edit rather than rewrite (4.0/5).

[Quick start](#quick-start) · [Results](#evaluation-and-results) · [Architecture](./docs/architecture.md)

## Why this exists

Audio description makes video accessible to blind and low-vision audiences, but
writing it by hand is slow, skilled work, and most short-form video never gets it.
InstaScribe does not try to replace the author. It removes the blank-page step: the
model proposes a draft for every scene, and the author stays in control of what ships.

## What it does

- Splits a video into scenes and drafts a description for each from the frames.
- Puts every draft in an editor where the author rewrites, approves, or rejects it.
- Detects speech with voice-activity detection and transcription, so descriptions
  land in the gaps between dialogue rather than over it.
- Renders a finished video with the description mixed in at broadcast loudness.

## How it works

```
video → scene segmentation → frame sampling → per-scene draft (gpt-4.1 vision)
      → human edit-and-approve loop → speech detection + transcription (silero-vad,
        faster-whisper) → gap-aware placement with a collision check
      → text-to-speech (tts-1-hd) → loudness-matched ffmpeg mix → described video
```

The vision, rewrite, and speech steps run through a provider interface: OpenAI by
default, Claude or Gemini with a key, or fully local with Ollama (Qwen2.5-VL) plus
Kokoro and no API key at all. Full diagram and the deploy model are in
[docs/architecture.md](./docs/architecture.md); provider setup and the local-quality
tradeoff are in [docs/local-models.md](./docs/local-models.md).

Decisions that shaped it:

- **The model drafts; the person decides.** It is slower than full automation, but
  output a blind listener relies on should not ship unreviewed. The study backed the
  call: people used the edit step rather than accepting drafts blindly.
- **Descriptions sit in curated gaps.** A collision check keeps narration off the
  dialogue track, so a description never talks over a line.
- **A draft per scene, not per frame.** Sampling frames and describing a scene as a
  unit is cheaper and closer to how a human describer thinks in shots.
- **A rolling character memory.** Identities established in one chunk of frames carry
  forward, and a rename re-renders every dependent scene through a pronoun-aware
  template so the narration stays grammatical.

## Quick start

### Try it in one command (no API key)

```bash
make demo
```

Builds and serves the browser app on a committed sample clip. Every model step is
served from baked fixtures, so it needs no key, no backend, and nothing beyond
Node. Open the printed URL and edit a real description in the editor.

### Run the full pipeline on your own video

Prerequisites: Python 3.12, Node 20+, and ffmpeg on your PATH (`brew install ffmpeg`).

```bash
make install                              # web deps + a .venv with the pipeline
cp .env.example .env                      # pick a model backend (below)
make server                               # single-origin app + API at :8765
```

Open http://localhost:8765, upload a short clip, edit and approve each
description, preview the mixed audio, then export the described video.

Pick a model backend in `.env` (`INSTASCRIBE_BACKEND`):

- **OpenAI** (default) — set `OPENAI_API_KEY`.
- **Claude** — `anthropic`; set `ANTHROPIC_API_KEY` and `pip install -r requirements-providers.txt`.
- **Gemini** — `gemini`; set `GEMINI_API_KEY` (no extra install).
- **Fully local, no key** — `local`: install [Ollama](https://ollama.com), pull
  `qwen2.5vl:7b` + `qwen2.5:7b`, and `pip install -r requirements-local.txt` for
  local TTS.

Vision, Smart Fill, and TTS each run through a provider interface, so the model
behind them is a config change, not a code change. Setup and the local-quality
tradeoff: [docs/local-models.md](./docs/local-models.md).

## Evaluation and results

Tested with 10 participants over two days (students; 9 sighted; mixed familiarity
with audio description). On a 1–5 scale:

| Dimension | Score |
|---|---|
| Description accuracy | 4.4 |
| Draft usefulness | 4.2 |
| Eyes-closed clarity (proxy) | 4.1 |
| Trust in the tool | 4.0 |
| Sense of control | 3.9 |
| Ease of finding and fixing errors | 3.8 |

Usability ran a 7-item index (a partial, non-standard SUS) scoring 69.6; people
learned the tool quickly and found it well integrated.

Honest limits, stated up front:

- Participants were students, not professional describers, and mostly sighted, so
  an eyes-closed task stood in as a proxy for a blind listener.
- Interaction logs ran on an ephemeral disk and were not retained, so this rests on
  the questionnaire and open-text answers.

## Reliability: what broke and how I fixed it

The work that made the tool usable lived in failures that only show up in real output:

- **An override race** made one scene's narration apply to all twelve. The fix was a
  per-job lock plus atomic writes around the shared override file, so concurrent edits
  stop clobbering each other.
- **A 6 dB loudness drop** in the mix, because ffmpeg's `amix` normalises across its
  inputs. The fix mixes with `normalize=0`, ducks the background per gap by measured
  LUFS, and caps peaks with a limiter (two-pass EBU R128).
- **A miscalibrated collision check** compared each description against dialogue
  instead of against the curated gaps, so it flagged the wrong overlaps.

## Built with

Python 3.12 · Flask · a pluggable model backend — OpenAI, Claude, or Gemini, or
fully local (Ollama Qwen2.5-VL + Kokoro) · faster-whisper · silero-vad · ffmpeg ·
React 19 · Vite · TypeScript · Tailwind · shadcn/ui · TanStack Query · Zustand ·
deployed on Fly.io.

## Project layout

```
modular_pipeline/   Flask server + the AD pipeline (frames, audio, vision, TTS, export)
App/                React + Vite editor (feature-folder structure)
docs/               architecture and evaluation notes
tests/              pytest (pipeline) + vitest lives under App/
```

## Media

The bundled demo clip is **Sintel** — © Blender Foundation,
[durian.blender.org](https://durian.blender.org), licensed
[CC BY 3.0](https://creativecommons.org/licenses/by/3.0/). Full attribution:
[THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md).

## License

The product core is source-available under [BUSL-1.1](./LICENSE). The TypeScript
SDK and Node CLI are separately open source under MIT. Earlier versions already
published under MIT remain MIT. See [LICENSING.md](./LICENSING.md) for the exact
scope and [COMMERCIAL_LICENSE.md](./COMMERCIAL_LICENSE.md) for production-use
licensing.
