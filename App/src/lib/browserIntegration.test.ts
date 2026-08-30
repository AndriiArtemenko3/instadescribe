// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  BrowserIntegrationError,
  browserCsrfToken,
  completeBrowserUpload,
  createBrowserJob,
  inviteOrganizationMember,
  uploadBrowserFile,
} from './browserIntegration'

const JOB_ID = '11111111-1111-4111-8111-111111111111'
const PROJECT_ID = '22222222-2222-4222-8222-222222222222'
const CSRF = 'c'.repeat(43)

function json(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
}

afterEach(() => vi.unstubAllGlobals())

describe('browser integration transfers', () => {
  it('reads only the exact readable CSRF cookie', () => {
    expect(browserCsrfToken(`other=x; __Host-instadescribe_csrf=${CSRF}`)).toBe(CSRF)
    expect(() => browserCsrfToken('__Host-instadescribe_csrf=short')).toThrowError(BrowserIntegrationError)
  })

  it('creates through the same-origin BFF without any service key header', async () => {
    Object.defineProperty(document, 'cookie', { configurable: true, value: `__Host-instadescribe_csrf=${CSRF}` })
    const fetchMock = vi.fn().mockResolvedValue(json({
      job: { id: JOB_ID, projectId: PROJECT_ID, state: 'awaiting_upload' },
      uploads: {
        video: {
          method: 'POST',
          url: 'https://storage.example/upload?signature=secret',
          fields: { policy: 'signed-policy' },
          expiresAt: '2030-01-01T00:00:00Z',
        },
      },
    }, 201))
    vi.stubGlobal('fetch', fetchMock)
    const created = await createBrowserJob({ project: { name: 'Lecture' } })
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    const headers = new Headers(init.headers)
    expect(url).toBe('/api/bff/cloud/jobs')
    expect(headers.get('x-csrf-token')).toBe(CSRF)
    expect(headers.has('authorization')).toBe(false)
    expect(headers.has('x-portfolio-token')).toBe(false)
    expect(created.job.id).toBe(JOB_ID)
  })

  it('invites through the CSRF-protected BFF without browser credentials', async () => {
    Object.defineProperty(document, 'cookie', { configurable: true, value: `__Host-instadescribe_csrf=${CSRF}` })
    const invitationId = '33333333-3333-4333-8333-333333333333'
    const fetchMock = vi.fn().mockResolvedValue(json({
      invitationId,
      email: 'member@example.com',
      role: 'reviewer',
      state: 'active',
    }, 201))
    vi.stubGlobal('fetch', fetchMock)

    const invited = await inviteOrganizationMember('member@example.com', 'reviewer')
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    const headers = new Headers(init.headers)
    expect(url).toBe('/api/bff/cloud/organization/invitations')
    expect(headers.get('x-csrf-token')).toBe(CSRF)
    expect(headers.get('idempotency-key')).toMatch(/^[0-9a-f-]{36}$/)
    expect(headers.has('authorization')).toBe(false)
    expect(headers.has('x-portfolio-token')).toBe(false)
    expect(invited).toEqual({ invitationId, email: 'member@example.com', role: 'reviewer', state: 'active' })
  })

  it('posts signed fields and the file directly to storage without browser credentials', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchMock)
    const file = new File(['video'], 'lecture.mp4', { type: 'video/mp4' })
    await uploadBrowserFile({
      method: 'POST',
      url: 'https://storage.example/upload',
      fields: { key: 'org/job/source', policy: 'signed-policy' },
      expiresAt: '2030-01-01T00:00:00Z',
    }, file)
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('https://storage.example/upload')
    expect(init.credentials).toBe('omit')
    expect(init.redirect).toBe('error')
    const form = init.body as FormData
    expect([...form.keys()]).toEqual(['key', 'policy', 'file'])
    expect(new Headers(init.headers).has('authorization')).toBe(false)
  })

  it('keeps the reserved job ID when upload confirmation fails', async () => {
    Object.defineProperty(document, 'cookie', { configurable: true, value: `__Host-instadescribe_csrf=${CSRF}` })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ code: 'capacity_conflict' }, 409)))
    await expect(completeBrowserUpload(JOB_ID)).rejects.toMatchObject({
      code: 'capacity_conflict',
      jobId: JOB_ID,
    })
  })
})
