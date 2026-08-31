# ADR-0011: observable video intelligence as a parallel workflow

- Status: Accepted
- Date: 2026-08-30
- Deciders: Andrii Artemenko

## Context

The API-first audio-description beta established reusable identity, tenancy, direct
media transfer, asynchronous jobs, worker fencing, audit and Browser API boundaries.
Video investigation needs those platform guarantees, but its evidence, uncertainty
and analyst-decision model is not an audio-description scene review with different
labels.

The hackathon target is a three-minute, local-first demonstration on an M4 Pro with
24 GB memory. It must expose why a hypothesis changed, preserve source lineage and
abstain when evidence is insufficient. It must not become a targeting, live-tracking,
face-recognition or operational-coordinate system.

## Decision

Create an autonomous `packages/investigation-core` package licensed under
Apache-2.0. It may not import the BUSL application. It owns dependency-light
evidence, belief, action, keyframe, trace, IPC and evaluation primitives. Product
orchestration, prompts, collectors, egress, tenancy and deployment remain outside
that package.

The product integration will add `video_investigation` as a parallel Job workflow
and retain `audio_description` as the default/backfilled legacy kind. Its planned
state model is:

```text
awaiting_upload -> queued -> preprocessing -> investigating
-> needs_review -> completed
                 \-> failed / cancelled
```

When implemented, the public Job lifecycle will continue to project both compute
phases as `processing`. The new contract will remain Browser-API-only until it
stabilizes; the public Integration API, SDK and CLI will remain unchanged.

Local execution is planned as a separate `python -I` process with an explicit
environment, bounded runtime settings, strict JSON request/result schemas and
parent-side binding to the authoritative source SHA, source ID, workflow kind and
local policy. The model may emit observations and candidate contributions; it may
not become the source of truth for a location.

The planned first executable product mode is `geolocateProvenance` with `local`
connectivity. Other enum values may enter the domain model before their pipelines;
Browser creation must then return a bounded `422 investigation_mode_unavailable`.
In particular, `approvedCrops` describes a future human authorization boundary; it
never authorizes network execution inside the open planner.

This first delivery implements only the autonomous open package and its distribution
and license gates. It does not implement or deploy the database, API, worker,
Browser workspace, retrieval or model-runtime integration described above.

## Safety and data policy

- Inputs must be analyst uploads, licensed datasets or explicitly permitted feeds.
- Telegram scraping is excluded.
- Local mode must make no non-loopback network request.
- Source legal basis, redistribution policy, retention and SHA-256 must be durable.
- Raw chain-of-thought is neither requested nor displayed. The UI will expose
  evidence, bounded tool decisions, timings and posterior changes instead.
- Connected results, when implemented, enter as unverified evidence and require
  local verification plus analyst action.
- No face recognition, live person/unit tracking, weapon targeting or operational
  coordinates for use of force are in scope.

## Consequences

The open package establishes a reusable technical and evaluation spine without
rewriting the product platform. Product integration will add two explicit costs:
the worker fleet will need workflow-specific queue routing, and investigation
artifacts/traces will need their own retention policy.

The month-one demo may use deterministic offline replay, while a single short live
local step proves the model boundary. Claims about quality remain blocked until a
licensed, documented benchmark is run.

## Rejected alternatives

- **Rename audio-description scenes into evidence.** Rejected because it preserves
  the wrong review and delivery invariants.
- **Let a VLM return final coordinates.** Rejected because it is unauditable and
  cannot express calibrated uncertainty or correlation.
- **Start with hosted inference or Telegram collection.** Rejected because local
  data control and permitted provenance are core product requirements.
- **Fine-tune first.** Rejected until traces and an error benchmark identify a stable
  target; the first learned component is a lightweight information-rich frame
  ranker.
