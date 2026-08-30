import { createHash } from "node:crypto";
import { access, mkdtemp, open, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  InstaDescribe,
  InstaDescribeError,
  type JobSummary,
  verifyWebhook,
  verifyWebhookJson,
  WebhookVerificationError,
} from "../src/index.js";

const JOB_ID = "11111111-1111-4111-8111-111111111111";
const PROJECT_ID = "22222222-2222-4222-8222-222222222222";
const DELIVERABLE_ID = "33333333-3333-4333-8333-333333333333";
const API_KEY = `idsb_live_0123456789ab.${"x".repeat(43)}`;
const REVIEW_URL = `https://app.example/orgs/agency/projects/${PROJECT_ID}/jobs/${JOB_ID}/review`;

function json(value: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json", ...headers } });
}

function job(state = "needs_review", progress = 100) {
  return {
    id: JOB_ID,
    object: "job",
    projectId: PROJECT_ID,
    clientReference: null,
    state,
    progress,
    stage: state,
    pipelineRevision: "test",
    source: { status: "uploaded", contentType: "video/mp4", sizeBytes: 10, durationSeconds: 90 },
    reviewUrl: state === "needs_review" || state === "completed" ? REVIEW_URL : null,
    error: null,
    createdAt: "2026-08-28T10:00:00Z",
    updatedAt: "2026-08-28T10:01:00Z",
  };
}

function jobSummary(reviewUrl: string | null): JobSummary {
  return {
    jobId: JOB_ID,
    projectId: PROJECT_ID,
    clientReference: null,
    state: reviewUrl === null ? "queued" : "needs_review",
    progress: reviewUrl === null ? 0 : 100,
    stage: null,
    pipelineRevision: "test",
    source: { status: "uploaded", contentType: "video/mp4", sizeBytes: 10, durationSeconds: 90 },
    reviewUrl,
    error: null,
    createdAt: "2026-08-28T10:00:00Z",
    updatedAt: "2026-08-28T10:01:00Z",
  };
}

