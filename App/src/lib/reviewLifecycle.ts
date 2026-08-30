import { CloudApiError, cloudFetch, errorFrom } from './cloudApi'

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const SHA256 = /^[0-9a-f]{64}$/
const REVIEW_STATES = new Set(['open', 'completed', 'expired'] as const)
const RENDER_STATES = new Set(['queued', 'rendering', 'completed', 'failed', 'cancelled'] as const)
const DELIVERABLE_KINDS = new Set(['mp4', 'mp3', 'srt', 'csv', 'docx'] as const)

export type CloudReviewState = 'open' | 'completed' | 'expired'
export type CloudRenderState = 'queued' | 'rendering' | 'completed' | 'failed' | 'cancelled'
export type CloudDeliverableKind = 'mp4' | 'mp3' | 'srt' | 'csv' | 'docx'

export interface CloudReviewSnapshot {
  id: string
  jobId: string
  state: CloudReviewState
  version: number
  locked: boolean
  sceneCount: number | null
  decidedSceneCount: number | null
  approvedSceneCount: number | null
  rejectedSceneCount: number | null
  zeroAdConfirmed: boolean
  lockedAt: string | null
  completedAt: string | null
  expiresAt: string
  createdAt: string
  updatedAt: string
}

export interface CloudRenderSnapshot {
  id: string
  jobId: string
  reviewId: string
  state: CloudRenderState
  attemptCount: number
  error: { code: string } | null
  createdAt: string
  updatedAt: string
  startedAt: string | null
  completedAt: string | null
}

export interface CloudDeliverable {
  id: string
  jobId: string
  kind: CloudDeliverableKind
  fileName: string
  contentType: string
  byteSize: number
  sha256: string
  createdAt: string
}

export interface CloudDeliverableSet {
  items: CloudDeliverable[]
  completedSet: true
}

