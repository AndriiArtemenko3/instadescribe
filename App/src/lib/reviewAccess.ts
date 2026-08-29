export interface ReviewAccessIdentity {
  orgSlug: string
  projectId: string
  jobId: string
}

export interface ReviewAccessProject {
  id: string
  orgSlug: string
  currentJobId: string | null
  name: string
  updatedAt: string
}

export type BrowserRole = 'owner' | 'editor' | 'reviewer' | 'viewer'

export type ReviewAccessResult =
  | { kind: 'authorized'; project: ReviewAccessProject; role: BrowserRole }
  | { kind: 'signed_out' | 'not_found' | 'unavailable' }

type ReviewFetch = (input: string, init: RequestInit) => Promise<Response>

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function projectIdentity(value: unknown): ReviewAccessProject | null {
  const item = record(value)
  if (
    !item ||
    typeof item.id !== 'string' ||
    typeof item.orgSlug !== 'string' ||
    !(item.currentJobId === null || typeof item.currentJobId === 'string') ||
    typeof item.name !== 'string' ||
    typeof item.updatedAt !== 'string'
  ) return null
  return {
    id: item.id,
    orgSlug: item.orgSlug,
    currentJobId: item.currentJobId,
    name: item.name,
    updatedAt: item.updatedAt,
  }
}

function sessionRole(value: unknown): BrowserRole | null {
  const body = record(value)
  const user = record(body?.user)
  if (!user || typeof user.role !== 'string') return null
  return ['owner', 'editor', 'reviewer', 'viewer'].includes(user.role)
    ? user.role as BrowserRole
    : null
}

/** Authorize an immutable job version, not merely a project's newest job. */
export async function loadReviewAccess(
  identity: ReviewAccessIdentity,
  signal: AbortSignal,
  reviewFetch: ReviewFetch = fetch,
): Promise<ReviewAccessResult> {
  let projectResponse: Response
  let jobResponse: Response
  let sessionResponse: Response
  try {
    [projectResponse, jobResponse, sessionResponse] = await Promise.all([
      reviewFetch('/api/bff/projects', {
        credentials: 'same-origin',
        cache: 'no-store',
        signal,
      }),
      reviewFetch(`/api/bff/cloud/jobs/${encodeURIComponent(identity.jobId)}`, {
        credentials: 'same-origin',
        cache: 'no-store',
        signal,
      }),
      reviewFetch('/api/bff/session', {
        credentials: 'same-origin',
        cache: 'no-store',
        signal,
      }),
    ])
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    return { kind: 'unavailable' }
  }

  if (
    projectResponse.status === 401 || jobResponse.status === 401 ||
    sessionResponse.status === 401
  ) return { kind: 'signed_out' }
  if (jobResponse.status === 404) return { kind: 'not_found' }
  if (!projectResponse.ok || !jobResponse.ok || !sessionResponse.ok) return { kind: 'unavailable' }

  let projectsBody: unknown
  let jobBody: unknown
  let sessionBody: unknown
  try {
    [projectsBody, jobBody, sessionBody] = await Promise.all([
      projectResponse.json(),
      jobResponse.json(),
      sessionResponse.json(),
    ])
  } catch {
    return { kind: 'unavailable' }
  }
  const projectsRecord = record(projectsBody)
  const projects = Array.isArray(projectsRecord?.projects)
    ? projectsRecord.projects.map(projectIdentity)
    : []
  if (projects.some((project) => project === null)) return { kind: 'unavailable' }

  const job = record(jobBody)
  if (
    !job ||
    job.id !== identity.jobId ||
    job.projectId !== identity.projectId
  ) return { kind: 'not_found' }
  const role = sessionRole(sessionBody)
  if (!role) return { kind: 'unavailable' }

  const project = projects.find((candidate) => (
    candidate?.id === identity.projectId && candidate.orgSlug === identity.orgSlug
  ))
  return project ? { kind: 'authorized', project, role } : { kind: 'not_found' }
}