describe("public Integration API client", () => {
  it("decodes the public aggregate TTS spend ceilings", async () => {
    const fetchMock = vi.fn(async () => json({
      brand: "InstaDescribe",
      apiVersion: "v1-beta",
      organizationId: "44444444-4444-4444-8444-444444444444",
      resources: ["organization", "projects", "jobs"],
      jobStates: ["awaiting_upload", "queued", "processing", "needs_review", "rendering", "completed", "failed", "cancelled"],
      review: { mode: "web" },
      uploads: { maxBytes: 1_073_741_824, maxDurationSeconds: 3_600, contentTypes: ["video/mp4"] },
      idempotency: { requiredForWrites: true, retentionSeconds: 86_400 },
      tts: {
        maxApprovedScenesPerReview: 120,
        maxRenderAttemptsPerReview: 2,
        maxFinalSynthesisCallsPerReview: 240,
        previews: {
          rollingWindowSeconds: 86_400,
          maxRequestsPerJob: 25,
          maxRequestsPerOrganization: 100,
          maxActivePerOrganization: 5,
          maxAttemptsPerRequest: 3,
        },
      },
    }));
    const client = new InstaDescribe({
      baseUrl: "https://api.example",
      apiKey: API_KEY,
      fetch: fetchMock as typeof fetch,
    });

    const result = await client.capabilities.get();

    expect(result.tts.maxFinalSynthesisCallsPerReview).toBe(240);
    expect(result.tts.previews).toMatchObject({
      rollingWindowSeconds: 86_400,
      maxRequestsPerJob: 25,
      maxRequestsPerOrganization: 100,
    });
  });

  it("uses Bearer auth only on the configured origin and decodes canonical jobs", async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      expect(new URL(input.toString()).href).toBe(`https://api.example/v1/jobs/${JOB_ID}`);
      const headers = new Headers(init?.headers);
      expect(headers.get("Authorization")).toBe(`Bearer ${API_KEY}`);
      expect(headers.has("X-Portfolio-Token")).toBe(false);
      expect(init?.redirect).toBe("error");
      expect(init?.credentials).toBe("omit");
      return json(job());
    });
    const client = new InstaDescribe({
      baseUrl: "https://api.example",
      appUrl: "https://app.example",
      apiKey: API_KEY,
      fetch: fetchMock as typeof fetch,
    });
    const result = await client.jobs.get(JOB_ID);
    expect(result).toMatchObject({ jobId: JOB_ID, projectId: PROJECT_ID, state: "needs_review" });
    expect(client.reviewUrl(result).href).toBe(REVIEW_URL);
  });

  it("rejects path-bearing origins, non-loopback HTTP, and malformed keys", () => {
    expect(() => new InstaDescribe({ baseUrl: "https://api.example/prefix", apiKey: API_KEY })).toThrow(InstaDescribeError);
    expect(() => new InstaDescribe({ baseUrl: "http://api.example", apiKey: API_KEY })).toThrow(/HTTPS origin/);
    expect(() => new InstaDescribe({ baseUrl: "https://api.example", apiKey: "bad key" })).toThrow(/idsb_live/);
    expect(() => new InstaDescribe({ baseUrl: "http://127.0.0.1:8000", apiKey: API_KEY })).not.toThrow();
  });

  it("never guesses a review route or accepts a cross-origin server URL", () => {
    const client = new InstaDescribe({ baseUrl: "https://api.example", appUrl: "https://app.example", apiKey: API_KEY });
    expect(() => client.reviewUrl(jobSummary(null))).toThrow(/not available/);
    expect(() => client.reviewUrl(jobSummary("https://attacker.example/review"))).toThrow(/unexpected origin/);
  });

  it("creates an inline project job with a stable caller idempotency key", async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      expect(new URL(input.toString()).pathname).toBe("/v1/jobs");
      expect(new Headers(init?.headers).get("Idempotency-Key")).toBe("create-launch-v1");
      expect(JSON.parse(String(init?.body))).toMatchObject({
        project: { name: "Agency launch", externalId: "crm-42" },
        video: { fileName: "launch.mp4", sizeBytes: 10 },
        settings: { preset: "standard", style: "documentary", detail: 3 },
      });
      return json({
        job: job("awaiting_upload", 0),
        uploads: {
          video: { method: "POST", url: "https://storage.example/video", fields: { key: "signed/video" }, expiresAt: "2026-08-28T11:00:00Z" },
          transcript: { method: "POST", url: "https://storage.example/transcript", fields: { key: "signed/transcript" }, expiresAt: "2026-08-28T11:00:00Z" },
        },
      }, 201);
    });
    const client = new InstaDescribe({ baseUrl: "https://api.example", apiKey: API_KEY, fetch: fetchMock as typeof fetch });
    const created = await client.jobs.create({
      project: { name: "Agency launch", externalId: "crm-42" },
      clientReference: "run-9",
      video: { fileName: "launch.mp4", contentType: "video/mp4", sizeBytes: 10, durationSeconds: 90 },
      transcript: { fileName: "launch.vtt", format: "vtt", contentType: "text/vtt", sizeBytes: 5 },
      settings: { preset: "standard", style: "documentary", detail: 3 },
    }, { idempotencyKey: "create-launch-v1" });
    expect(created.job.state).toBe("awaiting_upload");
    expect(created.uploads.transcript?.fields).toEqual({ key: "signed/transcript" });
  });

  it("paginates jobs and preserves opaque cursors", async () => {
    const fetchMock = vi.fn(async (input: string | URL | Request) => {
      const url = new URL(input.toString());
      expect(url.searchParams.get("limit")).toBe("10");
      expect(url.searchParams.get("cursor")).toBe("opaque+/=");
      expect(url.searchParams.get("projectId")).toBe(PROJECT_ID);
      return json({ object: "list", data: [job()], nextCursor: "next-token" });
    });
    const client = new InstaDescribe({ baseUrl: "https://api.example", apiKey: API_KEY, fetch: fetchMock as typeof fetch });
    await expect(client.jobs.list({ limit: 10, cursor: "opaque+/=", projectId: PROJECT_ID })).resolves.toMatchObject({
      items: [{ jobId: JOB_ID }], nextCursor: "next-token",
    });
  });

  it("uses locked complete/cancel paths and accepts semantic completion replay", async () => {
    const requests: Array<{ path: string; key: string | null }> = [];
    const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      requests.push({ path: new URL(input.toString()).pathname, key: new Headers(init?.headers).get("Idempotency-Key") });
      return json(job("queued", 0), requests.length === 1 ? 202 : 200);
    });
    const client = new InstaDescribe({ baseUrl: "https://api.example", apiKey: API_KEY, fetch: fetchMock as typeof fetch });
    await client.jobs.completeUpload(JOB_ID, { idempotencyKey: "complete-1" });
    await client.jobs.cancel(JOB_ID, { idempotencyKey: "cancel-1" });
    expect(requests).toEqual([
      { path: `/v1/jobs/${JOB_ID}/uploads/complete`, key: "complete-1" },
      { path: `/v1/jobs/${JOB_ID}/cancel`, key: "cancel-1" },
    ]);
  });

  it("waits for needs_review without overlapping polls", async () => {
    const states = [job("processing", 50), job("needs_review", 100)];
    let active = 0;
    let maximumActive = 0;
    const progress: number[] = [];
    const fetchMock = vi.fn(async () => {
      active += 1;
      maximumActive = Math.max(maximumActive, active);
      const next = states.shift();
      await Promise.resolve();
      active -= 1;
      return json(next);
    });
    const client = new InstaDescribe({ baseUrl: "https://api.example", apiKey: API_KEY, fetch: fetchMock as typeof fetch });
    const result = await client.jobs.wait(JOB_ID, {
      pollIntervalMs: 1,
      timeoutMs: 1_000,
      onProgress: (current) => { progress.push(current.progress); },
    });
    expect(result.state).toBe("needs_review");
    expect(progress).toEqual([50, 100]);
    expect(maximumActive).toBe(1);
  });

  it("maps RFC 9457 fields without exposing an arbitrary response body", async () => {
    const client = new InstaDescribe({
      baseUrl: "https://api.example",
      apiKey: API_KEY,
      fetch: (async () => json({
        type: "https://api.instadescribe.com/problems/idempotency_in_progress",
        title: "Request in progress",
        status: 409,
        detail: "The write is still in progress.",
        code: "idempotency_in_progress",
        requestId: "req-123",
        retryable: true,
        secretInternalField: "must-not-surface",
      }, 409)) as typeof fetch,
    });
    await expect(client.jobs.cancel(JOB_ID)).rejects.toMatchObject({
      kind: "conflict", code: "idempotency_in_progress", requestId: "req-123", retryable: true,
    });
  });
});

