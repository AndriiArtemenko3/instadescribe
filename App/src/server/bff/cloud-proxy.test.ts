import { afterEach, describe, expect, it, vi } from 'vitest'
import type { BffDependencies, SessionGateway } from './contracts'
import { handleCloudProxy } from './cloud-proxy'
import { CSRF_COOKIE_NAME, SESSION_COOKIE_NAME } from './http'

const JOB_ID = '11111111-1111-4111-8111-111111111111'
const PREVIEW_ID = '22222222-2222-4222-8222-222222222222'
const SESSION = 's'.repeat(43)
const CSRF = 'c'.repeat(43)
const ASSERTION_SECRET = new Uint8Array(32).fill(5)

function sessions(): SessionGateway {
  return {
    lookup: vi.fn().mockResolvedValue({
      kind: 'authenticated',
      principal: {
        subject: 'human-1',
        email: 'editor@example.com',
        displayName: 'Editor',
        organizationId: 'org-1',
        role: 'editor',
        mfaVerified: false,
      },
      accessToken: 'cognito-access-secret',
      expiresAt: '2030-01-01T00:00:00Z',
    }),
    signIn: vi.fn().mockResolvedValue({ kind: 'unavailable' }),
    continueChallenge: vi.fn().mockResolvedValue({ kind: 'unavailable' }),
    inspectChallenge: vi.fn().mockResolvedValue(null),
    forgotPassword: vi.fn().mockResolvedValue({ kind: 'unavailable' }),
    resetPassword: vi.fn().mockResolvedValue({ kind: 'unavailable' }),
    beginMfaEnrollment: vi.fn().mockResolvedValue({ kind: 'unavailable' }),
    revoke: vi.fn().mockResolvedValue({ kind: 'unavailable' }),
  }
}

function dependencies(gateway = sessions()): BffDependencies {
  return { sessions: gateway, projects: { list: vi.fn().mockResolvedValue({ kind: 'ok', projects: [] }) } }
}

function request(path: string, init: RequestInit = {}, authenticated = true): Request {
  const headers = new Headers(init.headers)
  if (authenticated) {
    headers.set('Cookie', `${SESSION_COOKIE_NAME}=${SESSION}; ${CSRF_COOKIE_NAME}=${CSRF}`)
  }
  return new Request(`https://app.example/api/bff/cloud/${path}`, { ...init, headers })
}

afterEach(() => vi.unstubAllEnvs())

