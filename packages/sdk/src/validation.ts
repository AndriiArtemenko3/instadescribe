import { InstaDescribeError } from "./errors.js";
import type {
  IntegrationCapabilitiesResponse as CapabilitiesWire,
  IntegrationDeliverableResponse as DeliverableWire,
  IntegrationJobResponse as JobWire,
  IntegrationPresignedUpload as PresignedUploadWire,
} from "./generated/openapi/types.gen.js";
import type {
  Capabilities,
  CreateJobResult,
  Deliverable,
  Deliverables,
  JobSummary,
  Page,
  PresignedUpload,
} from "./types.js";

const jobStates = new Set([
  "awaiting_upload", "queued", "processing", "needs_review",
  "rendering", "completed", "failed", "cancelled",
]);
const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const sha256Pattern = /^[0-9a-f]{64}$/;

function contract(message: string): never {
  throw new InstaDescribeError(`Integration API returned an invalid response: ${message}`, {
    kind: "contract", code: "invalid_response",
  });
}

function record(value: unknown, at: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) contract(`${at} is not an object`);
  return value as Record<string, unknown>;
}

function string(value: unknown, at: string): string {
  if (typeof value !== "string") contract(`${at} is not a string`);
  return value;
}

function nullableString(value: unknown, at: string): string | null {
  if (value === null) return null;
  return string(value, at);
}

function number(value: unknown, at: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) contract(`${at} is not a finite number`);
  return value;
}

function integer(value: unknown, at: string): number {
  const result = number(value, at);
  if (!Number.isInteger(result)) contract(`${at} is not an integer`);
  return result;
}

function uuid(value: unknown, at: string): string {
  const result = string(value, at);
  if (!uuidPattern.test(result)) contract(`${at} is not a UUID`);
  return result.toLowerCase();
}

function literal<T extends string | boolean>(value: unknown, expected: T, at: string): T {
  if (value !== expected) contract(`${at} is not ${String(expected)}`);
  return expected;
}

function safeHttpUrl(value: unknown, at: string): string {
  const raw = string(value, at);
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return contract(`${at} is not an absolute URL`);
  }
  const loopback = url.hostname === "localhost" || url.hostname === "127.0.0.1" || url.hostname === "[::1]";
  if ((url.protocol !== "https:" && !(url.protocol === "http:" && loopback)) || url.username || url.password) {
    contract(`${at} is not a safe HTTP URL`);
  }
  return url.href;
}

export function assertResourceId(value: string, label: "jobId" | "projectId" | "deliverableId"): string {
  if (!uuidPattern.test(value)) {
    throw new InstaDescribeError(`${label} must be a UUID`, {
      kind: "validation", code: `invalid_${label.replace("Id", "_id")}`,
    });
  }
  return value.toLowerCase();
}

export function assertIdempotencyKey(value: string): string {
  if (!/^[\x21-\x7e]{1,255}$/.test(value)) {
    throw new InstaDescribeError("idempotencyKey must contain 1-255 visible ASCII characters", {
      kind: "validation", code: "invalid_idempotency_key",
    });
  }
  return value;
}

function decodeUpload(value: unknown, at: string): PresignedUpload {
  const body = record(value, at);
  const fields = record(body.fields, `${at}.fields`);
  const safeFields: Record<string, string> = {};
  for (const [key, field] of Object.entries(fields)) safeFields[key] = string(field, `${at}.fields.${key}`);
  const wire: PresignedUploadWire = {
    method: literal(body.method, "POST", `${at}.method`),
    url: safeHttpUrl(body.url, `${at}.url`),
    fields: safeFields,
    expiresAt: string(body.expiresAt, `${at}.expiresAt`),
  };
  return wire;
}

