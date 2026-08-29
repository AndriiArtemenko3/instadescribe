// Cloud-core API client (G7, hardened by G7.1 B). The ONLY module that
// attaches the portfolio token. The constraint lives in cloudFetch ITSELF,
// not merely its path argument: production requests are same-origin;
// development permits only the documented loopback API origins; userinfo,
// query, fragment and unexpected base/path forms are rejected; protected
// requests use redirect:"error", credentials:"omit" and
// referrerPolicy:"no-referrer", so neither a malicious configured base nor
// a redirect can ever receive X-Portfolio-Token. Errors are typed and
// sanitized: only an ALLOWLISTED detail.code is parsed from API error JSON
// and every other body byte is discarded.

import { cloudApiBase } from './cloudMode'
import { getPortfolioToken } from './portfolioToken'
import { isAppRouterRuntime, isDevelopmentRuntime } from './runtimeEnv'

export type CloudErrorCategory =
  | 'auth' // missing/wrong portfolio token
  | 'validation' // the request was rejected by the contract
  | 'not_found'
  | 'capacity' // another job holds the processing slot (retryable)
  | 'service' // transient server/storage/queue condition (retryable)
  | 'conflict' // non-retryable conflict (terminal state, changed identity)
  | 'failed' // the pipeline reported a terminal failure
  | 'network' // fetch/transport failure

// The ONLY server codes the client will ever interpret; anything else in a
// response body is discarded unread.
const CODE_ALLOWLIST = new Set([
  'capacity_conflict',
  'source_not_visible',
  'terminal_conflict',
  'source_identity_changed',
  'source_mismatch',
  'artifacts_not_ready',
  'job_not_editable',
  'invalid_scene_id',
  'manifest_unavailable',
  'persistence_unavailable',
  'enqueue_unavailable',
  'storage_unavailable',
  'stale_version',
  'precondition_failed',
  'precondition_required',
  'invalid_idempotency_key',
  'idempotency_in_progress',
  'idempotency_key_expired',
  'idempotency_key_reused',
  'idempotency_unavailable',
  'job_capacity_exceeded',
  'media_quota_exceeded',
  'quota_unavailable',
  'upload_not_reserved',
  'upload_state_conflict',
  'upload_completion_failed',
  'review_not_available',
  'review_not_editable',
  'review_already_completed',
  'scene_decisions_incomplete',
  'scene_decisions_invalid',
  'zero_ad_confirmation_required',
  'zero_ad_confirmation_invalid',
  'render_not_started',
  'deliverables_not_ready',
  'download_unavailable',
  'preview_not_available',
  'preview_capacity_exceeded',
  'preview_in_progress',
  'preview_not_ready',
  'preview_unavailable',
  'not_found',
])

export class CloudApiError extends Error {
  readonly category: CloudErrorCategory
  readonly status?: number
  readonly code?: string
  readonly retryable: boolean

  constructor(category: CloudErrorCategory, status?: number, code?: string) {
    super(`cloud api: ${category}${status ? ` (${status})` : ''}`)
    this.name = 'CloudApiError'
    this.category = category
    this.status = status
    this.code = code
    this.retryable = category === 'capacity' || category === 'service' || category === 'network'
  }
}

/** Extract ONLY an allowlisted legacy or RFC 9457 code; all other fields are discarded. */
async function allowlistedCode(res: Response): Promise<string | undefined> {
  try {
    const body = (await res.json()) as { code?: unknown; detail?: { code?: unknown } }
    const code = body?.code ?? body?.detail?.code
    return typeof code === 'string' && CODE_ALLOWLIST.has(code) ? code : undefined
  } catch {
    return undefined
  }
}

function categorize(status: number, code?: string): CloudErrorCategory {
  if (status === 401 || status === 403) return 'auth'
  if (status === 404) return 'not_found'
  if (status === 409) {
    // A generic 409 must NOT automatically mean capacity (G7.1 A).
    if (code === 'capacity_conflict') return 'capacity'
    if (code === 'source_not_visible') return 'service'
    return 'conflict' // terminal_conflict / source_identity_changed / unknown
  }
  if (status === 422 || status === 400) return 'validation'
  if (status === 503 || status === 502 || status === 504 || status === 500) return 'service'
  return 'service'
}

export async function errorFrom(res: Response): Promise<CloudApiError> {
  const code = await allowlistedCode(res)
  return new CloudApiError(categorize(res.status, code), res.status, code)
}

const DEV_ALLOWED_ORIGINS = new Set(['http://localhost:8000', 'http://127.0.0.1:8000'])

/** Validate base+path and build the final same-origin/loopback URL. */
function protectedUrl(path: string): string {
  if (!path.startsWith('/api/') || path.includes('?') || path.includes('#') || path.includes('..')) {
    throw new CloudApiError('validation')
  }
  const base = cloudApiBase()
  if (base === '') {
    return path // same-origin — the production cloud form
  }
  let parsed: URL
  try {
    parsed = new URL(base)
  } catch {
    throw new CloudApiError('validation')
  }
  if (
    parsed.username ||
    parsed.password ||
    parsed.search ||
    parsed.hash ||
    (parsed.pathname !== '/' && parsed.pathname !== '')
  ) {
    throw new CloudApiError('validation')
  }
  if (!isDevelopmentRuntime() || !DEV_ALLOWED_ORIGINS.has(parsed.origin)) {
    throw new CloudApiError('validation') // only documented loopback origins
  }
  return `${parsed.origin}${path}`
}

