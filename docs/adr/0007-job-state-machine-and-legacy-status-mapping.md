# ADR-0007: Explicit job state machine with lossless mapping onto the legacy frontend status vocabulary

**Status:** Accepted for the legacy compatibility API; ADR-0010 defines the stable beta projection
**Date:** 2026-08-06

## Context

Today's job lifecycle is a set of ad-hoc status strings written to `status.json`: the server seeds `queued` (`server.py:96–107`), `run_job.py` writes `processing` → `ready`/`failed` with stage strings, and readers synthesize `not_found`/`error` for missing/corrupt files (`storage.py:60–72`). The frontend branches on exactly `queued | processing | ready | failed` (plus skipping `unknown`/`not_found` in reconcile) and displays the stage strings `queued, initializing, extracting_frames, transcribing_audio, analyzing_frames, exporting, complete` verbatim (`App/src/lib/uploadApi.ts:18,75`; `App/src/features/upload/components/StepProgress.tsx:6–14`). Transitions are unguarded assignments — nothing prevents an illegal move.

## Decision

- Persisted internal states: `AWAITING_UPLOAD → UPLOAD_COMPLETE → QUEUED → PROCESSING → READY_FOR_REVIEW → EXPORT_QUEUED → EXPORTING → COMPLETED`, plus terminal `FAILED` and `CANCELLED`. **`RETRYING` is not a persisted status** — a retry is one durable atomic `PROCESSING → QUEUED` transition (attempt/error metadata updated in the same operation), with "retrying" existing only as a logical/logged phase. The queue-publication recovery edge `UPLOAD_COMPLETE → PROCESSING` is legal (worker claims a job whose post-send `→ QUEUED` update failed). v0.1 exercises the subset through `READY_FOR_REVIEW`; export states and `CANCELLED` activate in the later source boundary.
- All transitions go through one service function with an explicit legal-transition table; illegal transitions raise a typed domain error and are unit-tested.
- The public API maps internal → legacy for the existing frontend, losslessly for everything the frontend distinguishes:

| Internal | API `status` | Notes |
|---|---|---|
| `AWAITING_UPLOAD`, `UPLOAD_COMPLETE`, `QUEUED` | `queued` | pre-processing states are indistinguishable to today's UI |
| `EXPORT_QUEUED`, `EXPORTING` | `processing` | exhaustive compatibility for the v0.2 export states |
| `PROCESSING` | `processing` | stage strings passed through unchanged from the pipeline's `status.json` |
| `READY_FOR_REVIEW`, `COMPLETED` | `ready` | `COMPLETED` (final export exists) is a v0.2 distinction |
| `FAILED`, `CANCELLED` | `failed` | `error_code`/`error_message` replace raw tracebacks |

- Stage strings and progress checkpoints (2→5→15→16→25→25+63·done/total→88→100, `run_job.py:119–139…395`) are mirrored into PostgreSQL exactly as the worker observes them — no renaming, so `StepProgress` needs zero changes.
- Terminal states acknowledge duplicate queue messages without reprocessing.

## Consequences

- The v0.1 frontend diff contains no status-model changes at all; the richer internal vocabulary is free to grow in v0.2 (exports, cancellation) without breaking the v0.1 client.
- `/api/v1` responses stay legacy-shaped during the migration; a future v2 may expose internal states directly to a Next.js client (v0.3 decision, not now).
- The mapping table is itself test-fixtured so a state added without a mapping fails CI.
