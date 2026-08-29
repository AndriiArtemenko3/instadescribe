# ADR-0002: FastAPI + PostgreSQL (RDS) replaces Flask + per-job file trees as the durable source of truth

**Status:** Accepted; amended by ADR-0008 and ADR-0010
**Date:** 2026-08-06

## Context

Durable state today is a filesystem convention: per-job directories holding settings, status, generated analysis JSON, scene overrides, posters, TTS cache, and exports (created by `modular_pipeline/server.py`, `run_job.py`, and `storage.py`; runtime dirs like `modular_pipeline/jobs/` are gitignored). Concurrency is protected only by in-process locks and atomic file replaces, which cannot coordinate multiple containers. A host restart loses any in-flight process; a container redeploy loses everything not on a mounted volume.

## Decision

- **API:** Python 3.12, FastAPI, Pydantic v2, Uvicorn/Gunicorn under `services/api/`.
- **Database:** PostgreSQL on Amazon RDS; **synchronous** SQLAlchemy 2.x, Alembic migrations, psycopg 3 (no async engine for the bounded v0.1 path). Each service owns a compiled, fully pinned dependency manifest (`requirements.in` → `uv pip compile`) — the root `requirements.txt` is floor-pinned and contains none of these packages.
- **Schema conventions (fixed):** UUID primary keys, UTC `TIMESTAMPTZ`, JSONB for job settings, foreign keys with `ON DELETE CASCADE`, explicit indexes, and `VARCHAR`/`TEXT` + `CHECK` constraints instead of PostgreSQL enum types.
- Core tables for v0.1: `projects` (durable work item — added by migration `0002`, ADR-0008 §1), `jobs` (**processing jobs** — one execution/version each, FK to projects, immutable `pipeline_revision` provenance), `artifacts`, `scene_overrides` (+ `pipeline_runs`/`pipeline_stage_runs` as v0.1-lean/v0.2-complete per the release plan).
- State transitions happen through repository/service methods with an explicit legal-transition table; the public API maps internal states to the frontend's existing lower-case status strings during migration.
- The existing Flask server is not deleted in v0.1; it remains the local/study path until parity is complete, and the committed fixture demo stays a static, cloud-independent build.

## Consequences

- Job state survives API restarts and container replacement — a v0.1 acceptance criterion.
- Scene overrides move from whole-file read-modify-write under a per-job lock to **atomic last-write-wins row upserts** (unique `(job_id, scene_id)`) — already strictly safer than the legacy whole-file race. A `version` column is recorded from day one, but stale-version **conflict rejection (409) is deferred to v0.2**: the current editor sends no version, so v0.1 optimistic-conflict enforcement would be untestable against the real client.
- Alembic becomes part of the deployment path (explicit migration step in v0.1, one-off ECS task by v0.2).
- Two persistence models coexist during migration; the release plan bounds this by keeping the cloud path authoritative for cloud jobs and the fixture demo fully static.