describe("signed upload and deliverable isolation", () => {
  const directories: string[] = [];
  afterEach(async () => {
    await Promise.all(directories.splice(0).map((directory) => rm(directory, { recursive: true, force: true })));
  });

  it("submitFile reserves once, streams both declared files, and confirms the same job", async () => {
    const directory = await mkdtemp(join(tmpdir(), "instadescribe-sdk-"));
    directories.push(directory);
    const videoPath = join(directory, "lecture.mp4");
    const transcriptPath = join(directory, "lecture.vtt");
    await writeFile(videoPath, Buffer.from("video"));
    await writeFile(transcriptPath, Buffer.from("WEBVTT\n"));
    const apiPaths: string[] = [];
    const storageFiles: string[] = [];
    const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(input.toString());
      if (url.hostname === "storage.example") {
        expect(new Headers(init?.headers).has("Authorization")).toBe(false);
        const form = init?.body as FormData;
        storageFiles.push((form.get("file") as File).name);
        return new Response(null, { status: 204 });
      }
      apiPaths.push(url.pathname);
      expect(new Headers(init?.headers).get("Authorization")).toBe(`Bearer ${API_KEY}`);
      if (url.pathname === "/v1/jobs") {
        expect(new Headers(init?.headers).get("Idempotency-Key")).toBe("lecture-create");
        return json({
          job: { ...job("awaiting_upload", 0), reviewUrl: null },
          uploads: {
            video: { method: "POST", url: "https://storage.example/video", fields: { key: "video" }, expiresAt: "2026-08-28T11:00:00Z" },
            transcript: { method: "POST", url: "https://storage.example/transcript", fields: { key: "transcript" }, expiresAt: "2026-08-28T11:00:00Z" },
          },
        }, 201);
      }
      expect(new Headers(init?.headers).get("Idempotency-Key")).toBe("lecture-complete");
      return json({ ...job("queued", 0), reviewUrl: null }, 202);
    });
    const client = new InstaDescribe({ baseUrl: "https://api.example", apiKey: API_KEY, fetch: fetchMock as typeof fetch });
    const submitted = await client.jobs.submitFile({
      filePath: videoPath,
      transcriptPath,
      project: { name: "BIO101" },
      idempotencyKey: "lecture-create",
      completeIdempotencyKey: "lecture-complete",
    });
    expect(submitted).toMatchObject({ jobId: JOB_ID, state: "queued" });
    expect(apiPaths).toEqual([
      "/v1/jobs",
      `/v1/jobs/${JOB_ID}/uploads/complete`,
    ]);
    expect(storageFiles).toEqual(["lecture.mp4", "lecture.vtt"]);
  });

  it("does not eagerly buffer a maximum-size sparse video before upload", async () => {
    const directory = await mkdtemp(join(tmpdir(), "instadescribe-sdk-bounded-memory-"));
    directories.push(directory);
    const videoPath = join(directory, "maximum.mp4");
    const maximumBytes = 1024 * 1024 * 1024;
    const handle = await open(videoPath, "w");
    try {
      await handle.truncate(maximumBytes);
    } finally {
      await handle.close();
    }

    let uploadedSize = 0;
    const fetchMock = vi.fn(async (_input: string | URL | Request, init?: RequestInit) => {
      const form = init?.body as FormData;
      const file = form.get("file");
      expect(file).toBeInstanceOf(Blob);
      uploadedSize = (file as Blob).size;
      return new Response(null, { status: 204 });
    });
    const client = new InstaDescribe({
      baseUrl: "https://api.example",
      apiKey: API_KEY,
      fetch: fetchMock as typeof fetch,
    });
    const rssBefore = process.memoryUsage().rss;

    await client.uploads.uploadFile(
      {
        method: "POST",
        url: "https://storage.example/maximum",
        fields: { key: "maximum" },
        expiresAt: "2026-08-28T11:00:00Z",
      },
      videoPath,
      { contentType: "video/mp4" },
    );

    expect(uploadedSize).toBe(maximumBytes);
    expect(process.memoryUsage().rss - rssBefore).toBeLessThan(96 * 1024 * 1024);
  });

  it("follows API 303 manually, then streams signed content without the Bearer key", async () => {
    const bytes = Buffer.from("described video");
    const sha256 = createHash("sha256").update(bytes).digest("hex");
    const metadata = {
      id: DELIVERABLE_ID, jobId: JOB_ID, kind: "mp4", fileName: "described_video.mp4",
      contentType: "video/mp4", byteSize: bytes.length, sha256, createdAt: "2026-08-28T10:02:00Z",
    };
    const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(input.toString());
      if (url.pathname.endsWith("/deliverables")) return json({ items: [metadata], completedSet: true });
      if (url.pathname.endsWith("/content")) {
        expect(new Headers(init?.headers).get("Authorization")).toBe(`Bearer ${API_KEY}`);
        expect(init?.redirect).toBe("manual");
        return new Response(null, { status: 303, headers: { Location: "https://storage.example/video?signed=1" } });
      }
      expect(url.hostname).toBe("storage.example");
      expect(new Headers(init?.headers).has("Authorization")).toBe(false);
      expect(new Headers(init?.headers).has("X-Portfolio-Token")).toBe(false);
      expect(init?.redirect).toBe("error");
      return new Response(bytes);
    });
    const client = new InstaDescribe({ baseUrl: "https://api.example", apiKey: API_KEY, fetch: fetchMock as typeof fetch });
    const directory = await mkdtemp(join(tmpdir(), "instadescribe-sdk-"));
    directories.push(directory);
    const destination = join(directory, "described_video.mp4");

    const result = await client.deliverables.download(JOB_ID, "mp4", destination);
    expect(result).toMatchObject({ path: destination, bytes: bytes.length, sha256, reusedExisting: false });
    expect(await readFile(destination)).toEqual(bytes);
    await expect(access(`${destination}.part`)).rejects.toMatchObject({ code: "ENOENT" });
  });

  it("removes .part after a deliverable checksum failure", async () => {
    const bytes = Buffer.from("corrupt");
    const expected = createHash("sha256").update("expected").digest("hex");
    const metadata = {
      id: DELIVERABLE_ID, jobId: JOB_ID, kind: "srt", fileName: "audio_description.srt",
      contentType: "application/x-subrip", byteSize: bytes.length, sha256: expected, createdAt: "2026-08-28T10:02:00Z",
    };
    const fetchMock = vi.fn(async (input: string | URL | Request) => {
      const path = new URL(input.toString()).pathname;
      if (path.endsWith("/deliverables")) return json({ items: [metadata], completedSet: true });
      if (path.endsWith("/content")) return new Response(null, { status: 303, headers: { Location: "https://storage.example/subtitles" } });
      return new Response(bytes);
    });
    const client = new InstaDescribe({ baseUrl: "https://api.example", apiKey: API_KEY, fetch: fetchMock as typeof fetch });
    const directory = await mkdtemp(join(tmpdir(), "instadescribe-sdk-"));
    directories.push(directory);
    const destination = join(directory, "audio_description.srt");
    await expect(client.deliverables.download(JOB_ID, "srt", destination)).rejects.toMatchObject({ kind: "integrity" });
    await expect(access(destination)).rejects.toMatchObject({ code: "ENOENT" });
    await expect(access(`${destination}.part`)).rejects.toMatchObject({ code: "ENOENT" });
  });

  it("refreshes one expired signed location and retries the same deliverable once", async () => {
    const bytes = Buffer.from("described video");
    const sha256 = createHash("sha256").update(bytes).digest("hex");
    const metadata = {
      id: DELIVERABLE_ID, jobId: JOB_ID, kind: "mp4", fileName: "described_video.mp4",
      contentType: "video/mp4", byteSize: bytes.length, sha256, createdAt: "2026-08-28T10:02:00Z",
    };
    let redirects = 0;
    let storageGets = 0;
    const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = new URL(input.toString());
      if (url.pathname.endsWith("/deliverables")) return json({ items: [metadata], completedSet: true });
      if (url.pathname.endsWith("/content")) {
        redirects += 1;
        expect(new Headers(init?.headers).get("Authorization")).toBe(`Bearer ${API_KEY}`);
        return new Response(null, {
          status: 303,
          headers: { Location: `https://storage.example/video?attempt=${redirects}` },
        });
      }
      storageGets += 1;
      expect(new Headers(init?.headers).has("Authorization")).toBe(false);
      return storageGets === 1 ? new Response(null, { status: 403 }) : new Response(bytes);
    });
    const client = new InstaDescribe({
      baseUrl: "https://api.example",
      apiKey: API_KEY,
      fetch: fetchMock as typeof fetch,
    });
    const directory = await mkdtemp(join(tmpdir(), "instadescribe-sdk-"));
    directories.push(directory);
    const destination = join(directory, "described_video.mp4");

    await expect(client.deliverables.download(JOB_ID, "mp4", destination)).resolves.toMatchObject({
      path: destination,
      bytes: bytes.length,
      sha256,
    });
    expect(redirects).toBe(2);
    expect(storageGets).toBe(2);
  });

  it("uploads signed POST form data without the Bearer key", async () => {
    const directory = await mkdtemp(join(tmpdir(), "instadescribe-sdk-"));
    directories.push(directory);
    const source = join(directory, "clip.mp4");
    await writeFile(source, Buffer.from("video"));
    const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      expect(new URL(input.toString()).hostname).toBe("storage.example");
      expect(new Headers(init?.headers).has("Authorization")).toBe(false);
      expect(init?.body).toBeInstanceOf(FormData);
      expect((init?.body as FormData).get("key")).toBe("uploads/job/video.mp4");
      return new Response(null, { status: 204 });
    });
    const client = new InstaDescribe({ baseUrl: "https://api.example", apiKey: API_KEY, fetch: fetchMock as typeof fetch });
    await client.uploads.uploadFile(
      { method: "POST", url: "https://storage.example/upload", fields: { key: "uploads/job/video.mp4" }, expiresAt: "2026-08-28T11:00:00Z" },
      source,
      { contentType: "video/mp4" },
    );
  });
});

