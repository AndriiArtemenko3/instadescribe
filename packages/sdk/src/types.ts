import type {
  IntegrationJobCreate,
  IntegrationJobResponse,
  IntegrationProductSettings,
} from "./generated/openapi/types.gen.js";

export type PublicJobState = IntegrationJobResponse["state"];
export type ProjectSelector =
  | { id: string; name?: never; externalId?: string }
  | { id?: never; name: string; externalId?: string };
export type ProductSettings = Omit<
  IntegrationProductSettings,
  "instructions" | "language" | "voice"
> & {
  instructions?: string;
  language?: string;
  voice?: string;
};
export type CreateJobInput = Omit<
  IntegrationJobCreate,
  "clientReference" | "project" | "settings"
> & {
  clientReference?: string;
  project: ProjectSelector;
  settings: ProductSettings;
};

export interface WriteOptions {
  /** Stable value for safely retrying the same write; generated when omitted. */
  idempotencyKey?: string | undefined;
  signal?: AbortSignal | undefined;
}

export interface SubmitFileInput {
  filePath: string;
  project: ProjectSelector;
  transcriptPath?: string | undefined;
  clientReference?: string | undefined;
  durationSeconds?: number | undefined;
  settings?: CreateJobInput["settings"] | undefined;
  idempotencyKey?: string | undefined;
  completeIdempotencyKey?: string | undefined;
  signal?: AbortSignal | undefined;
}

export interface PresignedUpload {
  readonly method: "POST";
  /** Ephemeral signed storage URL. Never persist or log it. */
  readonly url: string;
  /** Ephemeral signed form fields. Never persist or log them. */
  readonly fields: Readonly<Record<string, string>>;
  readonly expiresAt: string;
}

export interface JobSource {
  status: "awaiting_upload" | "uploaded";
  contentType: string | null;
  sizeBytes: number | null;
  durationSeconds: number | null;
}

export interface JobError {
  code: string | null;
  message: string | null;
}

export interface JobSummary {
  jobId: string;
  projectId: string;
  clientReference: string | null;
  state: PublicJobState;
  progress: number;
  stage: string | null;
  pipelineRevision: string;
  source: JobSource;
  reviewUrl: string | null;
  error: JobError | null;
  createdAt: string;
  updatedAt: string;
}

export interface CreateJobResult {
  job: JobSummary;
  uploads: {
    video: PresignedUpload;
    transcript?: PresignedUpload | undefined;
  };
}

export interface Page<T> {
  items: T[];
  nextCursor: string | null;
}

export interface ListJobsOptions {
  limit?: number;
  cursor?: string | undefined;
  projectId?: string | undefined;
  signal?: AbortSignal | undefined;
}

export interface WaitForJobOptions {
  /** Defaults to needs_review or completed. */
  until?: PublicJobState | readonly PublicJobState[] | ((job: JobSummary) => boolean);
  timeoutMs?: number;
  pollIntervalMs?: number;
  signal?: AbortSignal | undefined;
  onProgress?: (job: JobSummary) => void | Promise<void>;
}

export interface Deliverable {
  deliverableId: string;
  jobId: string;
  kind: "mp4" | "mp3" | "srt" | "csv" | "docx";
  fileName: string;
  contentType: string;
  byteSize: number;
  sha256: string;
  createdAt: string;
}

export interface Deliverables {
  items: Deliverable[];
  completedSet: true;
}

export interface DownloadDeliverableOptions {
  overwrite?: boolean;
  signal?: AbortSignal | undefined;
}

export interface DownloadDeliverableResult {
  deliverable: Deliverable;
  path: string;
  bytes: number;
  sha256: string;
  reusedExisting: boolean;
}

export interface Capabilities {
  brand: "InstaDescribe";
  apiVersion: "v1-beta";
  organizationId: string;
  resources: string[];
  jobStates: PublicJobState[];
  review: { mode: "web" };
  uploads: { maxBytes: number; maxDurationSeconds: number; contentTypes: string[] };
  idempotency: { requiredForWrites: true; retentionSeconds: number };
  tts: {
    maxApprovedScenesPerReview: number;
    maxRenderAttemptsPerReview: number;
    maxFinalSynthesisCallsPerReview: number;
    previews: {
      rollingWindowSeconds: number;
      maxRequestsPerJob: number;
      maxRequestsPerOrganization: number;
      maxActivePerOrganization: number;
      maxAttemptsPerRequest: number;
    };
  };
}

export interface InstaDescribeOptions {
  /** Public Integration API origin only, for example https://api.instadescribe.com. */
  baseUrl: string | URL;
  apiKey: string;
  /** Expected Web App origin used to validate server-provided review URLs. */
  appUrl?: string | URL;
  fetch?: typeof globalThis.fetch;
  requestTimeoutMs?: number;
}
