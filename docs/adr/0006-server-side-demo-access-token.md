# ADR-0006: A server-side portfolio demo token gates paid processing; no account system

**Status:** Accepted for legacy Cloud Core v0.1; superseded by ADR-0010 for the beta
**Date:** 2026-08-06

## Context

At this stage the application had no real authentication: the auth feature folder
was a UI shell, and the deployed study backend was protected only by obscurity plus
study-session mechanics. The bounded Cloud Core needed to gate paid model calls and
S3/compute usage without introducing a full account system into its first release.

## Decision

- Paid processing requires a **portfolio demo access token**: the visitor enters it in the UI when requesting an upload; the frontend sends it in a request header; the API compares a **constant-time hash** against a value stored in AWS Secrets Manager / SSM Parameter Store.
- The token is never embedded in the frontend bundle, never committed, never logged.
- The committed fixture demo remains publicly viewable with **zero** token/keys — it is static files on the frontend origin.
- Portfolio limits enforced server-side alongside the token: max upload 250 MB, max duration 5 minutes, max 1 active processing job, max 3 attempts.
- Documentation must state this is portfolio access control, not multi-tenant authentication.

## Consequences

- A reviewer can always see the product (fixture demo) but cannot spend the owner's money without the token.
- Token rotation is a Secrets Manager update **plus a task/service restart or replacement** so running tasks pick up the new injected digest — no image rebuild is needed, but it is not automatically a zero-restart rotation (corrected at the G4 review gate).
- No user identity exists, so job listings are effectively global to token holders — acceptable for a single-owner portfolio environment and disclosed in the release note.