describe('authenticated cloud BFF relay', () => {
  it('rejects non-allowlisted paths before resolving a session', async () => {
    const gateway = sessions()
    const upstream = vi.fn()
    const response = await handleCloudProxy(
      request(`jobs/${JOB_ID}/admin`),
      ['jobs', JOB_ID, 'admin'],
      dependencies(gateway),
      'https://api.example',
      ASSERTION_SECRET,
      upstream,
    )
    expect(response.status).toBe(404)
    expect(gateway.lookup).not.toHaveBeenCalled()
    expect(upstream).not.toHaveBeenCalled()
  })

  it('does not call FastAPI without the opaque browser session', async () => {
    const upstream = vi.fn()
    const response = await handleCloudProxy(
      request(`jobs/${JOB_ID}/manifest`, {}, false),
      ['jobs', JOB_ID, 'manifest'],
      dependencies(),
      'https://api.example',
      ASSERTION_SECRET,
      upstream,
    )
    expect(response.status).toBe(401)
    expect(upstream).not.toHaveBeenCalled()
  })

  it('forwards a read only to the exact App API with the server-held Cognito token', async () => {
    const upstream = vi.fn().mockResolvedValue(new Response(JSON.stringify({ jobId: JOB_ID }), {
      headers: { 'Content-Type': 'application/json', 'X-Request-Id': 'request-1' },
    }))
    const response = await handleCloudProxy(
      request(`jobs/${JOB_ID}/manifest`),
      ['jobs', JOB_ID, 'manifest'],
      dependencies(),
      'https://api.example',
      ASSERTION_SECRET,
      upstream,
    )
    const [url, init] = upstream.mock.calls[0] as [URL, RequestInit]
    expect(url.href).toBe(`https://api.example/api/app/v1/jobs/${JOB_ID}/manifest`)
    expect(new Headers(init.headers).get('authorization')).toBe('Bearer cognito-access-secret')
    expect(new Headers(init.headers).get('x-instadescribe-browser-assertion')).toMatch(
      /^v1\.\d+\.0\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{43}$/,
    )
    expect(new Headers(init.headers).has('cookie')).toBe(false)
    expect(init.redirect).toBe('manual')
    expect(await response.json()).toEqual({ jobId: JOB_ID })
    expect(response.headers.get('x-request-id')).toBe('request-1')
  })

  it('requires fixed Origin plus double-submit CSRF before a mutation', async () => {
    vi.stubEnv('NODE_ENV', 'production')
    vi.stubEnv('APP_ORIGIN', 'https://app.example')
    const upstream = vi.fn()
    const response = await handleCloudProxy(
      request(`jobs/${JOB_ID}/cancel`, {
        method: 'POST',
        headers: { Origin: 'https://app.example', 'Content-Type': 'application/json' },
        body: '{}',
      }),
      ['jobs', JOB_ID, 'cancel'],
      dependencies(),
      'https://api.example',
      ASSERTION_SECRET,
      upstream,
    )
    expect(response.status).toBe(403)
    expect(upstream).not.toHaveBeenCalled()
  })

  it('allows only the exact owner invitation path through the mutation boundary', async () => {
    vi.stubEnv('NODE_ENV', 'production')
    vi.stubEnv('APP_ORIGIN', 'https://app.example')
    const upstream = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      invitationId: '33333333-3333-4333-8333-333333333333',
      email: 'member@example.com',
      role: 'viewer',
      state: 'active',
    }), { status: 201, headers: { 'Content-Type': 'application/json' } }))
    const response = await handleCloudProxy(
      request('organization/invitations', {
        method: 'POST',
        headers: {
          Origin: 'https://app.example',
          'Content-Type': 'application/json',
          'X-CSRF-Token': CSRF,
          'Idempotency-Key': 'invite-1',
        },
        body: JSON.stringify({ email: 'member@example.com', role: 'viewer' }),
      }),
      ['organization', 'invitations'],
      dependencies(),
      'https://api.example',
      ASSERTION_SECRET,
      upstream,
    )
    expect(response.status).toBe(201)
    const [url, init] = upstream.mock.calls[0] as [URL, RequestInit]
    expect(url.pathname).toBe('/api/app/v1/organization/invitations')
    const headers = new Headers(init.headers)
    expect(headers.get('idempotency-key')).toBe('invite-1')
    expect(headers.get('x-csrf-token')).toBeNull()
  })

  it('relays bounded JSON writes and preserves an authenticated 303 without fetching storage', async () => {
    vi.stubEnv('NODE_ENV', 'production')
    vi.stubEnv('APP_ORIGIN', 'https://app.example')
    const writeFetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ state: 'cancelled' }), {
      headers: { 'Content-Type': 'application/json' },
    }))
    const write = await handleCloudProxy(
      request(`jobs/${JOB_ID}/cancel`, {
        method: 'POST',
        headers: {
          Origin: 'https://app.example',
          'Content-Type': 'application/json',
          'X-CSRF-Token': CSRF,
          'Idempotency-Key': 'cancel-1',
        },
        body: '{}',
      }),
      ['jobs', JOB_ID, 'cancel'],
      dependencies(),
      'https://api.example',
      ASSERTION_SECRET,
      writeFetch,
    )
    expect(write.status).toBe(200)
    const writeHeaders = new Headers((writeFetch.mock.calls[0] as [URL, RequestInit])[1].headers)
    expect(writeHeaders.get('idempotency-key')).toBe('cancel-1')
    expect(writeHeaders.get('x-csrf-token')).toBeNull()

    const location = 'https://storage.example/described.mp4?signature=secret'
    const redirectFetch = vi.fn().mockResolvedValue(new Response(null, {
      status: 303,
      headers: { Location: location },
    }))
    const redirect = await handleCloudProxy(
      request(`deliverables/${JOB_ID}/content`),
      ['deliverables', JOB_ID, 'content'],
      dependencies(),
      'https://api.example',
      ASSERTION_SECRET,
      redirectFetch,
    )
    expect(redirect.status).toBe(303)
    expect(redirect.headers.get('location')).toBe(location)
    expect(redirectFetch).toHaveBeenCalledTimes(1)
  })

  it('allows only the exact per-scene preview request, status and content routes', async () => {
    vi.stubEnv('NODE_ENV', 'production')
    vi.stubEnv('APP_ORIGIN', 'https://app.example')
    const upstream = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ previewId: PREVIEW_ID }), {
        status: 202,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ previewId: PREVIEW_ID }), {
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(null, {
        status: 303,
        headers: { Location: 'https://storage.example/preview.mp3?version=7' },
      }))

    const create = await handleCloudProxy(
      request(`jobs/${JOB_ID}/scenes/scene_7/tts-previews`, {
        method: 'POST',
        headers: {
          Origin: 'https://app.example',
          'Content-Type': 'application/json',
          'X-CSRF-Token': CSRF,
          'Idempotency-Key': 'preview-1',
        },
        body: JSON.stringify({ text: 'A door opens.', voice: 'nova', speed: 1 }),
      }),
      ['jobs', JOB_ID, 'scenes', 'scene_7', 'tts-previews'],
      dependencies(),
      'https://api.example',
      ASSERTION_SECRET,
      upstream,
    )
    const status = await handleCloudProxy(
      request(`tts-previews/${PREVIEW_ID}`),
      ['tts-previews', PREVIEW_ID],
      dependencies(),
      'https://api.example',
      ASSERTION_SECRET,
      upstream,
    )
    const content = await handleCloudProxy(
      request(`tts-previews/${PREVIEW_ID}/content`),
      ['tts-previews', PREVIEW_ID, 'content'],
      dependencies(),
      'https://api.example',
      ASSERTION_SECRET,
      upstream,
    )

    expect(create.status).toBe(202)
    expect(status.status).toBe(200)
    expect(content.status).toBe(303)
    expect(content.headers.get('location')).toBe('https://storage.example/preview.mp3?version=7')
    expect(upstream.mock.calls.map(([url]) => (url as URL).pathname)).toEqual([
      `/api/app/v1/jobs/${JOB_ID}/scenes/scene_7/tts-previews`,
      `/api/app/v1/tts-previews/${PREVIEW_ID}`,
      `/api/app/v1/tts-previews/${PREVIEW_ID}/content`,
    ])
    const createHeaders = new Headers((upstream.mock.calls[0] as [URL, RequestInit])[1].headers)
    expect(createHeaders.get('idempotency-key')).toBe('preview-1')
    expect(createHeaders.get('x-portfolio-token')).toBeNull()

    const invalid = await handleCloudProxy(
      request(`jobs/${JOB_ID}/scenes/scene_alpha/tts-previews`, {
        method: 'POST',
        headers: {
          Origin: 'https://app.example',
          'Content-Type': 'application/json',
          'X-CSRF-Token': CSRF,
        },
        body: '{}',
      }),
      ['jobs', JOB_ID, 'scenes', 'scene_alpha', 'tts-previews'],
      dependencies(),
      'https://api.example',
      ASSERTION_SECRET,
      upstream,
    )
    expect(invalid.status).toBe(404)
    expect(upstream).toHaveBeenCalledTimes(3)
  })
})