/**
 * Fetch a PROTECTED cloud API path. By construction the token can only
 * travel to the same-origin `/api/*` surface (production) or the documented
 * loopback API (development) — never S3, signed URLs, foreign origins, or
 * across a redirect.
 */
export async function cloudFetch(path: string, init: RequestInit = {}): Promise<Response> {
  if (isAppRouterRuntime()) {
    if (!path.startsWith('/api/v1/') || path.includes('?') || path.includes('#') || path.includes('..')) {
      throw new CloudApiError('validation')
    }
    const method = (init.method ?? 'GET').toUpperCase()
    const headers = new Headers(init.headers)
    if (method !== 'GET' && method !== 'HEAD') {
      const csrf = typeof document === 'undefined'
        ? null
        : document.cookie
          .split(';')
          .map((item) => item.trim())
          .find((item) => item.startsWith('__Host-instadescribe_csrf='))
          ?.slice('__Host-instadescribe_csrf='.length) ?? null
      if (!csrf || !/^[A-Za-z0-9_-]{43}$/.test(csrf)) throw new CloudApiError('auth')
      headers.set('X-CSRF-Token', csrf)
      // Browser writes use the same organization/path-scoped idempotency
      // boundary as service clients. A caller may provide a stable key for a
      // deliberate replay; otherwise one logical UI mutation gets one fresh
      // key and cloudFetch itself performs no automatic write retry.
      if (!headers.has('Idempotency-Key')) headers.set('Idempotency-Key', crypto.randomUUID())
    }
    try {
      return await fetch(`/api/bff/cloud/${path.slice('/api/v1/'.length)}`, {
        ...init,
        method,
        headers,
        redirect: 'error',
        credentials: 'same-origin',
        referrerPolicy: 'no-referrer',
      })
    } catch {
      throw new CloudApiError('network')
    }
  }
  const url = protectedUrl(path)
  const token = getPortfolioToken()
  if (!token) throw new CloudApiError('auth')
  const headers = new Headers(init.headers)
  headers.set('X-Portfolio-Token', token)
  let res: Response
  try {
    res = await fetch(url, {
      ...init,
      headers,
      redirect: 'error',
      credentials: 'omit',
      referrerPolicy: 'no-referrer',
    })
  } catch {
    throw new CloudApiError('network')
  }
  return res
}

// ── Contracts (G3–G6, authoritative on the server) ─────────────────────────

export interface CloudCreateSettings {
  model: string
  frameQuality: string
  fps: number
  chunkSizeSecs: number
  audioExtraction: boolean
  customPrompt: string
  detailLevel: number
  presetStyle: string
  language: string | null
}

export interface CloudCreateRequest {
  name: string
  durationSecs: number
  fileName: string
  contentType: string
  fileSizeBytes: number
  settings: CloudCreateSettings
}

export interface CloudCreateResponse {
  projectId: string
  projectVersion: number
  jobId: string
  upload: {
    url: string
    fields: Record<string, string>
    expiresAt: string
  }
}

export interface CloudJobStatus {
  id: string
  projectId: string
  projectVersion: number
  project_name: string
  starred: boolean
  status: 'queued' | 'processing' | 'ready' | 'failed'
  canonicalState:
    | 'AWAITING_UPLOAD'
    | 'UPLOAD_COMPLETE'
    | 'QUEUED'
    | 'PROCESSING'
    | 'READY_FOR_REVIEW'
    | 'EXPORT_QUEUED'
    | 'EXPORTING'
    | 'COMPLETED'
    | 'FAILED'
    | 'CANCELLED'
  /** Durable source-verification identity exists, independently of slot state. */
  sourceUploaded: boolean
  progress: number
  stage: string | null
  duration_secs: number | null
  model: string | null
  chunk_size: number | null
  pipeline_revision: string
  created_at: string | null
  updated_at: string | null
  error: string | null
  error_code: string | null
}