export function decodeJob(value: unknown, at = "job"): JobSummary {
  const body = record(value, at);
  literal(body.object, "job", `${at}.object`);
  const state = string(body.state, `${at}.state`);
  if (!jobStates.has(state)) contract(`${at}.state is unknown`);
  const sourceBody = record(body.source, `${at}.source`);
  const sourceStatus = string(sourceBody.status, `${at}.source.status`);
  if (sourceStatus !== "awaiting_upload" && sourceStatus !== "uploaded") contract(`${at}.source.status is unknown`);
  let error: JobSummary["error"] = null;
  if (body.error !== null) {
    const errorBody = record(body.error, `${at}.error`);
    error = {
      code: nullableString(errorBody.code, `${at}.error.code`),
      message: nullableString(errorBody.message, `${at}.error.message`),
    };
  }
  const duration = sourceBody.durationSeconds === null ? null : number(sourceBody.durationSeconds, `${at}.source.durationSeconds`);
  const wire: JobWire = {
    id: uuid(body.id, `${at}.id`),
    object: "job",
    projectId: uuid(body.projectId, `${at}.projectId`),
    clientReference: nullableString(body.clientReference, `${at}.clientReference`),
    state: state as JobWire["state"],
    progress: integer(body.progress, `${at}.progress`),
    stage: nullableString(body.stage, `${at}.stage`),
    pipelineRevision: string(body.pipelineRevision, `${at}.pipelineRevision`),
    source: {
      status: sourceStatus,
      contentType: nullableString(sourceBody.contentType, `${at}.source.contentType`),
      sizeBytes: sourceBody.sizeBytes === null ? null : integer(sourceBody.sizeBytes, `${at}.source.sizeBytes`),
      durationSeconds: duration,
    },
    reviewUrl: body.reviewUrl === null ? null : safeHttpUrl(body.reviewUrl, `${at}.reviewUrl`),
    error,
    createdAt: string(body.createdAt, `${at}.createdAt`),
    updatedAt: string(body.updatedAt, `${at}.updatedAt`),
  };
  if (wire.progress < 0 || wire.progress > 100) contract(`${at}.progress is outside 0-100`);
  if (wire.source.sizeBytes !== null && wire.source.sizeBytes < 0) contract(`${at}.source.sizeBytes is negative`);
  return {
    jobId: wire.id,
    projectId: wire.projectId,
    clientReference: wire.clientReference,
    state: wire.state,
    progress: wire.progress,
    stage: wire.stage,
    pipelineRevision: wire.pipelineRevision,
    source: wire.source,
    reviewUrl: wire.reviewUrl,
    error: wire.error,
    createdAt: wire.createdAt,
    updatedAt: wire.updatedAt,
  };
}

export function decodeCreateJob(value: unknown): CreateJobResult {
  const body = record(value, "response");
  const uploads = record(body.uploads, "uploads");
  const result: CreateJobResult = {
    job: decodeJob(body.job),
    uploads: {
      video: decodeUpload(uploads.video, "uploads.video"),
      ...(uploads.transcript == null ? {} : { transcript: decodeUpload(uploads.transcript, "uploads.transcript") }),
    },
  };
  return result;
}

export function decodeJobPage(value: unknown): Page<JobSummary> {
  const body = record(value, "jobs");
  literal(body.object, "list", "jobs.object");
  if (!Array.isArray(body.data)) contract("jobs.data is not an array");
  return {
    items: body.data.map((job, index) => decodeJob(job, `jobs.data[${index}]`)),
    nextCursor: nullableString(body.nextCursor, "jobs.nextCursor"),
  };
}

function decodeDeliverable(value: unknown, at: string): Deliverable {
  const body = record(value, at);
  const sha256 = string(body.sha256, `${at}.sha256`);
  if (!sha256Pattern.test(sha256)) contract(`${at}.sha256 is not lowercase SHA-256`);
  const kind = string(body.kind, `${at}.kind`);
  if (kind !== "mp4" && kind !== "mp3" && kind !== "srt" && kind !== "csv" && kind !== "docx") {
    contract(`${at}.kind is unknown`);
  }
  const wire: DeliverableWire = {
    id: uuid(body.id, `${at}.id`),
    jobId: uuid(body.jobId, `${at}.jobId`),
    kind,
    fileName: string(body.fileName, `${at}.fileName`),
    contentType: string(body.contentType, `${at}.contentType`),
    byteSize: integer(body.byteSize, `${at}.byteSize`),
    sha256,
    createdAt: string(body.createdAt, `${at}.createdAt`),
  };
  if (wire.byteSize < 0) contract(`${at}.byteSize is negative`);
  return { deliverableId: wire.id, jobId: wire.jobId, kind: wire.kind, fileName: wire.fileName, contentType: wire.contentType, byteSize: wire.byteSize, sha256: wire.sha256, createdAt: wire.createdAt };
}

