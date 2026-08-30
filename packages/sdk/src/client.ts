import { randomUUID } from "node:crypto";
import { stat } from "node:fs/promises";
import { basename, extname } from "node:path";
import { downloadDeliverable } from "./download.js";
import { InstaDescribeError } from "./errors.js";
import { ApiTransport, normalizeOrigin } from "./http.js";
import type {
  CreateJobInput,
  Deliverable,
  DownloadDeliverableOptions,
  InstaDescribeOptions,
  JobSummary,
  ListJobsOptions,
  PresignedUpload,
  SubmitFileInput,
  WaitForJobOptions,
  WriteOptions,
} from "./types.js";
import { uploadFile, type UploadFileOptions } from "./upload.js";
import {
  assertIdempotencyKey,
  assertResourceId,
  decodeCapabilities,
  decodeCreateJob,
  decodeDeliverables,
  decodeJob,
  decodeJobPage,
} from "./validation.js";

// CloudFront maps the stable public contract to FastAPI's internal
// /api/integrations/v1 router. Integrators must never depend on that internal
// composition path.
const PREFIX = "/v1";
const DEFAULT_TIMEOUT_MS = 30_000;
const DEFAULT_WAIT_TIMEOUT_MS = 30 * 60_000;
const DEFAULT_POLL_INTERVAL_MS = 3_000;
const MAX_VIDEO_BYTES = 1024 * 1024 * 1024;
const MAX_TRANSCRIPT_BYTES = 10 * 1024 * 1024;
const VIDEO_TYPES: Readonly<Record<string, string>> = {
  ".mp4": "video/mp4",
  ".mov": "video/quicktime",
  ".webm": "video/webm",
};

function positiveFinite(value: number, name: string): number {
  if (!Number.isFinite(value) || value <= 0) {
    throw new InstaDescribeError(`${name} must be a positive finite number`, {
      kind: "validation", code: `invalid_${name}`,
    });
  }
  return value;
}

function writeKey(options: WriteOptions): string {
  return assertIdempotencyKey(options.idempotencyKey ?? randomUUID());
}

async function delay(ms: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) throw new InstaDescribeError("Wait aborted", { kind: "aborted", code: "aborted" });
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(done, ms);
    function done(): void {
      signal?.removeEventListener("abort", aborted);
      resolve();
    }
    function aborted(): void {
      clearTimeout(timer);
      reject(new InstaDescribeError("Wait aborted", { kind: "aborted", code: "aborted" }));
    }
    signal?.addEventListener("abort", aborted, { once: true });
  });
}

function reached(job: JobSummary, until: NonNullable<WaitForJobOptions["until"]>): boolean {
  if (typeof until === "function") return until(job);
  return (Array.isArray(until) ? until : [until]).includes(job.state);
}

function listPath(options: ListJobsOptions): string {
  const limit = options.limit ?? 20;
  if (!Number.isInteger(limit) || limit < 1 || limit > 100) {
    throw new InstaDescribeError("limit must be an integer between 1 and 100", {
      kind: "validation", code: "invalid_limit",
    });
  }
  const query = new URLSearchParams({ limit: String(limit) });
  if (options.cursor !== undefined) query.set("cursor", options.cursor);
  if (options.projectId !== undefined) query.set("projectId", assertResourceId(options.projectId, "projectId"));
  return `${PREFIX}/jobs?${query.toString()}`;
}

async function localFile(
  filePath: string,
  label: "video" | "transcript",
  maxBytes: number,
): Promise<{ fileName: string; extension: string; sizeBytes: number }> {
  if (typeof filePath !== "string" || filePath.length === 0) {
    throw new InstaDescribeError(`${label} path is required`, {
      kind: "validation", code: `invalid_${label}_path`,
    });
  }
  let info;
  try {
    info = await stat(filePath);
  } catch (cause) {
    throw new InstaDescribeError(`Could not inspect the ${label} file`, {
      kind: "filesystem", code: `${label}_file_error`, cause,
    });
  }
  if (!info.isFile() || info.size < 1 || info.size > maxBytes) {
    throw new InstaDescribeError(
      `${label} must be a non-empty regular file no larger than ${maxBytes} bytes`,
      { kind: "validation", code: `invalid_${label}_size` },
    );
  }
  return {
    fileName: basename(filePath),
    extension: extname(filePath).toLowerCase(),
    sizeBytes: info.size,
  };
}

export class InstaDescribe {
  readonly #api: ApiTransport;
  readonly #fetch: typeof globalThis.fetch;
  readonly #appOrigin: URL;