export async function createCloudJob(payload: CloudCreateRequest): Promise<CloudCreateResponse> {
  const res = await cloudFetch('/api/v1/jobs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw await errorFrom(res)
  const parsed = await parseJsonResponse(res)
  if (!isCreateResponse(parsed)) throw new CloudApiError('service', res.status)
  return parsed
}

/** upload-complete has NO body; both 200 and 202 are success. */
export async function completeCloudUpload(jobId: string): Promise<void> {
  const res = await cloudFetch(`/api/v1/jobs/${jobId}/upload-complete`, { method: 'POST' })
  if (res.status === 200 || res.status === 202) return
  throw await errorFrom(res)
}

export async function getCloudJob(jobId: string): Promise<CloudJobStatus> {
  const res = await cloudFetch(`/api/v1/jobs/${jobId}`)
  if (!res.ok) throw await errorFrom(res)
  const parsed = await parseJsonResponse(res)
  if (!isJobSummary(parsed, jobId)) throw new CloudApiError('service', res.status)
  return parsed
}

/** Map keyed by JOB id; each entry carries the distinct projectId. */
export async function listCloudJobs(): Promise<Record<string, CloudJobStatus>> {
  const res = await cloudFetch('/api/v1/jobs')
  if (!res.ok) throw await errorFrom(res)
  const parsed = await parseJsonResponse(res)
  if (!isJobsMap(parsed)) throw new CloudApiError('service', res.status)
  return parsed
}

const CANONICAL_STATES = new Set<CloudJobStatus['canonicalState']>([
  'AWAITING_UPLOAD', 'UPLOAD_COMPLETE', 'QUEUED', 'PROCESSING',
  'READY_FOR_REVIEW', 'EXPORT_QUEUED', 'EXPORTING', 'COMPLETED',
  'FAILED', 'CANCELLED',
])

const JOB_FIELDS = new Set<keyof CloudJobStatus>([
  'id', 'projectId', 'projectVersion', 'project_name', 'starred', 'status', 'canonicalState',
  'sourceUploaded', 'progress', 'stage', 'duration_secs', 'model', 'chunk_size',
  'pipeline_revision', 'created_at', 'updated_at', 'error', 'error_code',
])
const LEGACY_STATUSES = new Set<CloudJobStatus['status']>(['queued', 'processing', 'ready', 'failed'])
const ID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const ISO_TIMESTAMP_RE = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(?:Z|[+-](\d{2}):(\d{2}))$/

const EXPECTED_LEGACY_STATUS: Record<CloudJobStatus['canonicalState'], CloudJobStatus['status']> = {
  AWAITING_UPLOAD: 'queued',
  UPLOAD_COMPLETE: 'queued',
  QUEUED: 'queued',
  PROCESSING: 'processing',
  READY_FOR_REVIEW: 'ready',
  EXPORT_QUEUED: 'processing',
  EXPORTING: 'processing',
  COMPLETED: 'ready',
  FAILED: 'failed',
  CANCELLED: 'failed',
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === 'string'
}

function isTimestamp(value: unknown): value is string | null {
  if (value === null) return true
  if (typeof value !== 'string') return false
  const match = ISO_TIMESTAMP_RE.exec(value)
  if (!match || !Number.isFinite(Date.parse(value))) return false
  const [, yearRaw, monthRaw, dayRaw, hourRaw, minuteRaw, secondRaw, offsetHourRaw, offsetMinuteRaw] = match
  const year = Number(yearRaw)
  const month = Number(monthRaw)
  const day = Number(dayRaw)
  const hour = Number(hourRaw)
  const minute = Number(minuteRaw)
  const second = Number(secondRaw)
  const offsetHour = offsetHourRaw === undefined ? 0 : Number(offsetHourRaw)
  const offsetMinute = offsetMinuteRaw === undefined ? 0 : Number(offsetMinuteRaw)
  const leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0)
  const monthLengths = [31, leapYear ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
  const daysInMonth = month >= 1 && month <= 12 ? monthLengths[month - 1] : 0
  return day >= 1 && day <= daysInMonth &&
    hour <= 23 && minute <= 59 && second <= 59 &&
    offsetHour <= 23 && offsetMinute <= 59
}

function hasExactJobFields(candidate: Record<string, unknown>): boolean {
  const fields = Object.keys(candidate)
  return fields.length === JOB_FIELDS.size && fields.every((field) => JOB_FIELDS.has(field as keyof CloudJobStatus))
}

function hasExactFields(candidate: Record<string, unknown>, expected: ReadonlySet<string>): boolean {
  const fields = Object.keys(candidate)
  return fields.length === expected.size && fields.every((field) => expected.has(field))
}

function isPositiveInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 1
}

function isRequiredTimestamp(value: unknown): value is string {
  return typeof value === 'string' && isTimestamp(value)
}

function isAbsoluteHttpUrl(value: unknown): value is string {
  if (typeof value !== 'string') return false
  try {
    const parsed = new URL(value)
    return (parsed.protocol === 'http:' || parsed.protocol === 'https:') && !parsed.username && !parsed.password
  } catch {
    return false
  }
}

const CREATE_FIELDS = new Set(['projectId', 'projectVersion', 'jobId', 'upload'])
const UPLOAD_FIELDS = new Set(['url', 'fields', 'expiresAt'])

function isCreateResponse(value: unknown): value is CloudCreateResponse {
  if (!isRecord(value) || !hasExactFields(value, CREATE_FIELDS)) return false
  if (
    typeof value.projectId !== 'string' || !ID_RE.test(value.projectId) ||
    typeof value.jobId !== 'string' || !ID_RE.test(value.jobId) ||
    value.projectId === value.jobId ||
    !isPositiveInteger(value.projectVersion) ||
    !isRecord(value.upload) || !hasExactFields(value.upload, UPLOAD_FIELDS)
  ) return false
  const upload = value.upload
  return isAbsoluteHttpUrl(upload.url) &&
    isRecord(upload.fields) &&
    Object.keys(upload.fields).length > 0 &&
    Object.entries(upload.fields).every(([key, field]) => key.length > 0 && typeof field === 'string') &&
    isRequiredTimestamp(upload.expiresAt)
}

