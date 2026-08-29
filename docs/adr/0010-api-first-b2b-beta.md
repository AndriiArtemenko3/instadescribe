# ADR-0010: API-first B2B beta boundary

**Status:** Implemented and locally verified; not tagged, published or deployed

**Date:** 2026-08-28

## Context

Cloud Core v0.1 proves asynchronous analysis through the browser, but its one
portfolio token, global job listing and UI-only workflow are not a safe
multi-organization integration product. A CMS, DAM or university automation
needs a stable service contract, direct media transfer, reconciliation and
terminal notifications without inheriting browser credentials or internal
provider settings.

## Decision

FastAPI remains the sole business authority. PostgreSQL owns tenant identity,
quota, idempotency and state transitions; S3 contains private versioned bytes;
SQS carries identifiers to Python media workers. Node.js has three bounded
roles: an authenticated Next.js web application/thin JSON BFF, a server-side
TypeScript SDK and a CLI. Neither Next nor the SDK duplicates state transition,
tenant or scoring rules.

The public server-to-server API is `/api/integrations/v1` internally and `/v1`
at the canonical API origin. It uses Bearer service-account keys, camelCase
JSON, RFC 9457 problems, opaque cursor pagination and required 24-hour
idempotency keys for retryable writes. The browser API is a separate
`/api/app/v1` boundary; service keys never enter the browser.

Every Project and Job has a non-null `organization_id`. A Job-to-Project
composite foreign key prevents cross-tenant attachment, while every repository
read/write includes the principal organization predicate. A foreign identifier
and an absent identifier are indistinguishable (`404`). Existing rows and the
legacy `/api/v1` token belong only to the deterministic `portfolio`
organization.

The stable public lifecycle is:

```text
awaiting_upload → queued → processing → needs_review → rendering → completed
                                                   ↘ failed / cancelled
```

Internal orchestration may add states without changing this projection. A
Project represents one source; every re-run is a new Job. `externalId` is unique
per organization at Project scope and `clientReference` is unique per
organization at Job scope.

Video and optional timed VTT/SRT upload directly to constrained S3 POSTs. New
object keys preserve both lifecycle class and tenant identity, for example
`uploads/orgs/{organizationId}/jobs/{jobId}/...`. The API key is attached only
to the exact API origin and never to S3.

Review mutations remain in the web application. Finish Review is one
transaction: verify a decision for every scene, require explicit zero-AD
confirmation when no scene is approved, lock the review, create one Render and
enqueue it. MP4, MP3, SRT, CSV and DOCX stay staged until the complete checksummed
set succeeds; only then does the Job become completed and all Deliverables
become public together.

Terminal state changes and immutable `JobEvent` outbox rows share a database
transaction. Webhooks are at-least-once, use an immutable event ID and sign
`id.timestamp.rawBody` with HMAC-SHA256. Payloads contain safe identifiers,
state and timestamps only. The beta permits one operator-approved HTTPS endpoint
per organization and rejects redirects, literal/private/link-local addresses
and unapproved hosts.

## Security and reliability invariants

- Service keys use `idsb_live_<key-id>.<secret>` and store only a versioned,
  server-peppered HMAC digest. Multiple keys allow overlap rotation and
  individual expiry/revocation.
- FastAPI validates Cognito JWT claims and active membership on every browser
  request. Next keeps only an opaque `__Host-` cookie; encrypted provider tokens
  live in the TTL-backed session store. JSON mutations require CSRF and Origin
  checks. Media never passes through Next.
- Job create reserves quota atomically. Measured ffprobe duration is
  authoritative and must reconcile quota before the first paid provider call.
- Cancellation is idempotent and invalidates the active lease/fence. A stale
  worker cannot publish state or artifacts. Every acknowledged render version
  is journaled by exact key and VersionId; failure/cancel/stale paths delete
  only those identities, and a bounded SKIP LOCKED janitor retries durable
  terminal/stale journals. Published identities are always excluded.
- The unavoidable residual window is process death after S3 acknowledges a
  Put but before the journal commit. Worker IAM intentionally has no bucket
  listing or key-only delete, so that unjournaled version requires inventory
  or operator reconciliation instead of a broad automatic delete.
- Presigned downloads are tenant-authorized, version-pinned, short-lived and
  `private, no-store`. API, audit and webhook logs omit tokens, payloads, media
  URLs, prompts and raw provider errors.

## Beta defaults

- 600 media-minutes per organization/month;
- one processing, five awaiting-upload and ten queued Jobs per organization;
- video at most 1 GiB/60 minutes; UTF-8 timed transcript at most 10 MiB;
- source/intermediates 30 days, Deliverables 90 days, metadata/audit 365 days;
- awaiting upload expires after 24 hours and inactive review after 30 days;
- at most 120 approved scenes per review and two durable render claims, for an
  aggregate ceiling of 240 final-render TTS synthesis calls per review;
- TTS previews use a rolling 24-hour durable ledger: 25 requests per Job,
  100 per organization, five active per organization and three attempts per
  request; terminal failures and cancellations still consume the request
  window;
- preview windows are based on durable request creation time, not timestamps
  for each worker attempt; the API therefore advertises request ceilings and
  per-request attempts separately, not a paid-attempt rolling-window maximum;
- manual organizations, service keys and webhook endpoint; no billing or SLA.

## Release boundary

This ADR records implementation and local verification only. It does not claim a
release tag, npm publication, AWS apply, customer key or traffic cutover. An accepted
Cloud Core v0.2 compatibility/reliability gate is still required before beta
deployment. Vite remains the rollback build until the Next browser/media parity gate
passes. The Astro landing remains an independent static deployment.

The beta canary requires tenant-negative tests, concurrent idempotency/quota
tests, cancellation races, transcript no-fallback proof, complete five-format
publication, webhook replay/retry/SSRF tests, Cognito/MFA/CSRF tests, published
SDK tarball E2E, restore evidence and a rehearsed rollback.
