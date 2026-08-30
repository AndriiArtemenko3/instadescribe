# `@instadescribe/sdk`

ESM-only Node.js client for the public InstaDescribe Integration API. Requires Node.js 22.19 or newer.

```ts
import { InstaDescribe } from "@instadescribe/sdk";

const client = new InstaDescribe({
  baseUrl: "https://api.instadescribe.example",
  appUrl: "https://app.instadescribe.example",
  apiKey: process.env.INSTADESCRIBE_API_KEY!,
});

const created = await client.jobs.create({
  project: { name: "Agency launch", externalId: "crm-42" },
  clientReference: "run-9",
  video: {
    fileName: "launch.mp4",
    contentType: "video/mp4",
    sizeBytes: 12_345_678,
    durationSeconds: 92,
  },
  settings: { preset: "standard", style: "documentary", detail: 3 },
}, { idempotencyKey: "launch-run-9" });

await client.uploads.uploadFile(created.uploads.video, "./launch.mp4", { contentType: "video/mp4" });
await client.jobs.completeUpload(created.job.jobId, { idempotencyKey: "launch-run-9-complete" });
const ready = await client.jobs.wait(created.job.jobId);
console.log(client.reviewUrl(ready).href);
```

All API requests are restricted to the configured origin and use `Authorization: Bearer`. The constructor accepts only service-account keys shaped as `idsb_live_<key-id>.<secret>`. Every write accepts an idempotency key and generates one when omitted. RFC 9457 errors become typed `InstaDescribeError` values with safe `code`, `status`, `retryable`, and `requestId` fields.

Signed uploads and downloads use a separate fetch path and never receive the API key. Deliverable downloads request the authenticated content endpoint with redirects disabled, read its `303 Location`, then stream the signed content through `destination.part`. Byte length and SHA-256 must match before the file becomes visible at its destination.

The authoritative deterministic FastAPI document is the repository-root `openapi/instadescribe-cloud-v1.json`, regenerated with `services/api/scripts/export_openapi.py` and verified with `--check`. The package-local `openapi/instadescribe-integration-v1.contract.json` is its SDK-public `/v1` projection. `src/generated/openapi/` is recreated by the pinned Hey API generator and must never be edited by hand; package exports expose only the ergonomic wrapper, types, errors and webhook verifier.

Webhook verification accepts the exact `Webhook-Id`, `Webhook-Timestamp` and
`Webhook-Signature: v1=<hex>` header values and verifies
`HMAC-SHA256(secret, id + "." + timestamp + "." + rawBody)` before parsing JSON.
Delivery remains at-least-once, so receivers deduplicate by event ID and reject
timestamps outside the default five-minute window.
