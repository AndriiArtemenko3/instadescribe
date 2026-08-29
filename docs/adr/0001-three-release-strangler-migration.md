# ADR-0001: Three-release strangler migration; Vite frontend is frozen through v0.2

**Status:** Accepted for Cloud Core v0.1; superseded in part by ADR-0010 for the API-first beta
**Date:** 2026-08-06

## Context

The application today is a single-process Flask server (`modular_pipeline/server.py`) that serves the built React/Vite SPA, per-job data files, videos, and all JSON API routes from one origin, launching pipeline runs as detached subprocesses (`server.py:113`). Everything durable lives on the local filesystem. A full rewrite in one invisible arc would leave no publishable evidence for weeks and would put the working editor, fixture demo, and study mode at risk.

## Decision

Deliver three independently releasable upgrades, in order, each with its own tag, evidence packet, and truthful CV claim:

1. **Cloud Core v0.1** — bounded AWS vertical slice (FastAPI, RDS PostgreSQL,
   private S3, SQS, ECS Fargate) behind the existing Vite frontend on S3/CloudFront
   — the Vite framework, component system, state approach, and visual design are
   preserved, with **bounded upload/manifest compatibility changes only**
   (presigned upload flow, manifest-based artifact loading, token entry).
2. **v0.2.0-portfolio-strong** — feature parity, reliability (leases, heartbeats, retries, DLQ, cancellation), Terraform hardening, GitHub Actions OIDC CI/CD, observability, benchmarks.
3. **v0.3.0** — a conditional post-v0.2 decision gate (ADR-0008 §6): the controlled Vite → Next.js App Router migration is the retained candidate, weighed at that gate against editor UX, evaluation, accessibility, reliability and cost work.

The Vite frontend (React 19, React Router 7, TanStack Query, Zustand, Tailwind, shadcn/ui) is **not** rewritten, redesigned, or reorganized during v0.1/v0.2. Milestone C branches from the accepted v0.2 tag.

## Consequences

- The repository is publicly presentable after each milestone instead of only at the end.
- During v0.1, compatibility with the existing frontend contracts (routes, status strings, artifact paths) constrains the new API design; temporary compatibility wrappers are acceptable.
- Next.js knowledge gaps cannot block the cloud release, and cloud churn cannot destabilize the frontend migration.
- Each stop-gate adds coordination overhead (evidence packet, review pause) — accepted as the cost of truthful, incremental claims.
