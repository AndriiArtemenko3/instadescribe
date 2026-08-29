# ADR-0008: Project/job boundary, evaluation evidence, cost posture, and conditional Next.js

**Status:** Accepted. Amends specific points of ADR-0001 (release framing), ADR-0002 (data model), and ADR-0005 (worker cost posture); those ADRs reference this one rather than restating. ADR-0003/0006/0007 are unchanged by this ADR.
**Date:** 2026-08-06

## 1. Projects vs processing jobs (G2.5)

A **project** is the durable user-owned work item; a **job** is one processing execution/version belonging to a project. The physical `jobs` table and the public `/jobs` route names are retained for bounded v0.1 compatibility, but code and documentation define each `jobs` row as a *processing job*. Migration `0002` adds durable `projects` (owning `name`/`starred` and future project-level lifecycle), a non-null `jobs.project_id` FK (CASCADE), and immutable server-supplied `jobs.pipeline_revision` provenance (`dev` locally, the immutable code/image revision in deployment, the honest sentinel `unknown-pre-g3` for backfilled rows — never an unproven revision). Artifacts and scene overrides stay job-owned: they belong to a specific generated result; project ownership is reachable through `jobs.project_id`.

## 2. Creation vs the compute slot (G3/G4 semantics)

`AWAITING_UPLOAD` is deliberately **outside** `uq_jobs_one_compute_active`, so `POST /api/v1/jobs` does **not** reserve the compute slot, and multiple pending upload reservations may coexist — that is correct behavior, not a bug. A count-before-insert check is never the concurrency guarantee. The database guarantee begins when G4 conditionally transitions a job into `UPLOAD_COMPLETE`/`QUEUED`: G4 must catch the partial-index unique violation and return a safe conflict/retry response. G3 may add an advisory active-job preflight for UX only; it must not reject harmless `AWAITING_UPLOAD` rows or be described as race-safe enforcement. The global cap currently subsumes the one-active-job-per-project rule; any future relaxation must first add an explicit per-project active-job constraint.

## 3. Review-state honesty and immutable originals (G6+)

Original generated scene artifacts remain **immutable**; user overrides are stored separately (already the schema shape). The precise v0.1 claim is **"persistent human edit"** — not "review" — until explicit review decisions exist. v0.2 adds explicit generated/edited/approved/rejected states (optionally regeneration-requested) with timestamps and versioning; `active` is never overloaded as review status. **G6 engineering note:** Core `ON CONFLICT DO UPDATE` upserts must explicitly set `updated_at = now()` and increment `version` — ORM `onupdate` does not fire for Core upserts.

## 4. Applied-AI evaluation evidence (building on what exists)

The project already holds real evaluation assets: `docs/evaluation.md`, the shared Python/TypeScript five-dimension scorer pinned to one fixture, documented 10-participant results (accuracy 4.4/5, usefulness 4.2/5, trust 4.0/5), Sintel CC BY 3.0 attribution, and G0's offline fake-provider replay through real FFmpeg/VAD/ASR. These are preserved and built on:
- **Pre-G8 deliverable — evaluation contract:** a frozen manifest of 3–5 rights-cleared evaluation **cases** (source, licence, hash), deterministic structural expectations per case, and a short manual groundedness/usefulness rubric. No media is added and no rights are claimed without verification. *G8.1 forward clarification:* at G8, non-overlapping bounded windows from ONE verified rights-cleared source are permitted as the safe minimum — they freeze the harness, provenance and rubric, **not corpus diversity**; multiple distinct rights-cleared source clips are a v0.2 benchmark requirement and require owner/licence review before addition.
- **v0.2 benchmark must report:** completion rate, structured-output validity, dialogue-gap/timing fit, TTS overflow, loudness/assembly validation, retries/failures, latency and estimated cost per source minute, a qualitative error taxonomy, and version-to-version comparisons.
- **Public evidence packets must state:** fixture licensing, which stages are real vs fake, what is pre-generated, and exact keyless replay/checksum commands.

## 5. Worker cost posture: G9-selected bounded evidence mode (D7 resolved locally)

An indefinitely always-on 2 vCPU/8 GB worker (~$97.82/mo compute alone) is **not** the default. G9.1
selects fail-closed bootstrap: API and worker desired counts zero; one API only after a one-shot
immutable-image migration reaches Alembic head and a second reviewed plan/apply is authorized; one
worker only during controlled G11/G12 tests. Its provisional 40-GiB ephemeral storage adds a charged
20-GiB increment above the included allocation and requires native G11 measurement. The intended
maximum 72-hour environment window is distinct from S3 deletion timing. This is manual v0.1 cost
control and does not claim queue-depth autoscaling or automatic scale-to-zero, which remain v0.2. No
AWS behavior or bill is validated until later authorized gates.

## 6. Next.js is a conditional post-v0.2 decision gate

v0.3 Next.js is a **candidate**, not the definition of project completion. The detailed migration plan (repo-audit §8, release-plan §4) is retained as that candidate's design. After the v0.2 evidence gate, its employability value is compared against editor UX, evaluation depth, accessibility validation, reliability, and cost work — and the owner picks. ADR-0001's three-release framing stays; the third release's *content* is decided at that gate.

## Consequences

Documentation, schemas, and claims stay honest about what is enforced where (slot at G4, not creation), what a row means (processing job), what the user studies actually measured, and what AWS will cost. The strangler adapter (legacy summary shape with `id` = processing `jobId`, explicit `projectId` alongside) is documented so no code treats the two IDs as interchangeable.
