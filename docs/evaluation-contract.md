# Evaluation contract (v1, frozen at G8)

This is the ADR-0008 §4 pre-G8 deliverable: a frozen, machine-validated
evaluation manifest plus the manual rubric **protocol**. It complements —
and deliberately does not restate — the automated five-dimension scorer
described in [docs/evaluation.md](evaluation.md) (whose Python/TypeScript
implementations stay pinned to `tests/fixtures/eval_sample.json`, a
synthetic drift-guard fixture that is distinct from the media corpus
defined here). This document defines a protocol; it reports no new human
labels, study results, or model-quality claims.

## The frozen manifest

`tests/fixtures/evaluation/manifest.v1.json` — schema
`instascribe-eval-manifest/1`, validated fail-closed by
`tests/test_evaluation_manifest.py`. Validation refuses unknown schema
versions, a missing source asset, hash/size/licence mismatches, duplicate
case IDs, overlapping or out-of-range windows, incomplete artifact
expectations, and unresolved expectation keys.

### Cases (v1)

Four non-overlapping windows of the one committed rights-cleared fixture:

| Case ID | Window | Purpose |
|---|---|---|
| `sintel-v1-w1-opening` | 0–30 s | dialogue-sparse establishing: coverage and gap placement |
| `sintel-v1-w2-dialogue` | 30–60 s | first dialogue exchange: timing against real speech |
| `sintel-v1-w3-action` | 60–90 s | action density: audio events and segmentation under motion |
| `sintel-v1-w4-resolution` | 90–120 s | tail: end-of-source bounds and final-scene assembly |

**Honest diversity limitation (precise contract, per G8.1):** G8 freezes
3–5 rights-cleared evaluation **cases**. At G8, non-overlapping bounded
windows from one verified rights-cleared source are permitted as the safe
minimum. They freeze the harness, provenance and rubric, **not corpus
diversity**. These four cases are four windows of ONE film — they are not
four independent source clips. Multiple distinct rights-cleared source
clips are a v0.2 benchmark requirement and require owner/licence review
**before** any new source is downloaded or committed.

### Rights and provenance

- **Source:** Sintel (~120 s segment) — © copyright Blender Foundation,
  [durian.blender.org](https://durian.blender.org).
- **Licence:** [Creative Commons Attribution 3.0 (CC BY 3.0)](https://creativecommons.org/licenses/by/3.0/)
  (see `THIRD_PARTY_NOTICES.md`).
- **Asset:** `App/public/videos/sintel-blender-cc.mp4` — SHA-256
  `75de21cc8ed60ece2d43e0127db8449e4eef0ae44ca47a586791945de0e5648b`,
  8,940,006 bytes, measured duration 120.000 s. No new media was added
  for this contract.

### Real vs fake stages (disclosure)

Runs driven by this contract execute **real** FFmpeg demux/extraction,
voice-activity detection, faster-whisper ASR from baked offline weights,
and real S3-compatible/SQS/PostgreSQL infrastructure — locally. The
vision-model scene description is the **deterministic fake provider**; no
paid or external model call occurs, and no real-provider output quality is
claimed. The committed demo data under `App/public/data/sintel-blender-cc/`
is the real pipeline's historical output and is **pre-generated** — it is
not produced by these cases.

### Keyless replay / checksum commands

```
shasum -a 256 App/public/videos/sintel-blender-cc.mp4
uv run --with-requirements requirements-dev.txt pytest tests/test_evaluation_manifest.py -q
```

## Manual rubric (protocol, not results)

Two 1–5 scales, scored per case (or per timecoded moment within a case):

- **Groundedness** — 1: describes events not present on screen …
  5: fully grounded in visible action.
- **Usefulness** — 1: unusable for a blind listener …
  5: clear, well-timed, sufficient to follow the story.

Each review row records: `reviewer`, `caseId`, `timecode`,
`groundednessScore` (integer 1–5), `usefulnessScore` (integer 1–5),
`rationale` (one or two sentences), and `errorCategory` from the closed
taxonomy: `hallucination`, `omission`, `timing_overrun`,
`dialogue_collision`, `character_error`, `style_mismatch`, `other`.
Both scores are REQUIRED bounded integers — a single ambiguous `score`
field is not a valid record (G8.1 A3); the review-record validator in
`tests/test_evaluation_manifest.py` fails closed on missing, boolean,
non-integer or out-of-range values. No completed review rows exist yet —
this remains a protocol, not an evaluation result.

This rubric is the human complement to the automated score's acknowledged
grounding gap (docs/evaluation.md, "Honest limits"): the in-editor score
proxies grounding mechanically; a human reviewer judges it directly. The
previously documented 10-participant study results are historical evidence
and are unchanged by this contract.

## Deferred to v0.2 (per ADR-0008 §4)

Completion rate, structured-output validity rates, dialogue-gap/timing
fit, TTS overflow, loudness/assembly validation, retry/failure counts,
latency and estimated cost per source minute, qualitative error-taxonomy
reporting, and version-to-version comparisons.
