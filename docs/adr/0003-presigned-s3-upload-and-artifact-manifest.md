# ADR-0003: Browser→S3 presigned POST uploads and a presigned-GET artifact manifest replace in-process media serving

**Status:** Accepted for Cloud Core v0.1; tenant-aware object contracts are added by ADR-0010
**Date:** 2026-08-06

## Context

Today the browser uploads video as multipart form data into the Flask process, and the frontend hard-codes local artifact paths — `/data/{job_id}/…` and `/videos/{job_id}.mp4` — served straight from application directories (`server.py` routes at lines 787, 792). This couples media bandwidth to the API container, makes artifacts as durable as the container disk, and blocks horizontal scaling.

## Decision

- Uploads: `POST /api/v1/jobs` creates the job (state `AWAITING_UPLOAD`) and returns an **S3 presigned POST** with content-type and content-length conditions; the browser uploads directly to a **private** media bucket (`uploads/{job_id}/source/{sanitized_filename}`); `POST /api/v1/jobs/{job_id}/upload-complete` verifies via `HeadObject`, then enqueues.
- Downloads: `GET /api/v1/jobs/{job_id}/manifest` returns logical artifact names mapped to **short-lived presigned GET URLs**. PostgreSQL stores S3 object keys, never expiring URLs.
- Bucket policy: block all public access, default encryption, CORS restricted to the frontend origin, lifecycle expiry for abandoned uploads and demo artifacts.
- The frontend data loader is updated to consume the manifest instead of the `/data` + `/videos` path convention — the **only** frontend change class permitted in v0.1.
- The committed Sintel fixture demo keeps its static `/data/sintel-blender-cc/*` + `/videos/sintel-blender-cc.mp4` files served from the frontend bucket; it never depends on presigned URLs or a backend.

## Consequences

- No large upload passes through the API container (a v0.1 mandatory requirement); ECS tasks can stay small.
- The editor needs a manifest-aware loader; polling code changes from fixed paths to manifest lookups.
- Presigned-URL expiry becomes a UX consideration (manifest re-fetch on expiry).
- CloudFront/S3 CORS and presigned-POST conditions need explicit integration testing (LocalStack locally, real S3 in the cloud smoke test).
