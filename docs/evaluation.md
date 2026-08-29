# AD-quality evaluation

InstaDescribe scores its own audio-description output against a rubric, so mechanical
problems surface in the editor before a single line is rendered. The scorer is pure
and dependency-free: the same logic runs in the API (`modular_pipeline/evaluation.py`),
in the editor's Quality tab (`App/src/lib/evaluation.ts`), and in tests. A shared
fixture (`tests/fixtures/eval_sample.json`) pins both implementations to one expected
score, so the Python and TypeScript versions cannot silently drift.

## The five dimensions

Each is scored 0–1 over the active, non-empty descriptions, then combined by weight.

| Dimension | Weight | What it checks |
|---|---|---|
| Timing fit | 0.25 | The narration fits inside the scene's time window (words × 0.4 s, adjusted for speed). |
| Dialogue safety | 0.30 | The narration window does not overlap dialogue beyond a half-second tolerance. |
| Coverage | 0.15 | Share of the video duration that carries an active description. |
| Character consistency | 0.15 | Every `character_id` a scene references resolves to a known entity. |
| Grounding | 0.15 | Proxy: penalises duplicated descriptions across scenes. |

Timing and dialogue safety carry the most weight because those are the two failures a
blind listener notices first: a description that runs past its gap, or one that talks
over a line of dialogue.

Alongside the scores, the harness returns per-scene flags
(`narration_too_long`, `dialogue_collision`, `orphan_character`, `duplicate_text`), so
the Quality tab can list the exact scenes to fix and jump straight to each one.

## How it relates to the human study

The 10-participant study measured *perceived* quality — whether people found the drafts
accurate (4.4/5), useful (4.2/5), and trustworthy (4.0/5). This harness measures
*mechanical* quality: the constraints a description must satisfy to be usable at all.
They are complementary. The study answers "is the writing good?"; the harness answers
"does it fit the gap, stay off the dialogue, and reference real characters?" — the
checks a human reviewer would otherwise run by hand on every scene.

## Honest limits

- **Grounding is a proxy.** It flags duplicate and empty descriptions; it does not yet
  verify a description against the actual frames. True visual grounding needs a vision
  model call and is out of scope for the zero-cost in-editor score.
- The reading-speed model is a single average (≈150 wpm); it does not account for
  punctuation pauses or proper-noun density.
