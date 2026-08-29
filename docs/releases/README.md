# Release evidence packets

Release evidence is written only after a bounded gate has passed. Each packet records
the released identity, deployed boundary, verification evidence, measured results,
safe visual evidence where available, and known limitations.

- [`v0.1-cloud-core.md`](./v0.1-cloud-core.md) — dated release evidence for the
  historically deployed legacy AWS Cloud Core. Its static frontend remains
  published, but API/readiness is unavailable as of `2026-08-29`.

The API-first B2B beta has no release packet yet because its infrastructure cutover,
live integration canaries and npm publication are still pending. Local implementation
status is documented in [Architecture](../architecture.md), not promoted to release
evidence.