function object(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function exact(value: Record<string, unknown>, fields: readonly string[]): boolean {
  const keys = Object.keys(value)
  return keys.length === fields.length && keys.every((key) => fields.includes(key))
}

function timestamp(value: unknown, nullable = false): value is string | null {
  if (nullable && value === null) return true
  return typeof value === 'string' && Number.isFinite(Date.parse(value))
}

function count(value: unknown): value is number | null {
  return value === null || (typeof value === 'number' && Number.isInteger(value) && value >= 0)
}

async function json(response: Response): Promise<unknown> {
  const mediaType = response.headers.get('Content-Type')?.split(';', 1)[0].trim().toLowerCase()
  if (mediaType !== 'application/json') throw new CloudApiError('service', response.status)
  try {
    return await response.json()
  } catch {
    throw new CloudApiError('service', response.status)
  }
}

const REVIEW_FIELDS = [
  'id', 'object', 'jobId', 'state', 'version', 'locked', 'sceneCount', 'decidedSceneCount',
  'approvedSceneCount', 'rejectedSceneCount', 'zeroAdConfirmed', 'lockedAt', 'completedAt',
  'expiresAt', 'createdAt', 'updatedAt',
] as const

function reviewSnapshot(value: unknown, jobId: string): CloudReviewSnapshot | null {
  const item = object(value)
  if (
    !item || !exact(item, REVIEW_FIELDS) || !UUID.test(String(item.id)) ||
    item.object !== 'review' || item.jobId !== jobId ||
    typeof item.state !== 'string' || !REVIEW_STATES.has(item.state as CloudReviewState) ||
    typeof item.version !== 'number' || !Number.isInteger(item.version) || item.version < 1 ||
    typeof item.locked !== 'boolean' || typeof item.zeroAdConfirmed !== 'boolean' ||
    !count(item.sceneCount) || !count(item.decidedSceneCount) ||
    !count(item.approvedSceneCount) || !count(item.rejectedSceneCount) ||
    !timestamp(item.lockedAt, true) || !timestamp(item.completedAt, true) ||
    !timestamp(item.expiresAt) || !timestamp(item.createdAt) || !timestamp(item.updatedAt)
  ) return null
  return item as unknown as CloudReviewSnapshot
}

const RENDER_FIELDS = [
  'id', 'object', 'jobId', 'reviewId', 'state', 'attemptCount', 'error', 'createdAt',
  'updatedAt', 'startedAt', 'completedAt',
] as const

function renderSnapshot(value: unknown, jobId: string): CloudRenderSnapshot | null {
  const item = object(value)
  const error = item ? object(item.error) : null
  if (
    !item || !exact(item, RENDER_FIELDS) || !UUID.test(String(item.id)) ||
    !UUID.test(String(item.reviewId)) || item.object !== 'render' || item.jobId !== jobId ||
    typeof item.state !== 'string' || !RENDER_STATES.has(item.state as CloudRenderState) ||
    typeof item.attemptCount !== 'number' || !Number.isInteger(item.attemptCount) || item.attemptCount < 0 ||
    !(item.error === null || (error && exact(error, ['code']) && typeof error.code === 'string')) ||
    !timestamp(item.createdAt) || !timestamp(item.updatedAt) ||
    !timestamp(item.startedAt, true) || !timestamp(item.completedAt, true)
  ) return null
  return item as unknown as CloudRenderSnapshot
}

const DELIVERABLE_FIELDS = [
  'id', 'jobId', 'kind', 'fileName', 'contentType', 'byteSize', 'sha256', 'createdAt',
] as const

function deliverable(value: unknown, jobId: string): CloudDeliverable | null {
  const item = object(value)
  if (
    !item || !exact(item, DELIVERABLE_FIELDS) || !UUID.test(String(item.id)) ||
    item.jobId !== jobId || typeof item.kind !== 'string' ||
    !DELIVERABLE_KINDS.has(item.kind as CloudDeliverableKind) ||
    typeof item.fileName !== 'string' || item.fileName.length < 1 || item.fileName.length > 255 ||
    typeof item.contentType !== 'string' || item.contentType.length < 1 ||
    typeof item.byteSize !== 'number' || !Number.isInteger(item.byteSize) || item.byteSize < 0 ||
    typeof item.sha256 !== 'string' || !SHA256.test(item.sha256) || !timestamp(item.createdAt)
  ) return null
  return item as unknown as CloudDeliverable
}

export async function fetchCloudReview(jobId: string): Promise<CloudReviewSnapshot> {
  if (!UUID.test(jobId)) throw new CloudApiError('validation')
  const response = await cloudFetch(`/api/v1/jobs/${jobId}/review`)
  if (!response.ok) throw await errorFrom(response)
  const parsed = reviewSnapshot(await json(response), jobId)
  if (!parsed) throw new CloudApiError('service', response.status)
  return parsed
}

export async function fetchCloudRender(jobId: string): Promise<CloudRenderSnapshot> {
  if (!UUID.test(jobId)) throw new CloudApiError('validation')
  const response = await cloudFetch(`/api/v1/jobs/${jobId}/render`)
  if (!response.ok) throw await errorFrom(response)
  const parsed = renderSnapshot(await json(response), jobId)
  if (!parsed) throw new CloudApiError('service', response.status)
  return parsed
}

export async function fetchCloudDeliverables(jobId: string): Promise<CloudDeliverableSet> {
  if (!UUID.test(jobId)) throw new CloudApiError('validation')
  const response = await cloudFetch(`/api/v1/jobs/${jobId}/deliverables`)
  if (!response.ok) throw await errorFrom(response)
  const body = object(await json(response))
  if (!body || !exact(body, ['items', 'completedSet']) || body.completedSet !== true || !Array.isArray(body.items)) {
    throw new CloudApiError('service', response.status)
  }
  const items = body.items.map((item) => deliverable(item, jobId))
  const kinds = new Set(items.map((item) => item?.kind))
  if (items.some((item) => item === null) || items.length !== 5 || kinds.size !== 5) {
    throw new CloudApiError('service', response.status)
  }
  return { items: items as CloudDeliverable[], completedSet: true }
}

/** Same-origin control URL; the BFF returns 303 and media bytes flow directly from S3. */
export function cloudDeliverableHref(deliverableId: string): string {
  if (!UUID.test(deliverableId)) throw new CloudApiError('validation')
  return `/api/bff/cloud/deliverables/${encodeURIComponent(deliverableId)}/content`
}
