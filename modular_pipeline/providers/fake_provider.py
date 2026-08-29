"""Deterministic, offline providers.

They make no network call and need no key, model, or GPU, so they power the test
suite and a keyless server smoke run (INSTADESCRIBE_BACKEND=fake). The frontend
demo build has its own equivalent in App/src/lib/demoApi.ts; this is the backend
mirror of that idea. TTS uses the worker image's existing ffmpeg binary; output
is a canary tone and placeholder text, not a real description.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

from .base import CaptionResult, ProviderError, TextResult

_WPS = 2.3  # AD delivered at ~2.3 words/sec, matching the server + demo constants
_MIN_FAKE_AUDIO_SECS = 0.4
_MAX_FAKE_AUDIO_SECS = 30.0
_FAKE_TTS_TIMEOUT_SECS = 30


class FakeVisionProvider:
    name = "fake"

    def caption_chunk(
        self, *, developer_prompt, user_text, frames, schema, image_detail="low"
    ) -> CaptionResult:
        start = frames[0].timestamp if frames else 0.0
        end = frames[-1].timestamp if frames else 0.0
        # The last synthetic scene spans one sampling interval past its frame:
        # real providers never emit zero-length scenes (see the committed demo
        # fixture), and the G5.1 artifact contract enforces end > start, so
        # the placeholder geometry must respect the same schema.
        if len(frames) > 1:
            interval = max(frames[-1].timestamp - frames[-2].timestamp, 0.1)
        else:
            interval = 1.0
        last_end = (frames[-1].timestamp + interval) if frames else 0.0
        scenes = [
            {
                "scene_id": i,
                "start": f.timestamp,
                "end": (frames[i + 1].timestamp if i + 1 < len(frames) else last_end),
                "frame_indices": [f.index],
                "character_ids": [],
                "ad": f"Placeholder description at {f.timestamp:.1f} seconds (no model call).",
                "ad_template": "Placeholder description (no model call).",
                "reason_for_split": "fake provider: one scene per frame",
            }
            for i, f in enumerate(frames)
        ]
        data = {
            "chunk_id": 0,
            "chunk_start": start,
            "chunk_end": end,
            "global_summary": "Placeholder summary produced by the fake provider.",
            "scenes": scenes,
            "memory_updates": {"seen_character_ids": [], "new_characters": []},
        }
        return CaptionResult(data=data, model="fake", usage={"total_tokens": 0})


class FakeTextProvider:
    name = "fake"

    def rewrite(self, *, system, user, temperature=0.4, max_tokens=400) -> TextResult:
        # Pull the description and word budget out of the smart-fill prompt, then
        # trim deterministically — keep the leading clause, drop the tail.
        text = user
        if "Current description:" in user:
            text = user.split("Current description:", 1)[1].strip().split("\n\n", 1)[0].strip()
        words = [w for w in text.split() if w]
        m = re.search(r"~(\d+)\s+words", user)
        budget = int(m.group(1)) if m else max(3, len(words) // 2)
        kept = words[: max(3, budget)]
        ad = " ".join(kept)
        if kept and len(kept) < len(words):
            ad = ad.rstrip(",;:") + "."
        return TextResult(text=ad or text, model="fake", tokens=0)


class FakeTTSProvider:
    name = "fake"
    voices = ("onyx",)

    def synthesize(self, *, text, voice, out_path: Path) -> Path:
        del voice  # all stable product voice aliases map to the same canary tone
        out_path.parent.mkdir(parents=True, exist_ok=True)
        word_count = len(text.split())
        duration = max(_MIN_FAKE_AUDIO_SECS, min(_MAX_FAKE_AUDIO_SECS, word_count / _WPS))

        # Production worker images deliberately exclude App/browser fixtures.
        # A quiet deterministic tone is non-silent (so two-pass loudnorm can
        # process it) and gives both preview and full-bundle canaries a real
        # MP3 without any provider or network dependency.  The lavfi expression
        # contains only bounded constants computed above; no user text reaches
        # the ffmpeg command line.
        with tempfile.NamedTemporaryFile(
            prefix=f".{out_path.name}.", suffix=".mp3", dir=out_path.parent, delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
        try:
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
                    f"sine=frequency=440:sample_rate=24000:duration={duration:.3f}",
                    "-filter:a",
                    "volume=0.08",
                    "-ac",
                    "1",
                    "-ar",
                    "24000",
                    "-codec:a",
                    "libmp3lame",
                    "-b:a",
                    "48k",
                    "-map_metadata",
                    "-1",
                    str(temporary_path),
                ],
                check=True,
                capture_output=True,
                timeout=_FAKE_TTS_TIMEOUT_SECS,
            )
            if not temporary_path.is_file() or temporary_path.stat().st_size == 0:
                raise ProviderError("fake TTS synthesis produced no audio")
            temporary_path.replace(out_path)
        except ProviderError:
            raise
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProviderError("fake TTS synthesis failed") from exc
        finally:
            temporary_path.unlink(missing_ok=True)
        return out_path