function isJobSummary(candidate: unknown, expectedJobId?: string): candidate is CloudJobStatus {
  if (!isRecord(candidate) || !hasExactJobFields(candidate)) return false
  const jobId = expectedJobId ?? candidate.id
  if (typeof jobId !== 'string' || !ID_RE.test(jobId)) return false
  if (
    typeof candidate.projectId !== 'string' ||
    !ID_RE.test(candidate.projectId) ||
    candidate.projectId === jobId ||
    !isPositiveInteger(candidate.projectVersion)
  ) return false
  if (
    typeof candidate.canonicalState !== 'string' ||
    !CANONICAL_STATES.has(candidate.canonicalState as CloudJobStatus['canonicalState']) ||
    typeof candidate.status !== 'string' ||
    !LEGACY_STATUSES.has(candidate.status as CloudJobStatus['status']) ||
    EXPECTED_LEGACY_STATUS[candidate.canonicalState as CloudJobStatus['canonicalState']] !== candidate.status
  ) return false
  return candidate.id === jobId &&
    typeof candidate.project_name === 'string' &&
    Array.from(candidate.project_name).length > 0 &&
    Array.from(candidate.project_name).length <= 200 &&
    typeof candidate.starred === 'boolean' &&
    typeof candidate.sourceUploaded === 'boolean' &&
    typeof candidate.progress === 'number' && Number.isInteger(candidate.progress) &&
    candidate.progress >= 0 && candidate.progress <= 100 &&
    isNullableString(candidate.stage) &&
    (candidate.duration_secs === null || (
      typeof candidate.duration_secs === 'number' && Number.isFinite(candidate.duration_secs) && candidate.duration_secs > 0
    )) &&
    (candidate.model === null || (typeof candidate.model === 'string' && candidate.model.length > 0)) &&
    (candidate.chunk_size === null || (
      typeof candidate.chunk_size === 'number' && Number.isInteger(candidate.chunk_size) && candidate.chunk_size > 0
    )) &&
    typeof candidate.pipeline_revision === 'string' && candidate.pipeline_revision.length > 0 &&
    isTimestamp(candidate.created_at) &&
    isTimestamp(candidate.updated_at) &&
    isNullableString(candidate.error) &&
    isNullableString(candidate.error_code)
}

function isJobsMap(value: unknown): value is Record<string, CloudJobStatus> {
  if (!isRecord(value)) return false
  return Object.entries(value).every(([jobId, candidate]) => isJobSummary(candidate, jobId))
}

function hasJsonMediaType(res: Response): boolean {
  return (res.headers.get('Content-Type') ?? '').split(';', 1)[0].trim().toLowerCase() === 'application/json'
}

async function parseJsonResponse(res: Response): Promise<unknown> {
  if (!hasJsonMediaType(res)) throw new CloudApiError('service', res.status)
  try {
    return await res.json()
  } catch {
    throw new CloudApiError('service', res.status)
  }
}

/** G7.1 B: validate a CANDIDATE token against a protected endpoint BEFORE
    it is persisted anywhere — the candidate travels only on this one
    request; nothing rejected can survive a mid-flight reload. True only
    when the server accepts it. */
export async function validatePortfolioToken(candidate: string): Promise<boolean> {
  if (!candidate) return false
  try {
    const url = protectedUrl('/api/v1/jobs')
    const res = await fetch(url, {
      headers: { 'X-Portfolio-Token': candidate },
      redirect: 'error',
      credentials: 'omit',
      referrerPolicy: 'no-referrer',
    })
    if (res.status !== 200) return false
    const parsed = await parseJsonResponse(res)
    return isJobsMap(parsed)
  } catch {
    // Transport/service problems are not proof of a wrong token, but they
    // must not admit a session either — fail closed.
    return false
  }
}

/** Cloud health probe (Settings): same base validation as every protected
    call, hardened fetch options, and NO token — /api/healthz is public. */
export async function probeCloudHealth(): Promise<boolean> {
  let target: string
  try {
    const base = cloudApiBase()
    if (base === '') {
      target = '/api/healthz'
    } else {
      const parsed = new URL(base)
      if (
        parsed.username || parsed.password || parsed.search || parsed.hash ||
        (parsed.pathname !== '/' && parsed.pathname !== '') ||
        !isDevelopmentRuntime() || !DEV_ALLOWED_ORIGINS.has(parsed.origin)
      ) {
        return false
      }
      target = `${parsed.origin}/api/healthz`
    }
    const res = await fetch(target, {
      method: 'GET',
      redirect: 'error',
      credentials: 'omit',
      referrerPolicy: 'no-referrer',
    })
    if (res.status !== 200) return false
    const parsed = await parseJsonResponse(res)
    return isRecord(parsed) && Object.keys(parsed).length === 1 && parsed.status === 'ok'
  } catch {
    return false
  }
}

