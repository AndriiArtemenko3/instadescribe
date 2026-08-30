import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  cloudDeliverableHref,
  fetchCloudDeliverables,
  fetchCloudRender,
  fetchCloudReview,
} from './reviewLifecycle'

const jobId = '22222222-2222-4222-8222-222222222222'
const reviewId = '33333333-3333-4333-8333-333333333333'
const renderId = '44444444-4444-4444-8444-444444444444'

function response(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
  })
}

beforeEach(() => vi.stubEnv('NEXT_PUBLIC_APP_ROUTER', '1'))
afterEach(() => {
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
})

describe('review lifecycle client', () => {
  it('parses the review and render contracts through the same-origin BFF', async () => {
    const fetchMock = vi.fn(async (input: string) => input.endsWith('/review')
      ? response({
        id: reviewId,
        object: 'review',
        jobId,
        state: 'open',
        version: 1,
        locked: false,
        sceneCount: 2,
        decidedSceneCount: 1,
        approvedSceneCount: 1,
        rejectedSceneCount: 0,
        zeroAdConfirmed: false,
        lockedAt: null,
        completedAt: null,
        expiresAt: '2026-09-27T10:00:00Z',
        createdAt: '2026-08-28T10:00:00Z',
        updatedAt: '2026-08-28T10:00:00Z',
      })
      : response({
        id: renderId,
        object: 'render',
        jobId,
        reviewId,
        state: 'rendering',
        attemptCount: 1,
        error: null,
        createdAt: '2026-08-28T10:00:00Z',
        updatedAt: '2026-08-28T10:01:00Z',
        startedAt: '2026-08-28T10:01:00Z',
        completedAt: null,
      }))
    vi.stubGlobal('fetch', fetchMock)

    await expect(fetchCloudReview(jobId)).resolves.toMatchObject({ state: 'open', locked: false })
    await expect(fetchCloudRender(jobId)).resolves.toMatchObject({ state: 'rendering', attemptCount: 1 })
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/bff/cloud/jobs/${jobId}/review`,
      expect.objectContaining({ credentials: 'same-origin', redirect: 'error' }),
    )
  })

  it('requires one complete five-format set and exposes only a BFF download path', async () => {
    const kinds = ['mp4', 'mp3', 'srt', 'csv', 'docx'] as const
    vi.stubGlobal('fetch', vi.fn(async () => response({
      completedSet: true,
      items: kinds.map((kind, index) => ({
        id: `${index + 1}1111111-1111-4111-8111-111111111111`,
        jobId,
        kind,
        fileName: `description.${kind}`,
        contentType: 'application/octet-stream',
        byteSize: index,
        sha256: 'a'.repeat(64),
        createdAt: '2026-08-28T10:00:00Z',
      })),
    })))

    const set = await fetchCloudDeliverables(jobId)
    expect(set.items.map((item) => item.kind)).toEqual(kinds)
    expect(cloudDeliverableHref(set.items[0].id)).toBe(
      `/api/bff/cloud/deliverables/${set.items[0].id}/content`,
    )
  })

  it('fails closed on partial or malformed lifecycle responses', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response({ completedSet: true, items: [] })))
    await expect(fetchCloudDeliverables(jobId)).rejects.toMatchObject({ category: 'service' })
  })
})
