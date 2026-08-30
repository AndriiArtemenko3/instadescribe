import { describe, expect, it, vi } from 'vitest'
import { loadReviewAccess } from './reviewAccess'

const identity = {
  orgSlug: 'biology',
  projectId: '11111111-1111-4111-8111-111111111111',
  jobId: '22222222-2222-4222-8222-222222222222',
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('loadReviewAccess', () => {
  it('authorizes a tenant-scoped non-current job version', async () => {
    const reviewFetch = vi.fn(async (url: string) => {
      if (url === '/api/bff/projects') return json(200, { projects: [{
        id: identity.projectId,
        orgSlug: identity.orgSlug,
        currentJobId: '33333333-3333-4333-8333-333333333333',
        name: 'BIO101',
        updatedAt: '2026-08-28T10:00:00Z',
      }] })
      if (url === '/api/bff/session') return json(200, { user: { role: 'reviewer' } })
      return json(200, { id: identity.jobId, projectId: identity.projectId })
    })

    const result = await loadReviewAccess(identity, new AbortController().signal, reviewFetch)

    expect(result).toMatchObject({ kind: 'authorized', role: 'reviewer' })
    expect(reviewFetch).toHaveBeenCalledWith(
      `/api/bff/cloud/jobs/${identity.jobId}`,
      expect.objectContaining({ credentials: 'same-origin', cache: 'no-store' }),
    )
  })

  it('rejects a job whose project does not match the route', async () => {
    const reviewFetch = vi.fn(async (url: string) => {
      if (url === '/api/bff/projects') return json(200, { projects: [{
        id: identity.projectId,
        orgSlug: identity.orgSlug,
        currentJobId: identity.jobId,
        name: 'BIO101',
        updatedAt: '2026-08-28T10:00:00Z',
      }] })
      if (url === '/api/bff/session') return json(200, { user: { role: 'editor' } })
      return json(200, {
        id: identity.jobId,
        projectId: '44444444-4444-4444-8444-444444444444',
      })
    })

    await expect(loadReviewAccess(identity, new AbortController().signal, reviewFetch))
      .resolves.toEqual({ kind: 'not_found' })
  })

  it('maps authentication, missing jobs, and malformed data fail closed', async () => {
    const cases: Array<[number, unknown, string]> = [
      [401, {}, 'signed_out'],
      [404, {}, 'not_found'],
      [200, { id: identity.jobId }, 'not_found'],
    ]
    for (const [status, body, kind] of cases) {
      const reviewFetch = vi.fn(async (url: string) => {
        if (url === '/api/bff/projects') return json(200, { projects: [] })
        if (url === '/api/bff/session') return json(200, { user: { role: 'viewer' } })
        return json(status, body)
      })
      await expect(loadReviewAccess(identity, new AbortController().signal, reviewFetch))
        .resolves.toEqual({ kind })
    }
  })

  it('fails closed when the browser role is missing or unknown', async () => {
    const reviewFetch = vi.fn(async (url: string) => {
      if (url === '/api/bff/projects') return json(200, { projects: [{
        id: identity.projectId,
        orgSlug: identity.orgSlug,
        currentJobId: identity.jobId,
        name: 'BIO101',
        updatedAt: '2026-08-28T10:00:00Z',
      }] })
      if (url === '/api/bff/session') return json(200, { user: { role: 'service' } })
      return json(200, { id: identity.jobId, projectId: identity.projectId })
    })

    await expect(loadReviewAccess(identity, new AbortController().signal, reviewFetch))
      .resolves.toEqual({ kind: 'unavailable' })
  })
})