export type CloudSceneReviewStatus = 'generated' | 'edited' | 'approved' | 'rejected'
export type CloudPersistedReviewStatus = Exclude<CloudSceneReviewStatus, 'generated'>
export type CloudSceneReviewCommand = CloudPersistedReviewStatus

export interface CloudSceneEditableFields {
  ad?: string
  active?: boolean
  locked?: boolean
  voice?: string
  speed?: number
}

export interface CloudSceneOverride extends CloudSceneEditableFields {
  version: number
  reviewStatus: CloudPersistedReviewStatus
  reviewedAt: string | null
  updatedAt: string
}

export interface CloudSceneMutation extends CloudSceneEditableFields {
  reviewStatus?: CloudSceneReviewCommand
}

export interface CloudProjectMutation {
  name?: string
  starred?: boolean
  expectedVersion: number
}

export interface CloudProjectResponse {
  projectId: string
  name: string
  starred: boolean
  version: number
  updatedAt: string
}

const PROJECT_RESPONSE_FIELDS = new Set(['projectId', 'name', 'starred', 'version', 'updatedAt'])
const OVERRIDE_REQUIRED_FIELDS = new Set([
  'active', 'locked', 'version', 'reviewStatus', 'reviewedAt', 'updatedAt',
])
const OVERRIDE_OPTIONAL_FIELDS = new Set(['ad', 'voice', 'speed'])
const PATCH_RESPONSE_FIELDS = new Set([
  'projectId', 'jobId', 'sceneId', 'version', 'reviewStatus', 'reviewedAt', 'updatedAt', 'override',
])
const REVIEW_STATUSES = new Set<CloudPersistedReviewStatus>(['edited', 'approved', 'rejected'])
const REVIEW_COMMANDS = new Set<CloudSceneReviewCommand>(['edited', 'approved', 'rejected'])
const VOICES = new Set(['onyx', 'nova', 'alloy', 'shimmer', 'echo', 'fable'])
const SCENE_ID_RE = /^scene_[1-9][0-9]*$/

function isReviewTimestamp(status: CloudPersistedReviewStatus, value: unknown): value is string | null {
  return status === 'approved' || status === 'rejected'
    ? isRequiredTimestamp(value)
    : value === null
}

function isSafeProjectName(value: unknown): value is string {
  return typeof value === 'string' &&
    value === value.trim() &&
    Array.from(value).length >= 1 &&
    Array.from(value).length <= 200 &&
    !Array.from(value).some((character) => {
      const code = character.codePointAt(0) ?? 0
      return code <= 0x1f || code === 0x7f
    })
}

function hasUnsafeAdControl(value: string): boolean {
  return Array.from(value).some((character) => {
    const code = character.codePointAt(0) ?? 0
    return code <= 0x08 || code === 0x0b || code === 0x0c ||
      (code >= 0x0e && code <= 0x1f) || code === 0x7f
  })
}

function isProjectResponse(value: unknown, expectedProjectId: string): value is CloudProjectResponse {
  return isRecord(value) &&
    hasExactFields(value, PROJECT_RESPONSE_FIELDS) &&
    value.projectId === expectedProjectId && ID_RE.test(expectedProjectId) &&
    isSafeProjectName(value.name) &&
    typeof value.starred === 'boolean' &&
    isPositiveInteger(value.version) &&
    isRequiredTimestamp(value.updatedAt)
}

function isPreciseSpeed(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0.5 && value <= 2.5 &&
    Math.abs(value * 100 - Math.round(value * 100)) < 1e-9
}

function hasExactOverrideFields(value: Record<string, unknown>): boolean {
  const keys = Object.keys(value)
  return OVERRIDE_REQUIRED_FIELDS.size <= keys.length &&
    keys.length <= OVERRIDE_REQUIRED_FIELDS.size + OVERRIDE_OPTIONAL_FIELDS.size &&
    keys.every((key) => OVERRIDE_REQUIRED_FIELDS.has(key) || OVERRIDE_OPTIONAL_FIELDS.has(key)) &&
    [...OVERRIDE_REQUIRED_FIELDS].every((key) => key in value)
}

function isCloudOverride(value: unknown): value is CloudSceneOverride {
  if (!isRecord(value) || !hasExactOverrideFields(value)) return false
  if (
    typeof value.active !== 'boolean' ||
    typeof value.locked !== 'boolean' ||
    !isPositiveInteger(value.version) ||
    typeof value.reviewStatus !== 'string' ||
    !REVIEW_STATUSES.has(value.reviewStatus as CloudPersistedReviewStatus) ||
    !isReviewTimestamp(value.reviewStatus as CloudPersistedReviewStatus, value.reviewedAt) ||
    !isRequiredTimestamp(value.updatedAt)
  ) return false
  if ('ad' in value && (
    typeof value.ad !== 'string' ||
    Array.from(value.ad).length > 8000 ||
    hasUnsafeAdControl(value.ad)
  )) return false
  if ('voice' in value && (typeof value.voice !== 'string' || !VOICES.has(value.voice))) return false
  if ('speed' in value && !isPreciseSpeed(value.speed)) return false
  return true
}

