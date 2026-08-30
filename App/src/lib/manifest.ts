// Version-pinned artifact manifest client (G7 Gate B4, ADR-0003).
//
// The cloud editor is data-ready from a VALID manifest — never from
// project.dataPath. Artifact JSON and video/poster references are fetched
// with PLAIN fetch (no token header; the URLs are short-lived signatures).
// Signed URLs are ephemeral: never persisted, never placed in Zustand/
// localStorage/stable query keys, and refreshed before `expiresAt`.
// Errors expose only a logical artifact category and a safe HTTP class.

import { cloudFetch, errorFrom, CloudApiError } from './cloudApi'

export interface ManifestRef {
  url: string
  contentType: string
  sizeBytes: number
  checksumSha256: string
}

export interface CloudManifest {
  projectId: string
  jobId: string
  pipelineRevision: string
  expiresAt: string
  artifacts: {
    video: ManifestRef
    scenes: ManifestRef
    entities: ManifestRef
    audioEvents: ManifestRef
    placementGaps: ManifestRef
    transcript: ManifestRef
    posterJpg: ManifestRef | null
    posterAvif: ManifestRef | null
  }
}

const REQUIRED = [
  'video',
  'scenes',
  'entities',
  'audioEvents',
  'placementGaps',
  'transcript',
] as const

function isRef(value: unknown): value is ManifestRef {
  if (typeof value !== 'object' || value === null) return false
  const ref = value as Record<string, unknown>
  return (
    typeof ref.url === 'string' &&
    typeof ref.contentType === 'string' &&
    typeof ref.sizeBytes === 'number' &&
    typeof ref.checksumSha256 === 'string'
  )
}

/** Small typed runtime validator — identity is checked BEFORE any use. */
export function validateManifest(
  raw: unknown,
  expectedProjectId: string,
  expectedJobId: string,
): CloudManifest {
  const m = raw as CloudManifest
  if (typeof raw !== 'object' || raw === null) throw new CloudApiError('service')
  if (m.projectId !== expectedProjectId || m.jobId !== expectedJobId) {
    throw new CloudApiError('service') // identity mismatch: refuse to use it
  }
  if (typeof m.expiresAt !== 'string' || !m.expiresAt.endsWith('Z')) {
    throw new CloudApiError('service')
  }
  const artifacts = m.artifacts as Record<string, unknown> | undefined
  if (!artifacts) throw new CloudApiError('service')
  for (const key of REQUIRED) {
    if (!isRef(artifacts[key])) throw new CloudApiError('service')
  }
  for (const key of ['posterJpg', 'posterAvif'] as const) {
    if (artifacts[key] !== null && !isRef(artifacts[key])) throw new CloudApiError('service')
  }
  return m
}

export async function fetchManifest(
  projectId: string,
  jobId: string,
): Promise<CloudManifest> {
  const res = await cloudFetch(`/api/v1/jobs/${jobId}/manifest`)
  // Same allowlisted-code interpretation as every other protected call —
  // a manifest 409 (artifacts_not_ready) is a non-retryable conflict, not
  // capacity (G7.1 A).
  if (!res.ok) throw await errorFrom(res)
  return validateManifest(await res.json(), projectId, jobId)
}

/** Milliseconds until the manifest should be refreshed (safety margin before
    expiresAt); floor keeps a pathological clock from hot-looping. */
export function refreshDelayMs(manifest: CloudManifest, marginMs = 45_000): number {
  const expires = Date.parse(manifest.expiresAt)
  if (Number.isNaN(expires)) return 60_000
  return Math.max(15_000, expires - Date.now() - marginMs)
}

/** Fetch one artifact's JSON through its signed URL — PLAIN fetch, no token.
    Errors carry only the logical artifact name and a safe class. */
export async function fetchArtifactJson<T>(ref: ManifestRef, artifact: string): Promise<T> {
  let res: Response
  try {
    // G7.1 B: signed GETs are credential-free, referrer-free and fail
    // closed on redirects — nothing private can leak across an S3 hop.
    res = await fetch(ref.url, {
      credentials: 'omit',
      referrerPolicy: 'no-referrer',
      redirect: 'error',
    })
  } catch {
    throw new Error(`artifact ${artifact}: network`)
  }
  if (!res.ok) {
    const cls = res.status === 403 ? 'expired-or-denied' : `http-${Math.floor(res.status / 100)}xx`
    throw new Error(`artifact ${artifact}: ${cls}`)
  }
  return (await res.json()) as T
}
