# ADR-0009: Conflict-safe human review and recoverable worker leases

**Status:** Implemented locally; not released or deployed as a separate public version

**Date:** 2026-08-10
**Amends:** ADR-0002 scene-override concurrency and ADR-0004 worker recovery

## Context

The v0.1 Cloud Core deliberately stopped at one persistent human edit and a
single manually controlled worker. That was sufficient for the first truthful
AWS release, but it left two limitations that matter to a reliable review workflow:

1. scene edits were atomic but last-write-wins, and `active` was not a review
   decision; and
2. a worker that died while a job was `PROCESSING` left that job stranded until
   an operator repaired it.

Project rename and star operations were also still fenced in the cloud client,
despite `projects` already being the durable product entity.

## Decision

### Human review and optimistic versions

- The immutable generated `scenes.json` artifact remains the original model
  output. Human changes continue to live only in `scene_overrides`.
- A stored override has one explicit review state: `edited`, `approved`, or
  `rejected`. `generated` is the response state inferred when no override row
  exists; a client cannot relabel retained human text as generated.
- Approval and rejection record `reviewed_at`. Editing any content after a
  decision moves the row back to `edited` and clears that timestamp.
- A first scene write uses expected version zero. Every later write must match
  the exact positive version. PostgreSQL performs the conditional mutation and
  one concurrent writer wins; a loser receives sanitized `409 stale_version`.
- Project rename and star use the same principle with the existing project
  version. Job create/list/get responses carry `projectVersion` so the client
  never infers it from local state.
- The client serializes writes per scene, advances the acknowledged version
  only from validated responses, and preserves a newer local draft if an older
  request completes or a stale-version conflict occurs.

This is optimistic concurrency, not collaborative real-time editing. Delete,
multi-user identity, comments, and a complete audit log remain outside this
increment.

### Worker lease, heartbeat, and reclaim

- PostgreSQL is the ownership authority. A successful claim assigns a fresh
  fencing token, increments the attempt once, and sets a database-clock lease.
- The initial defaults are a 300-second database lease, a 60-second heartbeat,
  and 300-second SQS visibility renewal. Configuration fails closed if the
  heartbeat does not leave a safe missed-beat margin or queue visibility is
  shorter than the database lease.
- The existing subprocess supervisor drives the heartbeat synchronously. There
  is no background thread or second database session to outlive the child.
- Each due pulse renews the exact live database lease and then the current SQS
  receipt. Lease loss stops and reaps the process tree. A database outage makes
  no stale mutation. A queue-heartbeat failure follows the bounded retry path.
- Progress, failure transitions, and final artifact publication require both
  the exact fencing token and an unexpired lease.
- A redelivery may reclaim an expired `PROCESSING` row with a new token and a
  new attempt. Concurrent reclaimers still have one database winner. A legacy
  v0.1 `PROCESSING` row with a null lease is treated as expired.

The delivery model remains **at least once**. Blocking S3 or filesystem calls
can outlast a lease; in that case duplicate compute is possible, but stale
database mutations and terminal publication remain fenced. No exactly-once or
unbounded self-healing claim is made.

## Consequences

- The editor can now distinguish generation from human editing and explicit
  approval/rejection without overloading inclusion in export.
- Browser tabs and concurrent writers cannot silently overwrite a newer scene
  or project mutation.
- A worker death no longer permanently strands a `PROCESSING` job once the
  message is redelivered and the lease has expired.
- Retry attempts may leave attempt-scoped S3 objects, as already permitted by
  ADR-0004; only the lease owner can publish winning artifact rows.
- Cancellation, automatic worker scale-to-zero, export, full observability,
  Terraform/CI hardening, and the complete benchmark remain later v0.2 work.
- This ADR authorizes no AWS mutation. Deployment requires a separate reviewed
  image, migration, Terraform, readiness, rollback, and smoke-test gate.

## Mandatory deployment compatibility gate

This increment intentionally changes the browser/API wire contract: job responses
gain required `projectVersion` data and scene mutations gain required optimistic
`expectedVersion` semantics. The released v0.1 browser validates exact response
shapes, while cached or already-open v0.1 clients do not send the new scene
version. There is therefore no proven safe rolling order for these changes.

Live AWS must remain on v0.1 and this increment must not be deployed until a
versioned compatibility path is implemented and tested. The preferred path is a
new `/api/v2` contract while retaining `/api/v1` for the released client during
cutover. The deployment gate must prove, at minimum:

- cached and open v0.1 clients continue to use `/api/v1` successfully;
- the v0.2 client uses only the versioned optimistic contracts;
- mixed-version requests fail safely without losing edits;
- rollback to the v0.1 frontend and API contract is tested; and
- the old contract is removed only after its cache/open-client window is closed.

Passing local API, worker, or frontend tests in this increment is not evidence that
the compatibility/cutover gate has passed, and no v0.2 deployment claim is
permitted before it does.
