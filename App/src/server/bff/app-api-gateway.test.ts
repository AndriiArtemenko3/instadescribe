import { describe, expect, it, vi } from 'vitest'
import { AppApiGateway, normalizedApiOrigin } from './app-api-gateway'

const ASSERTION_SECRET = new Uint8Array(32).fill(5)

function tokens(email = 'editor@example.com', mfaVerified = false) {
  return {
    username: 'cognito-generated-username',
    email,
    mfaVerified,
    accessToken: 'access-secret',
    refreshToken: 'refresh-secret',
    idToken: null,
    accessExpiresAt: '2030-01-01T00:00:00.000Z',
  }
}

function json(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('AppApiGateway', () => {
  it('uses the canonical membership route and accepts only a human exact principal', async () => {
    const serverFetch = vi.fn().mockResolvedValue(json({
      subject: 'human-1',
      email: 'editor@example.com',
      displayName: 'Editor',
      organizationId: 'org-1',
      role: 'editor',
      mfaVerified: false,
    }))
    const gateway = new AppApiGateway('https://api.example', ASSERTION_SECRET, serverFetch)

    await expect(gateway.resolve(tokens())).resolves.toMatchObject({ role: 'editor' })
    const [url, init] = serverFetch.mock.calls[0] as [URL, RequestInit]
    expect(url.href).toBe('https://api.example/api/app/v1/session')
    expect(new Headers(init.headers).get('authorization')).toBe('Bearer access-secret')
    expect(new Headers(init.headers).get('x-instadescribe-browser-assertion')).toMatch(
      /^v1\.\d+\.0\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{43}$/,
    )
    expect(init.credentials).toBe('omit')
    expect(init.redirect).toBe('error')
  })

  it('returns an unverified owner only to the server-side session policy for enrollment', async () => {
    const gateway = new AppApiGateway('https://api.example', ASSERTION_SECRET, vi.fn().mockResolvedValue(json({
      subject: 'owner-1', email: 'owner@example.com', displayName: 'Owner', organizationId: 'org-1',
      role: 'owner', mfaVerified: false,
    })))
    await expect(gateway.resolve(tokens('owner@example.com'))).resolves.toMatchObject({
      role: 'owner',
      mfaVerified: false,
    })
  })

  it('never creates a browser session for a service-account principal', async () => {
    const gateway = new AppApiGateway('https://api.example', ASSERTION_SECRET, vi.fn().mockResolvedValue(json({
      subject: 'service-1', email: 'service@example.com', displayName: 'Automation', organizationId: 'org-1',
      role: 'service', mfaVerified: true,
    })))
    await expect(gateway.resolve(tokens('service@example.com', true))).resolves.toBe('unavailable')
  })

  it('rejects extra project fields instead of passing media metadata through', async () => {
    const gateway = new AppApiGateway('https://api.example', ASSERTION_SECRET, vi.fn().mockResolvedValue(json({
      data: [{
        id: 'p1', orgSlug: 'org', currentJobId: 'j1', name: 'Project', status: 'ready',
        updatedAt: '2030-01-01T00:00:00.000Z', mediaUrl: 'https://storage.example/signed',
      }],
    })))
    await expect(gateway.list({
      principal: {
        subject: 'human-1', email: 'editor@example.com', displayName: 'Editor', organizationId: 'org-1',
        role: 'editor', mfaVerified: false,
      },
      accessToken: 'access-secret',
    })).resolves.toEqual({ kind: 'unavailable' })
  })

  it('requires HTTPS and rejects paths, credentials, and non-loopback HTTP origins', () => {
    expect(normalizedApiOrigin('https://api.example')).toBe('https://api.example')
    expect(normalizedApiOrigin('https://api.example/base')).toBeNull()
    expect(normalizedApiOrigin('https://user:pass@api.example')).toBeNull()
    expect(normalizedApiOrigin('http://api.example', true)).toBeNull()
    expect(normalizedApiOrigin('http://127.0.0.1:8000', true)).toBe('http://127.0.0.1:8000')
  })
})
