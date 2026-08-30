# ADR-0004: SQS + DLQ with an ECS worker that runs the existing pipeline through a subprocess adapter

**Status:** Accepted; recovery and render behavior are amended by ADR-0009 and ADR-0010
**Date:** 2026-08-06

## Context

Background work today is a detached `subprocess.Popen` of `run_job.py` from the API process (`server.py:111–117`), with progress read from job-local status files. `run_job.py` deliberately sets `JOB_*` environment variables **before** importing `config.py`, because `config.py` reads its entire configuration from the environment at import time (`config.py:12–48`; `pyproject.toml` grants `run_job.py` an E402 exemption for exactly this). Rewriting the pipeline modules into an importable, injectable library first would put the whole migration on the critical path of its riskiest refactor.

## Decision

- **Queue:** one SQS standard queue plus a dead-letter queue with a bounded redrive policy (maxReceiveCount aligned with `jobs.max_attempts`).
- **Message:** identifiers only (`schemaVersion`, `messageId`, `taskType`, `jobId`, `requestedAt`) — the worker loads current settings from PostgreSQL.
- **Worker (v0.1):** one ECS Fargate task (`linux/amd64`), one job at a time. Per job it: claims the job via one conditional SQL update; creates an isolated temp working directory; downloads the source from S3 by its persisted object key; validates the media with `ffprobe` **before any model call** (corrupt/non-video/over-duration/type-mismatch → non-retryable failure); writes the settings file / `JOB_*` environment the pipeline already expects; invokes the existing pipeline **as a subprocess** (preserving the env-before-import contract); polls the pipeline's status/progress output and mirrors it into PostgreSQL; and removes the temp directory in a `finally` block.
- **The child's exit status is authoritative over `status.json`** — imports and settings parsing can fail before `run_job.py`'s `try/except`, and the pipeline writes legacy `ready` before `result.json`. Success therefore requires, in order: child exit 0 → required artifact set validated (source video + valid `scenes.json` at minimum) → objects uploaded → `artifacts` rows persisted → only then `READY_FOR_REVIEW`. Bounded stderr is captured; clients see a classified `error_code`, never raw tracebacks.
- **Retry alignment:** `maxReceiveCount` = `jobs.max_attempts`; a retryable failure is **one durable atomic transition `PROCESSING → QUEUED`** with attempt count and safe error metadata updated in the same operation (`RETRYING` is a logical/logged phase, never a persisted `jobs.status` — no two-write sequence can strand a job), message left undeleted; non-retryable validation failures delete the message; exhausted attempts leave the message for DLQ redrive — and duplicate handling acknowledges only *successful* terminal jobs so it never consumes a failure message before redrive.
- Artifact writes are idempotent via **attempt-scoped** keys
  (`jobs/{job_id}/attempts/{attempt}/…`) + upsert (G5.1 correction: unscoped
  "deterministic overwrite" keys let a stale attempt rewrite a winner's bytes
  after the DB fence — generated objects are therefore attempt-scoped, the
  winning rows are selected atomically in the success transaction, and any
  future manifest resolves ROWS, never constructs keys). The `source_video`
  row is the exact VERSIONED upload object: S3 versioning is enabled, upload
  verification requires a `VersionId`, and the worker downloads only that
  pinned version.
- Deeper extraction (`process_job(context, progress_sink, artifact_sink)`), leases/heartbeats, reclaim, and cancellation move to v0.2 unless evidence shows one is indispensable for the truthful v0.1 journey.

## Consequences

- The pipeline's environment-at-import coupling is **contained** behind a process boundary instead of refactored on the critical path.
- Progress granularity in v0.1 equals whatever the status files provide; finer stage telemetry arrives with v0.2's `pipeline_stage_runs`.
- Long jobs rely on a generous SQS visibility timeout in v0.1 (single worker, max 1 concurrent job); heartbeat-based extension is explicitly a v0.2 reliability item.
- The worker image must bundle ffmpeg and the ML stack (`requirements.txt`), so it is a different, larger image than the API image.