function isOverridesMap(value: unknown): value is Record<string, CloudSceneOverride> {
  return isRecord(value) && Object.entries(value).every(([sceneId, override]) =>
    sceneId.length <= 120 && SCENE_ID_RE.test(sceneId) && isCloudOverride(override),
  )
}

function isPatchResponse(
  value: unknown,
  expectedJobId: string,
  expectedSceneId: string,
): value is CloudPatchResponse {
  if (!isRecord(value) || !hasExactFields(value, PATCH_RESPONSE_FIELDS) || !isCloudOverride(value.override)) return false
  if (
    typeof value.projectId !== 'string' || !ID_RE.test(value.projectId) ||
    value.jobId !== expectedJobId || !ID_RE.test(expectedJobId) ||
    value.projectId === expectedJobId ||
    value.sceneId !== expectedSceneId || !SCENE_ID_RE.test(expectedSceneId) ||
    !isPositiveInteger(value.version) ||
    typeof value.reviewStatus !== 'string' ||
    !REVIEW_STATUSES.has(value.reviewStatus as CloudPersistedReviewStatus) ||
    !isReviewTimestamp(value.reviewStatus as CloudPersistedReviewStatus, value.reviewedAt) ||
    !isRequiredTimestamp(value.updatedAt)
  ) return false
  return value.version === value.override.version &&
    value.reviewStatus === value.override.reviewStatus &&
    value.reviewedAt === value.override.reviewedAt &&
    value.updatedAt === value.override.updatedAt
}

function validateSceneMutation(patch: CloudSceneMutation, expectedVersion: number): void {
  if (!Number.isInteger(expectedVersion) || expectedVersion < 0) throw new CloudApiError('validation')
  const entries = Object.entries(patch)
  const allowed = new Set(['ad', 'active', 'locked', 'voice', 'speed', 'reviewStatus'])
  if (entries.length === 0 || entries.some(([key, value]) => !allowed.has(key) || value === null || value === undefined)) {
    throw new CloudApiError('validation')
  }
  if ('ad' in patch && (
    typeof patch.ad !== 'string' ||
    Array.from(patch.ad).length > 8000 ||
    hasUnsafeAdControl(patch.ad)
  )) throw new CloudApiError('validation')
  if ('active' in patch && typeof patch.active !== 'boolean') throw new CloudApiError('validation')
  if ('locked' in patch && typeof patch.locked !== 'boolean') throw new CloudApiError('validation')
  if ('voice' in patch && (typeof patch.voice !== 'string' || !VOICES.has(patch.voice))) throw new CloudApiError('validation')
  if ('speed' in patch && !isPreciseSpeed(patch.speed)) throw new CloudApiError('validation')
  if ('reviewStatus' in patch && (typeof patch.reviewStatus !== 'string' || !REVIEW_COMMANDS.has(patch.reviewStatus))) throw new CloudApiError('validation')
}

export async function fetchCloudOverrides(jobId: string): Promise<Record<string, CloudSceneOverride>> {
  const res = await cloudFetch(`/api/v1/jobs/${jobId}/overrides`)
  if (!res.ok) throw await errorFrom(res)
  const parsed = await parseJsonResponse(res)
  if (!isOverridesMap(parsed)) throw new CloudApiError('service', res.status)
  return parsed
}

export interface CloudPatchResponse {
  projectId: string
  jobId: string
  sceneId: string
  version: number
  reviewStatus: CloudPersistedReviewStatus
  reviewedAt: string | null
  updatedAt: string
  override: CloudSceneOverride
}

export async function patchCloudProject(
  projectId: string,
  patch: CloudProjectMutation,
): Promise<CloudProjectResponse> {
  if (!ID_RE.test(projectId) || !isPositiveInteger(patch.expectedVersion)) {
    throw new CloudApiError('validation')
  }
  const keys = Object.keys(patch)
  if (
    keys.length < 2 || keys.length > 3 ||
    keys.some((key) => !['name', 'starred', 'expectedVersion'].includes(key)) ||
    (!('name' in patch) && !('starred' in patch)) ||
    ('name' in patch && !isSafeProjectName(patch.name)) ||
    ('starred' in patch && typeof patch.starred !== 'boolean')
  ) throw new CloudApiError('validation')
  const res = await cloudFetch(`/api/v1/projects/${projectId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  if (!res.ok) throw await errorFrom(res)
  const parsed = await parseJsonResponse(res)
  if (!isProjectResponse(parsed, projectId)) throw new CloudApiError('service', res.status)
  return parsed
}

/** PATCH the EXACT canonical pipeline scene id — never a synthesized index. */
export async function patchCloudScene(
  jobId: string,
  sceneId: string,
  patch: CloudSceneMutation,
  expectedVersion: number,
): Promise<CloudPatchResponse> {
  if (!ID_RE.test(jobId) || sceneId.length > 120 || !SCENE_ID_RE.test(sceneId)) {
    throw new CloudApiError('validation')
  }
  validateSceneMutation(patch, expectedVersion)
  const res = await cloudFetch(`/api/v1/jobs/${jobId}/scenes/${sceneId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...patch, expectedVersion }),
  })
  if (!res.ok) throw await errorFrom(res)
  const parsed = await parseJsonResponse(res)
  if (!isPatchResponse(parsed, jobId, sceneId)) throw new CloudApiError('service', res.status)
  return parsed
}

