import { afterEach, describe, expect, it, vi } from 'vitest'
import type { BffDependencies, SessionGateway } from './contracts'
import { handleCloudProxy } from './cloud-proxy'
import { CSRF_COOKIE_NAME, SESSION_COOKIE_NAME } from './http'

const JOB_ID = '11111111-1111-4111-8111-111111111111'
const PREVIEW_ID = '22222222-2222-4222-8222-222222222222'
const INVESTIGATION_ID = '33333333-3333-4333-8333-333333333333'
const STEP_ID = '44444444-4444-4444-8444-444444444444'
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
    const writeInit = (writeFetch.mock.calls[0] as [URL, RequestInit])[1]
    const writeHeaders = new Headers(writeInit.headers)
    expect(writeHeaders.get('idempotency-key')).toBe('cancel-1')
    expect(writeHeaders.get('x-csrf-token')).toBeNull()
    expect(new TextDecoder().decode(writeInit.body as ArrayBuffer)).toBe('{}')

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

  it('cancels an undeclared streaming body as soon as it exceeds 64 KiB', async () => {
    vi.stubEnv('NODE_ENV', 'production')
    vi.stubEnv('APP_ORIGIN', 'https://app.example')
    let pulls = 0
    let cancelled = false
    const stream = new ReadableStream<Uint8Array>({
      pull(controller) {
        pulls += 1
        controller.enqueue(new Uint8Array(8 * 1024))
      },
      cancel() {
        cancelled = true
      },
    })
    const headers = new Headers({
      Cookie: `${SESSION_COOKIE_NAME}=${SESSION}; ${CSRF_COOKIE_NAME}=${CSRF}`,
      Origin: 'https://app.example',
      'Content-Type': 'application/json',
      'X-CSRF-Token': CSRF,
    })
    const streamedRequest = new Request(`https://app.example/api/bff/cloud/jobs/${JOB_ID}/cancel`, {
      method: 'POST',
      headers,
      body: stream,
      duplex: 'half',
    } as RequestInit & { duplex: 'half' })
    const upstream = vi.fn()

    const response = await handleCloudProxy(
      streamedRequest,
      ['jobs', JOB_ID, 'cancel'],
      dependencies(),
      'https://api.example',
      ASSERTION_SECRET,
      upstream,
    )

    expect(response.status).toBe(413)
    expect(cancelled).toBe(true)
    expect(pulls).toBeLessThanOrEqual(10)
    expect(upstream).not.toHaveBeenCalled()
  })

  it('does not trust a forged low Content-Length for a streaming body', async () => {
    vi.stubEnv('NODE_ENV', 'production')
    vi.stubEnv('APP_ORIGIN', 'https://app.example')
    let cancelled = false
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array((64 * 1024) + 1))
      },
      cancel() {
        cancelled = true
      },
    })
    const headers = new Headers({
      Cookie: `${SESSION_COOKIE_NAME}=${SESSION}; ${CSRF_COOKIE_NAME}=${CSRF}`,
      Origin: 'https://app.example',
      'Content-Type': 'application/json',
      'Content-Length': '1',
      'X-CSRF-Token': CSRF,
    })
    const streamedRequest = new Request(`https://app.example/api/bff/cloud/jobs/${JOB_ID}/cancel`, {
      method: 'POST',
      headers,
      body: stream,
      duplex: 'half',
    } as RequestInit & { duplex: 'half' })
    const upstream = vi.fn()

    const response = await handleCloudProxy(
      streamedRequest,
      ['jobs', JOB_ID, 'cancel'],
      dependencies(),
      'https://api.example',
      ASSERTION_SECRET,
      upstream,
    )

    expect(response.status).toBe(413)
    expect(cancelled).toBe(true)
    expect(upstream).not.toHaveBeenCalled()
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

  it('allows exact investigation reads and rejects every unimplemented nested route', async () => {
    const upstream = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ data: [] }), {
      headers: { 'Content-Type': 'application/json' },
    }))
    const readPaths = [
      ['investigations'],
      ...['', '/steps', '/keyframes', '/evidence', '/beliefs', '/report']
        .map((suffix) => ['investigations', INVESTIGATION_ID, ...suffix.split('/').filter(Boolean)]),
    ]
    for (const segments of readPaths) {
      const response = await handleCloudProxy(
        request(segments.join('/')),
        segments,
        dependencies(),
        'https://api.example',
        ASSERTION_SECRET,
        upstream,
      )
      expect(response.status).toBe(200)
    }

    const rejected: Array<{ method: string; segments: string[] }> = [
      { method: 'GET', segments: ['investigations', INVESTIGATION_ID, 'raw-model-output'] },
      { method: 'POST', segments: ['investigations', INVESTIGATION_ID, 'egress', STEP_ID, 'decision'] },
      { method: 'POST', segments: ['investigations', 'not-a-uuid', 'cancel'] },
      { method: 'PATCH', segments: ['investigations', INVESTIGATION_ID] },
    ]
    for (const { method, segments } of rejected) {
      const response = await handleCloudProxy(
        request(segments.join('/'), { method }),
        segments,
        dependencies(),
        'https://api.example',
        ASSERTION_SECRET,
        upstream,
      )
      expect(response.status).toBe(404)
    }
    expect(upstream).toHaveBeenCalledTimes(7)
  })

  it('keeps the three implemented investigation writes behind exact CSRF-protected routes', async () => {
    vi.stubEnv('NODE_ENV', 'production')
    vi.stubEnv('APP_ORIGIN', 'https://app.example')
    const upstream = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ ok: true }), {
      headers: { 'Content-Type': 'application/json' },
    }))
    const mutationHeaders = {
      Origin: 'https://app.example',
      'Content-Type': 'application/json',
      'X-CSRF-Token': CSRF,
      'Idempotency-Key': 'investigation-action-1',
    }
    const paths = [
      'investigations',
      `investigations/${INVESTIGATION_ID}/decision`,
      `investigations/${INVESTIGATION_ID}/cancel`,
    ]
    for (const path of paths) {
      const response = await handleCloudProxy(
        request(path, { method: 'POST', headers: mutationHeaders, body: '{}' }),
        path.split('/'),
        dependencies(),
        'https://api.example',
        ASSERTION_SECRET,
        upstream,
      )
      expect(response.status).toBe(200)
    }

    const noCsrf = await handleCloudProxy(
      request(`investigations/${INVESTIGATION_ID}/decision`, {
        method: 'POST',
        headers: { Origin: 'https://app.example', 'Content-Type': 'application/json' },
        body: '{}',
      }),
      ['investigations', INVESTIGATION_ID, 'decision'],
      dependencies(),
      'https://api.example',
      ASSERTION_SECRET,
      upstream,
    )
    expect(noCsrf.status).toBe(403)
    expect(upstream).toHaveBeenCalledTimes(3)
  })
})
