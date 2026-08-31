# Engineering history

InstaDescribe evolved by preserving a working authoring loop while moving one
durable boundary at a time. This is a concise public record; private audit logs,
operator handoffs and local worktree evidence are intentionally excluded from the
public snapshot.

## 1. Local human-in-the-loop pipeline

The first system combined a Flask server, React/Vite editor and filesystem-backed
pipeline. It established scene drafting, transcript-aware placement, editable review,
TTS and five export formats. A deterministic Sintel fixture made the interaction
replayable without provider credentials. The formative evaluation and its limits are
recorded in [evaluation.md](./evaluation.md).

## 2. Cloud Core v0.1

The first AWS vertical slice moved durable state to FastAPI/PostgreSQL, sent media
directly to private versioned S3 and transported work through SQS to an isolated
Python worker. It deliberately stopped at persisted scene editing and used a shared
portfolio token instead of tenant identity. The deployed boundary, immutable
identities and limitations are retained in the
[v0.1 release evidence](./releases/v0.1-cloud-core.md).

The foundational decisions are preserved in
[ADR-0001 through ADR-0008](./adr/0001-three-release-strangler-migration.md).

## 3. Reliability and review semantics

The next local increment introduced version-aware review updates, worker
leases/heartbeats, fencing and reclaim behavior so stale workers cannot publish after
cancellation or lease loss. It remains an implemented source boundary rather than a
separate public deployment. See
[ADR-0009](./adr/0009-review-concurrency-and-worker-leases.md).

## 4. API-first B2B beta

The current source introduces organizations, memberships, service accounts, scoped
API keys, quotas, idempotency, separate browser/integration APIs, Next.js/BFF,
TypeScript SDK/CLI, transactional webhook outbox and atomic five-format delivery.
FastAPI remains the business authority and Python remains the media execution layer.
See [ADR-0010](./adr/0010-api-first-b2b-beta.md), the current
[architecture](./architecture.md) and the
[before/after comparison](./architecture-evolution.md).

## 5. Observable Video Intelligence foundation

The next milestone introduced `video_investigation` as a workflow parallel to
audio description and published an autonomous Apache-2.0 investigation core. The
product integration adds tenant-scoped source/evidence/belief/decision persistence,
a Browser-only local contract, a dedicated queue, fenced worker execution and a
strict isolated child boundary.

Two deterministic acceptance scenarios cross creation, version-pinned upload,
queue, worker, persistence, analyst decision and report: one supports a hypothesis
and one requires abstention. They deliberately perform no model inference or
public-web request. The investigation workspace, live multimodal run, retrieval,
persisted replay and benchmark remain later milestones. See
[Video-investigation foundation](./investigation-architecture.md) and
[ADR-0011](./adr/0011-observable-video-intelligence.md).

## Release truth

- Cloud Core v0.1 is the only public AWS deployment represented here. Its release
  packet records dated deployment and health evidence; this repository makes no
  claim about its current availability or support status.
- The API-first beta is implemented and locally verified, not yet cut over.
- The SDK and CLI are present in source and are not yet published to npm.
- The investigation foundation is implemented and fixture-verified in source, not
  deployed and not yet exposed through an analyst workspace.
- No live customer, billing, SLA, production-readiness or legal-compliance claim is
  made.
