// Cloud direct-upload flow (G7 B3, made recoverable by G7.1 A).
//
// Explicit in-memory stages: validated -> created (IDs + upload contract
// retained in memory only) -> uploaded -> completing -> accepted. A retry
// resumes the SAME session: after a successful create there is never a
// second create; after a successful S3 POST there is never a second upload —
// only upload-complete is retried for that exact jobId. Presigned fields/
// URLs never touch localStorage or sessionStorage. Request fields are
// mapped EXPLICITLY — `mode`, provider and pipelineRevision never leave the
// client.

import {
  CloudApiError,
  completeCloudUpload,
  createCloudJob,
  type CloudCreateResponse,
} from './cloudApi'
import type { UploadSettings } from '@/types'

export const CLOUD_MAX_FILE_BYTES = 250 * 1024 * 1024 // the server-visible cap
export const CLOUD_MAX_FILE_LABEL = '250 MiB'
export const CLOUD_MAX_DURATION_SECS = 300

// Exact extension/MIME pairs (mirrors the server contract).
const PAIRS: Record<string, string> = {
  '.mp4': 'video/mp4',
  '.mov': 'video/quicktime',
  '.webm': 'video/webm',
}

export interface CloudUploadInput {
  file: File
  projectName: string
  settings: UploadSettings
  customPrompt: string
  durationSecs: number
}

/** Returns a safe, user-facing reason when invalid; null when acceptable. */
export function validateCloudUpload(input: CloudUploadInput): string | null {
  const { file, settings, durationSecs } = input
  if (file.size > CLOUD_MAX_FILE_BYTES) {
    return `The cloud demo accepts files up to ${CLOUD_MAX_FILE_LABEL}.`
  }
  if (!Number.isFinite(durationSecs) || durationSecs <= 0) {
    return 'Could not read the video duration.'
  }
  if (durationSecs > CLOUD_MAX_DURATION_SECS) {
    return 'The cloud demo accepts clips up to 5 minutes.'
  }
  if (resolveContentType(file) === null) {
    return 'Use an .mp4, .mov or .webm file whose type matches its extension.'
  }
  if (settings.model !== 'gpt-4.1') return 'Only the gpt-4.1 model is available.'
  if (settings.fps !== 0.5 && settings.fps !== 1) return 'FPS must be 0.5 or 1.'
  if (settings.frameQuality !== 'low') return 'Only low frame quality is available.'
  if (settings.chunkSizeSecs !== 60 && settings.chunkSizeSecs !== 120) {
    return 'Chunk size must be 60 or 120 seconds.'
  }
  return null
}

/**
 * The exact extension<->MIME pair is the contract. A MISSING browser MIME is
 * normalized from a known extension; a contradictory non-empty pair is
 * rejected (null).
 */
export function resolveContentType(file: File): string | null {
  const dot = file.name.lastIndexOf('.')
  const ext = dot >= 0 ? file.name.slice(dot).toLowerCase() : ''
  const expected = PAIRS[ext]
  if (!expected) return null
  if (!file.type) return expected // normalize a missing browser MIME
  return file.type === expected ? expected : null
}

export interface CloudSubmitResult {
  projectId: string
  projectVersion: number
  jobId: string
}

export interface CloudUploadCallbacks {
  /** Fires immediately after create commits IDs, before the S3 POST starts. */
  onCreated?: (ids: CloudSubmitResult) => void
  /** Fires only after the direct S3 POST succeeds. */
  onUploaded?: (ids: CloudSubmitResult) => void
}

export type CloudUploadStage = 'validated' | 'created' | 'uploaded' | 'completing' | 'accepted'

/** Direct browser POST to S3: returned fields VERBATIM, then the file.
    Plain fetch with credentials omitted, no referrer, and fail-closed
    redirect handling — the portfolio token never travels to S3 (G7.1 B). */
async function postToS3(upload: CloudCreateResponse['upload'], file: File): Promise<void> {
  const form = new FormData()
  for (const [key, value] of Object.entries(upload.fields)) form.append(key, value)
  form.append('file', file)
  let res: Response
  try {
    res = await fetch(upload.url, {
      method: 'POST',
      body: form,
      credentials: 'omit',
      referrerPolicy: 'no-referrer',
      redirect: 'error',
    })
  } catch {
    throw new CloudApiError('network')
  }
  if (!res.ok) throw new CloudApiError('service', res.status)
}

/** Retry upload-complete for the SAME job on retryable conditions — never
    create or upload a duplicate. */
export async function completeWithRetry(
  jobId: string,
  attempts = 3,
  backoffMs = 1500,
): Promise<void> {
  let last: unknown
  for (let i = 0; i < attempts; i++) {
    try {
      await completeCloudUpload(jobId)
      return
    } catch (err) {
      last = err
      if (err instanceof CloudApiError && err.retryable && i < attempts - 1) {
        await new Promise((r) => setTimeout(r, backoffMs * (i + 1)))
        continue
      }
      throw err
    }
  }
  throw last instanceof Error ? last : new CloudApiError('service')
}

function contractStillValid(upload: CloudCreateResponse['upload']): boolean {
  const expires = Date.parse(upload.expiresAt)
  return !Number.isNaN(expires) && expires - Date.now() > 10_000
}