  readonly capabilities = {
    get: async (options: { signal?: AbortSignal | undefined } = {}) =>
      decodeCapabilities(await this.#api.json("GET", `${PREFIX}/capabilities`, { signal: options.signal })),
  };

  readonly jobs = {
    submitFile: async (input: SubmitFileInput) => {
      if (
        input.durationSeconds !== undefined
        && (!Number.isFinite(input.durationSeconds)
          || input.durationSeconds <= 0
          || input.durationSeconds > 3_600)
      ) {
        throw new InstaDescribeError("durationSeconds must be greater than zero and at most 3600", {
          kind: "validation", code: "invalid_duration_seconds",
        });
      }
      const video = await localFile(input.filePath, "video", MAX_VIDEO_BYTES);
      const videoContentType = VIDEO_TYPES[video.extension];
      if (videoContentType === undefined) {
        throw new InstaDescribeError("Video must be .mp4, .mov or .webm", {
          kind: "validation", code: "unsupported_video_type",
        });
      }
      let transcript: Awaited<ReturnType<typeof localFile>> | undefined;
      if (input.transcriptPath !== undefined) {
        transcript = await localFile(input.transcriptPath, "transcript", MAX_TRANSCRIPT_BYTES);
        if (transcript.extension !== ".vtt" && transcript.extension !== ".srt") {
          throw new InstaDescribeError("Transcript must be timed UTF-8 VTT or SRT", {
            kind: "validation", code: "unsupported_transcript_type",
          });
        }
      }
      const created = await this.jobs.create({
        project: input.project,
        ...(input.clientReference === undefined ? {} : { clientReference: input.clientReference }),
        video: {
          fileName: video.fileName,
          contentType: videoContentType,
          sizeBytes: video.sizeBytes,
          ...(input.durationSeconds === undefined ? {} : { durationSeconds: input.durationSeconds }),
        },
        ...(transcript === undefined ? {} : {
          transcript: {
            fileName: transcript.fileName,
            format: transcript.extension === ".vtt" ? "vtt" as const : "srt" as const,
            contentType: transcript.extension === ".vtt" ? "text/vtt" as const : "application/x-subrip" as const,
            sizeBytes: transcript.sizeBytes,
          },
        }),
        settings: input.settings ?? { preset: "standard", style: "documentary", detail: 3 },
      }, {
        ...(input.idempotencyKey === undefined ? {} : { idempotencyKey: input.idempotencyKey }),
        ...(input.signal === undefined ? {} : { signal: input.signal }),
      });
      await this.uploads.uploadFile(created.uploads.video, input.filePath, {
        fileName: video.fileName,
        contentType: videoContentType,
        signal: input.signal,
      });
      if (transcript !== undefined) {
        if (created.uploads.transcript === undefined) {
          throw new InstaDescribeError("API omitted the transcript upload contract", {
            kind: "contract", code: "missing_transcript_upload",
          });
        }
        await this.uploads.uploadFile(created.uploads.transcript, input.transcriptPath!, {
          fileName: transcript.fileName,
          contentType: transcript.extension === ".vtt" ? "text/vtt" : "application/x-subrip",
          signal: input.signal,
        });
      }
      return this.jobs.completeUpload(created.job.jobId, {
        ...(input.completeIdempotencyKey === undefined
          ? {}
          : { idempotencyKey: input.completeIdempotencyKey }),
        ...(input.signal === undefined ? {} : { signal: input.signal }),
      });
    },
    create: async (input: CreateJobInput, options: WriteOptions = {}) => {
      if ("id" in input.project && input.project.id !== undefined) {
        assertResourceId(input.project.id, "projectId");
      }
      const value = await this.#api.json("POST", `${PREFIX}/jobs`, {
        body: input,
        signal: options.signal,
        idempotencyKey: writeKey(options),
        acceptedStatuses: [201],
      });
      return decodeCreateJob(value);
    },
    list: async (options: ListJobsOptions = {}) =>
      decodeJobPage(await this.#api.json("GET", listPath(options), { signal: options.signal })),
    get: async (jobId: string, options: { signal?: AbortSignal | undefined } = {}) => {
      const id = assertResourceId(jobId, "jobId");
      return decodeJob(await this.#api.json("GET", `${PREFIX}/jobs/${id}`, { signal: options.signal }));
    },
    completeUpload: async (jobId: string, options: WriteOptions = {}) => {
      const id = assertResourceId(jobId, "jobId");
      const value = await this.#api.json("POST", `${PREFIX}/jobs/${id}/uploads/complete`, {
        signal: options.signal,
        idempotencyKey: writeKey(options),
        acceptedStatuses: [200, 202],
      });
      return decodeJob(value);
    },
    cancel: async (jobId: string, options: WriteOptions = {}) => {
      const id = assertResourceId(jobId, "jobId");
      const value = await this.#api.json("POST", `${PREFIX}/jobs/${id}/cancel`, {
        signal: options.signal,
        idempotencyKey: writeKey(options),
        acceptedStatuses: [200],
      });
      return decodeJob(value);
    },
    wait: async (jobId: string, options: WaitForJobOptions = {}) => {
      const timeoutMs = positiveFinite(options.timeoutMs ?? DEFAULT_WAIT_TIMEOUT_MS, "timeoutMs");
      const pollIntervalMs = positiveFinite(options.pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS, "pollIntervalMs");
      const until = options.until ?? (["needs_review", "completed"] as const);
      const started = Date.now();
      while (true) {
        const job = await this.jobs.get(jobId, { signal: options.signal });
        await options.onProgress?.(job);
        if (reached(job, until)) return job;
        if (job.state === "failed") {
          throw new InstaDescribeError(job.error?.message ?? "Job failed", {
            kind: "job_failed", code: job.error?.code ?? "job_failed",
          });
        }
        if (job.state === "cancelled") {
          throw new InstaDescribeError("Job was cancelled", {
            kind: "job_cancelled", code: job.error?.code ?? "job_cancelled",
          });
        }
        const remaining = timeoutMs - (Date.now() - started);
        if (remaining <= 0) {
          throw new InstaDescribeError("Timed out waiting for the job", {
            kind: "timeout", code: "wait_timeout", retryable: true,
          });
        }
        await delay(Math.min(pollIntervalMs, remaining), options.signal);
      }
    },
  };

  readonly deliverables = {
    list: async (jobId: string, options: { signal?: AbortSignal | undefined } = {}) => {
      const id = assertResourceId(jobId, "jobId");
      return decodeDeliverables(await this.#api.json("GET", `${PREFIX}/jobs/${id}/deliverables`, { signal: options.signal }));
    },
    download: async (
      jobId: string,
      selector: string | Deliverable,
      destination: string,
      options: DownloadDeliverableOptions = {},
    ) => {
      const id = assertResourceId(jobId, "jobId");
      let deliverable: Deliverable;
      if (typeof selector === "string") {
        const collection = await this.deliverables.list(id, { signal: options.signal });
        deliverable = collection.items.find((item) => item.deliverableId === selector || item.kind === selector)
          ?? (() => { throw new InstaDescribeError(`Deliverable ${selector} was not found`, { kind: "not_found", code: "deliverable_not_found" }); })();
      } else {
        if (selector.jobId !== id) {
          throw new InstaDescribeError("Deliverable belongs to a different job", {
            kind: "validation", code: "deliverable_job_mismatch",
          });
        }
        deliverable = selector;
      }
      const deliverableId = assertResourceId(deliverable.deliverableId, "deliverableId");
      const contentPath = `${PREFIX}/deliverables/${deliverableId}/content`;
      let location = await this.#api.redirectLocation(contentPath, options.signal);
      try {
        return await downloadDeliverable(this.#fetch, deliverable, location, destination, options);
      } catch (cause) {
        // A short-lived S3 URL may expire between listing and transfer. Mint
        // exactly one fresh version-pinned location; never retry integrity,
        // filesystem, cancellation or any other storage response.
        if (!(cause instanceof InstaDescribeError) || cause.status !== 403) throw cause;
        location = await this.#api.redirectLocation(contentPath, options.signal);
        return downloadDeliverable(this.#fetch, deliverable, location, destination, options);
      }
    },
  };

  readonly uploads = {
    uploadFile: async (contract: PresignedUpload, filePath: string, options: UploadFileOptions = {}) =>
      uploadFile(this.#fetch, contract, filePath, options),
  };

  constructor(options: InstaDescribeOptions) {
    if (
      typeof options.apiKey !== "string"
      || !/^idsb_live_[a-f0-9]{12}\.[A-Za-z0-9_-]{43,64}$/.test(options.apiKey)
    ) {
      throw new InstaDescribeError("apiKey must be an idsb_live service-account key", {
        kind: "validation", code: "invalid_api_key",
      });
    }
    const baseOrigin = normalizeOrigin(options.baseUrl, "baseUrl");
    this.#appOrigin = normalizeOrigin(options.appUrl ?? baseOrigin, "appUrl");
    this.#fetch = options.fetch ?? globalThis.fetch;
    if (typeof this.#fetch !== "function") {
      throw new InstaDescribeError("A Fetch API implementation is required", {
        kind: "validation", code: "missing_fetch",
      });
    }
    this.#api = new ApiTransport(
      baseOrigin,
      options.apiKey,
      this.#fetch,
      positiveFinite(options.requestTimeoutMs ?? DEFAULT_TIMEOUT_MS, "requestTimeoutMs"),
    );
  }

  reviewUrl(job: JobSummary): URL {
    if (job.reviewUrl === null) {
      throw new InstaDescribeError("Review is not available for this job yet", {
        kind: "conflict", code: "review_not_ready",
      });
    }
    let url: URL;
    try {
      url = new URL(job.reviewUrl);
    } catch {
      throw new InstaDescribeError("Server returned an invalid review URL", {
        kind: "contract", code: "invalid_review_url",
      });
    }
    if (url.origin !== this.#appOrigin.origin || url.username || url.password) {
      throw new InstaDescribeError("Server returned a review URL on an unexpected origin", {
        kind: "contract", code: "invalid_review_url",
      });
    }
    return url;
  }
}
