import { describe, expect, it, vi } from 'vitest'
import type {
  BffDependencies,
  ProjectGateway,
  SessionGateway,
  SessionPrincipal,
} from './contracts'
import {
  handleChallengePost,
  handleMfaEnrollmentPost,
  handleProjectsGet,
  handleSessionDelete,
  handleSessionGet,
  handleSessionPost,
} from './handlers'
import { CHALLENGE_COOKIE_NAME, CSRF_COOKIE_NAME, SESSION_COOKIE_NAME } from './http'
import { defaultBffDependencies } from './providers'

const principal: SessionPrincipal = {
  subject: 'subject-1',
  email: 'editor@example.com',
  displayName: 'Editorial User',
  organizationId: 'org-1',
  role: 'editor',
  mfaVerified: false,
}

const csrfToken = 'c'.repeat(43)
const opaqueSession = 's'.repeat(43)

function authenticatedSessions(): SessionGateway {
  return {
    lookup: vi.fn().mockResolvedValue({
      kind: 'authenticated',
      principal,
      accessToken: 'provider-access-token',
      expiresAt: new Date(Date.now() + 60_000).toISOString(),
    }),
    signIn: vi.fn().mockResolvedValue({
      kind: 'authenticated',
      principal,
      opaqueSession,
      expiresAt: new Date(Date.now() + 60_000).toISOString(),
    }),
    revoke: vi.fn().mockResolvedValue({ kind: 'revoked' }),
    continueChallenge: vi.fn().mockResolvedValue({ kind: 'unavailable' }),
    inspectChallenge: vi.fn().mockResolvedValue(null),
    forgotPassword: vi.fn().mockResolvedValue({ kind: 'accepted' }),
    resetPassword: vi.fn().mockResolvedValue({ kind: 'reset' }),
    beginMfaEnrollment: vi.fn().mockResolvedValue({ kind: 'unavailable' }),
  }
}

function dependencies(sessions: SessionGateway, projects?: ProjectGateway): BffDependencies {
  return {
    sessions,
    projects: projects ?? { list: vi.fn().mockResolvedValue({ kind: 'ok', projects: [] }) },
  }
}

function request(
  path: string,
  init: RequestInit = {},
  withSession = false,
): Request {
  const headers = new Headers(init.headers)
  if (withSession) headers.set('Cookie', `${SESSION_COOKIE_NAME}=${opaqueSession}; ${CSRF_COOKIE_NAME}=${csrfToken}`)
  return new Request(`https://app.example${path}`, { ...init, headers })
}