export type CloudTtsPreviewState = 'queued' | 'rendering' | 'completed' | 'failed' | 'cancelled'
export type CloudTtsPreviewVoice = 'onyx' | 'nova' | 'alloy' | 'shimmer' | 'echo' | 'fable'

export interface CloudTtsPreview {
  previewId: string
  jobId: string
  sceneId: string
  state: CloudTtsPreviewState
  contentReady: boolean
  errorCode: string | null
  createdAt: string
  updatedAt: string
  expiresAt: string
}

interface CloudTtsPreviewWaitOptions {
  signal?: AbortSignal
  /** Test seam; product calls use the bounded one-second interval. */
  pollIntervalMs?: number
  /** Test seam; product calls stop after four minutes. */
  maxPolls?: number
}

const TTS_PREVIEW_FIELDS = new Set([
  'previewId', 'jobId', 'sceneId', 'state', 'contentReady', 'errorCode',
  'createdAt', 'updatedAt', 'expiresAt',
])
const TTS_PREVIEW_STATES = new Set<CloudTtsPreviewState>([
  'queued', 'rendering', 'completed', 'failed', 'cancelled',
])
const TTS_PREVIEW_ERROR_RE = /^[a-z][a-z0-9_]{0,79}$/
const TTS_PREVIEW_MAX_BYTES = 10 * 1024 * 1024

function isCloudTtsPreview(
  value: unknown,
  expected: { previewId?: string; jobId?: string; sceneId?: string },
): value is CloudTtsPreview {
  if (!isRecord(value) || !hasExactFields(value, TTS_PREVIEW_FIELDS)) return false
  if (
    typeof value.previewId !== 'string' || !ID_RE.test(value.previewId) ||
    typeof value.jobId !== 'string' || !ID_RE.test(value.jobId) ||
    typeof value.sceneId !== 'string' || !SCENE_ID_RE.test(value.sceneId) ||
    (expected.previewId !== undefined && value.previewId !== expected.previewId) ||
    (expected.jobId !== undefined && value.jobId !== expected.jobId) ||
    (expected.sceneId !== undefined && value.sceneId !== expected.sceneId) ||
    typeof value.state !== 'string' ||
    !TTS_PREVIEW_STATES.has(value.state as CloudTtsPreviewState) ||
    typeof value.contentReady !== 'boolean' ||
    value.contentReady !== (value.state === 'completed') ||
    !isRequiredTimestamp(value.createdAt) ||
    !isRequiredTimestamp(value.updatedAt) ||
    !isRequiredTimestamp(value.expiresAt)
  ) return false
  return value.state === 'failed'
    ? typeof value.errorCode === 'string' && TTS_PREVIEW_ERROR_RE.test(value.errorCode)
    : value.errorCode === null
}

function validateTtsPreviewRequest(
  jobId: string,
  sceneId: string,
  text: string,
  voice: CloudTtsPreviewVoice,
  speed: number,
): string {
  const spokenText = text.trim()
  if (
    !isAppRouterRuntime() ||
    !ID_RE.test(jobId) ||
    sceneId.length > 120 || !SCENE_ID_RE.test(sceneId) ||
    Array.from(spokenText).length < 1 || Array.from(spokenText).length > 2000 ||
    hasUnsafeAdControl(spokenText) ||
    !VOICES.has(voice) ||
    !isPreciseSpeed(speed)
  ) throw new CloudApiError('validation')
  return spokenText
}

/** Queue one durable, worker-rendered preview; no provider credential enters the browser. */
export async function createCloudTtsPreview(
  jobId: string,
  sceneId: string,
  text: string,
  voice: CloudTtsPreviewVoice,
  speed: number,
  signal?: AbortSignal,
): Promise<CloudTtsPreview> {
  const spokenText = validateTtsPreviewRequest(jobId, sceneId, text, voice, speed)
  const response = await cloudFetch(`/api/v1/jobs/${jobId}/scenes/${sceneId}/tts-previews`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text: spokenText, voice, speed }),
    signal,
  })
  if (!response.ok) throw await errorFrom(response)
  const value = await parseJsonResponse(response)
  if (!isCloudTtsPreview(value, { jobId, sceneId })) {
    throw new CloudApiError('service', response.status)
  }
  return value
}

export async function getCloudTtsPreview(
  previewId: string,
  signal?: AbortSignal,
): Promise<CloudTtsPreview> {
  if (!isAppRouterRuntime() || !ID_RE.test(previewId)) throw new CloudApiError('validation')
  const response = await cloudFetch(`/api/v1/tts-previews/${previewId}`, { signal })
  if (!response.ok) throw await errorFrom(response)
  const value = await parseJsonResponse(response)
  if (!isCloudTtsPreview(value, { previewId })) {
    throw new CloudApiError('service', response.status)
  }
  return value
}