export function decodeDeliverables(value: unknown): Deliverables {
  const body = record(value, "deliverables");
  if (!Array.isArray(body.items)) contract("deliverables.items is not an array");
  return {
    items: body.items.map((item, index) => decodeDeliverable(item, `deliverables.items[${index}]`)),
    completedSet: literal(body.completedSet, true, "deliverables.completedSet"),
  };
}

export function decodeCapabilities(value: unknown): Capabilities {
  const body = record(value, "capabilities");
  const review = record(body.review, "capabilities.review");
  const uploads = record(body.uploads, "capabilities.uploads");
  const idempotency = record(body.idempotency, "capabilities.idempotency");
  const tts = record(body.tts, "capabilities.tts");
  const previews = record(tts.previews, "capabilities.tts.previews");
  if (!Array.isArray(body.resources) || !body.resources.every((item) => typeof item === "string")) contract("capabilities.resources is invalid");
  if (!Array.isArray(body.jobStates) || !body.jobStates.every((item) => typeof item === "string" && jobStates.has(item))) contract("capabilities.jobStates is invalid");
  if (!Array.isArray(uploads.contentTypes) || !uploads.contentTypes.every((item) => typeof item === "string")) contract("capabilities.uploads.contentTypes is invalid");
  const wire: CapabilitiesWire = {
    brand: literal(body.brand, "InstaDescribe", "capabilities.brand"),
    apiVersion: literal(body.apiVersion, "v1-beta", "capabilities.apiVersion"),
    organizationId: uuid(body.organizationId, "capabilities.organizationId"),
    resources: body.resources as string[],
    jobStates: body.jobStates as CapabilitiesWire["jobStates"],
    review: { mode: literal(review.mode, "web", "capabilities.review.mode") },
    uploads: {
      maxBytes: integer(uploads.maxBytes, "capabilities.uploads.maxBytes"),
      maxDurationSeconds: number(uploads.maxDurationSeconds, "capabilities.uploads.maxDurationSeconds"),
      contentTypes: uploads.contentTypes as string[],
    },
    idempotency: {
      requiredForWrites: literal(idempotency.requiredForWrites, true, "capabilities.idempotency.requiredForWrites"),
      retentionSeconds: integer(idempotency.retentionSeconds, "capabilities.idempotency.retentionSeconds"),
    },
    tts: {
      maxApprovedScenesPerReview: integer(tts.maxApprovedScenesPerReview, "capabilities.tts.maxApprovedScenesPerReview"),
      maxRenderAttemptsPerReview: integer(tts.maxRenderAttemptsPerReview, "capabilities.tts.maxRenderAttemptsPerReview"),
      maxFinalSynthesisCallsPerReview: integer(tts.maxFinalSynthesisCallsPerReview, "capabilities.tts.maxFinalSynthesisCallsPerReview"),
      previews: {
        rollingWindowSeconds: integer(previews.rollingWindowSeconds, "capabilities.tts.previews.rollingWindowSeconds"),
        maxRequestsPerJob: integer(previews.maxRequestsPerJob, "capabilities.tts.previews.maxRequestsPerJob"),
        maxRequestsPerOrganization: integer(previews.maxRequestsPerOrganization, "capabilities.tts.previews.maxRequestsPerOrganization"),
        maxActivePerOrganization: integer(previews.maxActivePerOrganization, "capabilities.tts.previews.maxActivePerOrganization"),
        maxAttemptsPerRequest: integer(previews.maxAttemptsPerRequest, "capabilities.tts.previews.maxAttemptsPerRequest"),
      },
    },
  };
  return wire;
}