describe('App Router JSON BFF', () => {
  it('starts voluntary MFA only with same-origin CSRF and replaces the session with a challenge', async () => {
    const sessions = authenticatedSessions()
    sessions.beginMfaEnrollment = vi.fn().mockResolvedValue({
      kind: 'challenge',
      challenge: { type: 'mfa_setup', totpSecret: 'SERVER-CHALLENGE-SECRET' },
      opaqueChallenge: 'm'.repeat(43),
      expiresAt: new Date(Date.now() + 60_000).toISOString(),
    })
    const response = await handleMfaEnrollmentPost(
      request('/api/bff/auth/mfa/enroll', {
        method: 'POST',
        headers: { Origin: 'https://app.example', 'X-CSRF-Token': csrfToken },
      }, true),
      dependencies(sessions),
    )
    const body = await response.json()

    expect(response.status).toBe(202)
    expect(body).toEqual({ challenge: { type: 'mfa_setup', totpSecret: 'SERVER-CHALLENGE-SECRET' } })
    expect(response.headers.get('set-cookie')).toContain(`${CHALLENGE_COOKIE_NAME}=`)
    expect(response.headers.get('set-cookie')).toContain(`${SESSION_COOKIE_NAME}=;`)
    expect(sessions.beginMfaEnrollment).toHaveBeenCalledWith(opaqueSession)
  })

  it('rejects voluntary MFA without CSRF before touching the session gateway', async () => {
    const sessions = authenticatedSessions()
    const response = await handleMfaEnrollmentPost(
      request('/api/bff/auth/mfa/enroll', {
        method: 'POST',
        headers: { Origin: 'https://app.example' },
      }, true),
      dependencies(sessions),
    )

    expect(response.status).toBe(403)
    expect(sessions.beginMfaEnrollment).not.toHaveBeenCalled()
  })

  it('does not call the project adapter for an unauthenticated request', async () => {
    const projects: ProjectGateway = { list: vi.fn() }
    const response = await handleProjectsGet(
      request('/api/bff/projects'),
      dependencies(authenticatedSessions(), projects),
    )

    expect(response.status).toBe(401)
    expect(projects.list).not.toHaveBeenCalled()
    expect(response.headers.get('cache-control')).toBe('private, no-store')
  })

  it('fails closed when the real session provider is not provisioned', async () => {
    const response = await handleSessionGet(
      request('/api/bff/session', {}, true),
      defaultBffDependencies,
    )

    expect(response.status).toBe(503)
    expect(await response.json()).toEqual({
      error: { code: 'session_provider_unavailable', message: 'Sign-in is temporarily unavailable.' },
    })
  })

  it('returns the human role needed for owner-only account controls without token fields', async () => {
    const response = await handleSessionGet(
      request('/api/bff/session', {}, true),
      dependencies(authenticatedSessions()),
    )
    const body = await response.json()

    expect(response.status).toBe(200)
    expect(body).toEqual({
      user: {
        email: principal.email,
        displayName: principal.displayName,
        organizationId: principal.organizationId,
        role: 'editor',
      },
    })
    expect(JSON.stringify(body)).not.toContain('provider-access-token')
    expect(JSON.stringify(body)).not.toContain('mfaVerified')
  })

  it('returns an exact metadata allowlist and strips media or secret excess properties', async () => {
    const projects: ProjectGateway = {
      list: vi.fn().mockResolvedValue({
        kind: 'ok',
        projects: [{
          id: 'project-1',
          orgSlug: 'studio-one',
          currentJobId: 'job-1',
          name: 'Launch film',
          status: 'ready',
          updatedAt: '2026-08-28T12:00:00.000Z',
          mediaUrl: 'https://storage.example/signed-secret',
          apiKey: 'provider-secret',
        }],
      }),
    }
    const response = await handleProjectsGet(
      request('/api/bff/projects', {}, true),
      dependencies(authenticatedSessions(), projects),
    )
    const body = await response.text()

    expect(response.status).toBe(200)
    expect(body).toContain('Launch film')
    expect(body).not.toContain('signed-secret')
    expect(body).not.toContain('provider-secret')
    expect(body).not.toContain('provider-access-token')
    expect(projects.list).toHaveBeenCalledWith({ principal, accessToken: 'provider-access-token' })
    expect(response.headers.get('content-type')).toContain('application/json')
  })

  it('rejects cross-origin sign-in before credentials reach the adapter', async () => {
    const sessions = authenticatedSessions()
    const response = await handleSessionPost(
      request('/api/bff/session', {
        method: 'POST',
        headers: {
          Origin: 'https://attacker.example',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ email: 'editor@example.com', password: 'secret' }),
      }),
      dependencies(sessions),
    )

    expect(response.status).toBe(403)
    expect(sessions.signIn).not.toHaveBeenCalled()
  })

  it('issues only a secure opaque HttpOnly cookie after adapter success', async () => {
    const response = await handleSessionPost(
      request('/api/bff/session', {
        method: 'POST',
        headers: {
          Origin: 'https://app.example',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ email: 'editor@example.com', password: 'secret' }),
      }),
      dependencies(authenticatedSessions()),
    )
    const body = await response.text()
    const cookie = response.headers.get('set-cookie') ?? ''

    expect(response.status).toBe(200)
    expect(cookie).toContain(`${SESSION_COOKIE_NAME}=${opaqueSession}`)
    expect(cookie).toContain('HttpOnly')
    expect(cookie).toContain('Secure')
    expect(cookie).toContain('SameSite=Lax')
    expect(body).not.toContain(opaqueSession)
    expect(body).not.toContain('secret')
  })

  it('keeps Cognito challenge state behind an opaque HttpOnly challenge cookie', async () => {
    const sessions = authenticatedSessions()
    sessions.signIn = vi.fn().mockResolvedValue({
      kind: 'challenge',
      challenge: { type: 'new_password_required', requiredAttributes: [] },
      opaqueChallenge: 'x'.repeat(43),
      expiresAt: new Date(Date.now() + 60_000).toISOString(),
      providerSession: 'must-never-leak',
    })
    const response = await handleSessionPost(
      request('/api/bff/session', {
        method: 'POST',
        headers: { Origin: 'https://app.example', 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: principal.email, password: 'temporary' }),
      }),
      dependencies(sessions),
    )
    const body = await response.text()

    expect(response.status).toBe(202)
    expect(response.headers.get('set-cookie')).toContain('__Host-instadescribe_auth_challenge=')
    expect(body).toContain('new_password_required')
    expect(body).not.toContain('must-never-leak')
    expect(body).not.toContain('x'.repeat(43))
  })

  it('never issues a session after enrollment and requires reauthentication', async () => {
    const sessions = authenticatedSessions()
    sessions.continueChallenge = vi.fn().mockResolvedValue({ kind: 'reauthentication_required' })
    const response = await handleChallengePost(
      request('/api/bff/auth/challenge', {
        method: 'POST',
        headers: {
          Origin: 'https://app.example',
          'Content-Type': 'application/json',
          Cookie: `${CHALLENGE_COOKIE_NAME}=${'x'.repeat(43)}`,
        },
        body: JSON.stringify({ type: 'mfa_setup', code: '123456' }),
      }),
      dependencies(sessions),
    )
    const body = await response.text()
    const cookies = response.headers.get('set-cookie') ?? ''

    expect(response.status).toBe(200)
    expect(body).toBe('{"reauthenticationRequired":true}')
    expect(cookies).toContain(`${CHALLENGE_COOKIE_NAME}=`)
    expect(cookies).toContain('Max-Age=0')
    expect(cookies).not.toContain(`${SESSION_COOKIE_NAME}=${opaqueSession}`)
    expect(body).not.toContain('access')
    expect(body).not.toContain('secret')
  })

  it('requires both same-origin and double-submit CSRF for logout', async () => {
    const sessions = authenticatedSessions()
    const response = await handleSessionDelete(
      request('/api/bff/session', { method: 'DELETE', headers: { Origin: 'https://app.example' } }, true),
      dependencies(sessions),
    )

    expect(response.status).toBe(403)
    expect(sessions.revoke).not.toHaveBeenCalled()
  })

  it('clears the local cookie but reports unconfirmed server revocation', async () => {
    const response = await handleSessionDelete(
      request('/api/bff/session', {
        method: 'DELETE',
        headers: { Origin: 'https://app.example', 'X-CSRF-Token': csrfToken },
      }, true),
      defaultBffDependencies,
    )

    expect(response.status).toBe(503)
    expect(response.headers.get('set-cookie')).toContain('Max-Age=0')
  })
})