/**
 * One resumable upload session per selected file. `run()` advances through
 * the stages and may be called again after a failure: completed stages are
 * never repeated (exactly one create, exactly one successful S3 POST); only
 * upload-complete is retried for the same jobId.
 */
export class CloudUploadSession {
  stage: CloudUploadStage = 'validated'
  private created: CloudCreateResponse | null = null
  private inFlight = false
  private abandoned = false
  private readonly completeAttempts: number
  private readonly backoffMs: number

  constructor(opts: { completeAttempts?: number; backoffMs?: number } = {}) {
    this.completeAttempts = opts.completeAttempts ?? 3
    this.backoffMs = opts.backoffMs ?? 1500
  }

  get ids(): CloudSubmitResult | null {
    return this.created ? {
      projectId: this.created.projectId,
      projectVersion: this.created.projectVersion,
      jobId: this.created.jobId,
    } : null
  }

  /** Input replacement, cancel, or unmount abandons an incomplete
      reservation. An in-flight fetch cannot always be cancelled, but its
      completion is no longer allowed to publish a card or begin completion. */
  abandon(): void {
    this.abandoned = true
  }

  private ensureActive(): void {
    if (this.abandoned) throw new CloudApiError('conflict')
  }

  async run(input: CloudUploadInput, callbacks: CloudUploadCallbacks = {}): Promise<CloudSubmitResult> {
    this.ensureActive()
    if (this.inFlight) throw new CloudApiError('validation') // double-submit guard
    this.inFlight = true
    try {
      // Bounded staged loop: a contract that lapsed BEFORE any byte was
      // uploaded triggers exactly ONE recreate (continue); a second
      // consecutive lapse fails loudly instead of minting jobs forever.
      for (let recreates = 0; ; ) {
        if (!this.created) {
          const reason = validateCloudUpload(input)
          if (reason) throw new CloudApiError('validation')
          const contentType = resolveContentType(input.file)
          if (!contentType) throw new CloudApiError('validation')
          // EXPLICIT mapping — no spread, no mode/provider/pipelineRevision.
          this.created = await createCloudJob({
            name: input.projectName || 'Untitled Project',
            durationSecs: input.durationSecs,
            fileName: input.file.name,
            contentType,
            fileSizeBytes: input.file.size,
            settings: {
              model: input.settings.model,
              frameQuality: input.settings.frameQuality,
              fps: input.settings.fps,
              chunkSizeSecs: input.settings.chunkSizeSecs,
              audioExtraction: input.settings.audioExtraction,
              customPrompt: input.customPrompt,
              detailLevel: input.settings.detailLevel,
              presetStyle: input.settings.presetStyle,
              language: input.settings.language,
            },
          })
          this.stage = 'created'
          this.ensureActive()
          callbacks.onCreated?.(this.ids!)
        }
        if (this.stage === 'created' && !contractStillValid(this.created.upload)) {
          if (recreates >= 1) throw new CloudApiError('service')
          recreates += 1
          this.created = null
          this.stage = 'validated'
          continue
        }
        break
      }
      if (this.stage === 'created') {
        await postToS3(this.created!.upload, input.file) // same POST on retry
        this.ensureActive()
        this.stage = 'uploaded'
        callbacks.onUploaded?.(this.ids!)
      }
      if (this.stage === 'uploaded' || this.stage === 'completing') {
        this.stage = 'completing'
        await completeWithRetry(this.created!.jobId, this.completeAttempts, this.backoffMs)
        this.ensureActive()
        this.stage = 'accepted'
      }
      return {
        projectId: this.created!.projectId,
        projectVersion: this.created!.projectVersion,
        jobId: this.created!.jobId,
      }
    } finally {
      this.inFlight = false
    }
  }
}

/** Stage-truthful UI copy for submit failures — never raw responses, and
    never "your upload is kept" unless the S3 POST actually completed. */
export function submitErrorMessage(err: unknown, stage: CloudUploadStage = 'validated'): string {
  if (err instanceof CloudApiError) {
    if (err.category === 'auth') {
      return 'Access token missing or not accepted. Check the portfolio token and sign in again.'
    }
    if (err.category === 'validation') {
      return 'The upload was rejected by validation. Check the file and settings.'
    }
    if (err.category === 'conflict') {
      return 'This job can no longer be submitted. Start a new upload.'
    }
    if (err.category === 'not_found') {
      return 'This job no longer exists on the server. Start a new upload.'
    }
    if (stage === 'completing') {
      return err.category === 'capacity'
        ? 'Your video is uploaded. Another clip owns the processing slot; press Confirm later to retry this same job.'
        : 'Your video is uploaded — the final confirmation is pending. Press Confirm to retry the confirmation for the same job.'
    }
    if (err.category === 'capacity') {
      return 'Another clip is processing right now. Try again once it finishes.'
    }
    if (stage === 'created') {
      return 'The file transfer to storage failed. Press Confirm to retry the upload.'
    }
    if (err.category === 'network') {
      return 'Network problem while starting the job. Check your connection and retry.'
    }
    return 'The service is temporarily unavailable. Retry in a moment.'
  }
  return 'Failed to start the job. Please try again.'
}