describe("webhook verification fixture", () => {
  it("matches the server golden vector and parses JSON only after authentication", () => {
    const eventId = "evt_123";
    const timestamp = 1_787_918_400;
    const secret = "0123456789abcdef0123456789abcdef"; // gitleaks:allow
    const rawBody = Buffer.from('{"jobId":"job_123","state":"completed"}');
    const signature = "bcccca626325ca5fbf2f40b3316c3d2b0fcb7b1814f5aca0e7ff613fd93f57b1";
    expect(verifyWebhook({ eventId, rawBody, secret, timestamp, signature: `v1=${signature}`, now: timestamp })).toMatchObject({ eventId, timestamp });
    expect(verifyWebhookJson<{ state: string }>({ eventId, rawBody, secret, timestamp, signature: `v1=${signature}`, now: timestamp })).toEqual({ jobId: "job_123", state: "completed" });
  });

  it("rejects invalid and stale signatures", () => {
    const base = { eventId: "evt_123", rawBody: "{}", secret: "0".repeat(32), timestamp: 100, signature: `v1=${"0".repeat(64)}` };
    expect(() => verifyWebhook({ ...base, now: 100 })).toThrow(WebhookVerificationError);
    expect(() => verifyWebhook({ ...base, now: 1_000, toleranceSeconds: 300 })).toThrow(/tolerance/);
  });
});