function waitForPreviewPoll(delayMs: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) return Promise.reject(new DOMException('Aborted', 'AbortError'))
  if (delayMs === 0) return Promise.resolve()
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => {
      signal?.removeEventListener('abort', aborted)
      resolve()
    }, delayMs)
    function aborted() {
      window.clearTimeout(timer)
      reject(new DOMException('Aborted', 'AbortError'))
    }
    signal?.addEventListener('abort', aborted, { once: true })
  })
}

async function boundedPreviewAudio(response: Response): Promise<Blob> {
  const declared = Number(response.headers.get('Content-Length') ?? 0)
  if (Number.isFinite(declared) && declared > TTS_PREVIEW_MAX_BYTES) {
    await response.body?.cancel()
    throw new CloudApiError('service', response.status)
  }
  if (!response.body) throw new CloudApiError('service', response.status)
  const reader = response.body.getReader()
  const chunks: Uint8Array[] = []
  let received = 0
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    received += value.byteLength
    if (received > TTS_PREVIEW_MAX_BYTES) {
      await reader.cancel()
      throw new CloudApiError('service', response.status)
    }
    chunks.push(value)
  }
  if (received === 0) throw new CloudApiError('service', response.status)
  const body = new Uint8Array(new ArrayBuffer(received))
  let offset = 0
  for (const chunk of chunks) {
    body.set(chunk, offset)
    offset += chunk.byteLength
  }
  return new Blob([body.buffer], { type: 'audio/mpeg' })
}

async function downloadCloudTtsPreview(previewId: string, signal?: AbortSignal): Promise<Blob> {
  let response: Response
  try {
    response = await fetch(`/api/bff/cloud/tts-previews/${previewId}/content`, {
      method: 'GET',
      headers: { Accept: 'audio/mpeg' },
      cache: 'no-store',
      credentials: 'same-origin',
      redirect: 'follow',
      referrerPolicy: 'no-referrer',
      signal,
    })
  } catch (error) {
    if (signal?.aborted) throw error
    throw new CloudApiError('network')
  }
  if (!response.ok) {
    // A followed storage response is not a trusted JSON contract. Do not
    // buffer or parse its body; classify only the bounded status metadata.
    throw new CloudApiError(categorize(response.status), response.status)
  }
  const mediaType = (response.headers.get('Content-Type') ?? '').split(';', 1)[0].trim().toLowerCase()
  if (mediaType !== 'audio/mpeg') throw new CloudApiError('service', response.status)
  return boundedPreviewAudio(response)
}

/** Queue, poll and fetch one immutable S3 version as a bounded in-memory audio Blob. */
export async function requestCloudTtsPreviewAudio(
  jobId: string,
  sceneId: string,
  text: string,
  voice: CloudTtsPreviewVoice,
  speed: number,
  options: CloudTtsPreviewWaitOptions = {},
): Promise<Blob> {
  const pollIntervalMs = options.pollIntervalMs ?? 1_000
  const maxPolls = options.maxPolls ?? 240
  if (!Number.isInteger(pollIntervalMs) || pollIntervalMs < 0 || pollIntervalMs > 10_000 ||
      !Number.isInteger(maxPolls) || maxPolls < 1 || maxPolls > 600) {
    throw new CloudApiError('validation')
  }
  let preview = await createCloudTtsPreview(jobId, sceneId, text, voice, speed, options.signal)
  for (let poll = 0; poll < maxPolls; poll += 1) {
    if (preview.state === 'completed') {
      return downloadCloudTtsPreview(preview.previewId, options.signal)
    }
    if (preview.state === 'failed') {
      throw new CloudApiError('failed', undefined, preview.errorCode ?? undefined)
    }
    if (preview.state === 'cancelled') throw new CloudApiError('conflict')
    await waitForPreviewPoll(pollIntervalMs, options.signal)
    preview = await getCloudTtsPreview(preview.previewId, options.signal)
  }
  throw new CloudApiError('service', 504, 'preview_unavailable')
}

export interface CloudFinishReviewResponse {
  jobId: string
  reviewId: string
  renderId: string
  reviewState: 'completed'
  renderState: 'queued'
  idempotent: boolean
}

export async function finishCloudReview(
  jobId: string,
  zeroAdConfirmed: boolean,
): Promise<CloudFinishReviewResponse> {
  if (!ID_RE.test(jobId)) throw new CloudApiError('validation')
  const res = await cloudFetch(`/api/v1/jobs/${jobId}/review/finish`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': crypto.randomUUID(),
    },
    body: JSON.stringify({ zeroAdConfirmed }),
  })
  if (!res.ok) throw await errorFrom(res)
  const value = await parseJsonResponse(res)
  if (
    !isRecord(value) ||
    !ID_RE.test(String(value.jobId)) || value.jobId !== jobId ||
    !ID_RE.test(String(value.reviewId)) || !ID_RE.test(String(value.renderId)) ||
    value.reviewState !== 'completed' || value.renderState !== 'queued' ||
    typeof value.idempotent !== 'boolean'
  ) throw new CloudApiError('service', res.status)
  return value as unknown as CloudFinishReviewResponse
}
