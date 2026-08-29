import { randomBytes, timingSafeEqual } from 'node:crypto'
import type { AuthenticatedSession, SessionGateway } from './contracts'

export const SESSION_COOKIE_NAME = '__Host-instadescribe_session'
export const CHALLENGE_COOKIE_NAME = '__Host-instadescribe_auth_challenge'
export const CSRF_COOKIE_NAME = '__Host-instadescribe_csrf'
export const CSRF_HEADER_NAME = 'x-csrf-token'

const JSON_HEADERS = {
  'Cache-Control': 'private, no-store',
  'Content-Type': 'application/json; charset=utf-8',
  'X-Content-Type-Options': 'nosniff',
  Vary: 'Cookie',
}

export function jsonResponse(
  status: number,
  body: unknown,
  extraHeaders?: HeadersInit,
  cookies: readonly string[] = [],
): Response {
  const headers = new Headers(JSON_HEADERS)
  if (extraHeaders) {
    new Headers(extraHeaders).forEach((value, key) => headers.set(key, value))
  }
  cookies.forEach((cookie) => headers.append('Set-Cookie', cookie))
  return new Response(JSON.stringify(body), { status, headers })
}

export function sameOrigin(request: Request): boolean {
  const origin = request.headers.get('origin')
  if (!origin) return false
  try {
    const configured = process.env.APP_ORIGIN
    if (configured) {
      const expected = new URL(configured)
      const loopback = expected.hostname === 'localhost' || expected.hostname === '127.0.0.1' || expected.hostname === '::1'
      const allowedProtocol = expected.protocol === 'https:' ||
        (process.env.NODE_ENV !== 'production' && expected.protocol === 'http:' && loopback)
      if (
        !allowedProtocol || expected.username || expected.password || expected.search || expected.hash ||
        (expected.pathname !== '/' && expected.pathname !== '')
      ) return false
      return new URL(origin).origin === expected.origin
    }
    // Production never trusts a request-derived Host as the CSRF authority.
    if (process.env.NODE_ENV === 'production') return false
    return new URL(origin).origin === new URL(request.url).origin
  } catch {
    return false
  }
}

function readCookie(request: Request, expectedName: string, maximum = 4096): string | null {
  const cookieHeader = request.headers.get('cookie')
  if (!cookieHeader) return null

  for (const pair of cookieHeader.split(';')) {
    const separator = pair.indexOf('=')
    if (separator < 0) continue
    const name = pair.slice(0, separator).trim()
    if (name !== expectedName) continue
    const encoded = pair.slice(separator + 1).trim()
    if (!encoded || encoded.length > maximum) return null
    try {
      return decodeURIComponent(encoded)
    } catch {
      return null
    }
  }
  return null
}

export function readSessionCookie(request: Request): string | null {
  return readCookie(request, SESSION_COOKIE_NAME)
}

export function readChallengeCookie(request: Request): string | null {
  return readCookie(request, CHALLENGE_COOKIE_NAME)
}

export function readCsrfCookie(request: Request): string | null {
  return readCookie(request, CSRF_COOKIE_NAME, 256)
}

export function clearSessionCookie(): string {
  return `${SESSION_COOKIE_NAME}=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0`
}

export function clearChallengeCookie(): string {
  return `${CHALLENGE_COOKIE_NAME}=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0`
}

export function clearCsrfCookie(): string {
  return `${CSRF_COOKIE_NAME}=; Path=/; Secure; SameSite=Lax; Max-Age=0`
}

export function issueSessionCookie(opaqueSession: string, expiresAt: string): string | null {
  if (!/^[A-Za-z0-9_-]{43}$/.test(opaqueSession)) return null
  const expiresMs = Date.parse(expiresAt)
  if (!Number.isFinite(expiresMs) || expiresMs <= Date.now()) return null
  const maxAge = Math.max(1, Math.floor((expiresMs - Date.now()) / 1000))
  return `${SESSION_COOKIE_NAME}=${encodeURIComponent(opaqueSession)}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${maxAge}`
}

export function issueChallengeCookie(opaqueChallenge: string, expiresAt: string): string | null {
  if (!/^[A-Za-z0-9_-]{43}$/.test(opaqueChallenge)) return null
  const expiresMs = Date.parse(expiresAt)
  if (!Number.isFinite(expiresMs) || expiresMs <= Date.now()) return null
  const maxAge = Math.max(1, Math.floor((expiresMs - Date.now()) / 1000))
  return `${CHALLENGE_COOKIE_NAME}=${encodeURIComponent(opaqueChallenge)}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${maxAge}`
}

export function generateCsrfToken(): string {
  return randomBytes(32).toString('base64url')
}

export function issueCsrfCookie(token: string, expiresAt: string): string | null {
  if (!/^[A-Za-z0-9_-]{43}$/.test(token)) return null
  const expiresMs = Date.parse(expiresAt)
  if (!Number.isFinite(expiresMs) || expiresMs <= Date.now()) return null
  const maxAge = Math.max(1, Math.floor((expiresMs - Date.now()) / 1000))
  return `${CSRF_COOKIE_NAME}=${encodeURIComponent(token)}; Path=/; Secure; SameSite=Lax; Max-Age=${maxAge}`
}

export function validMutationCsrf(request: Request): boolean {
  const cookie = readCsrfCookie(request)
  const header = request.headers.get(CSRF_HEADER_NAME)
  if (!cookie || !header || !/^[A-Za-z0-9_-]{43}$/.test(cookie) || !/^[A-Za-z0-9_-]{43}$/.test(header)) {
    return false
  }
  const cookieBytes = Buffer.from(cookie)
  const headerBytes = Buffer.from(header)
  return cookieBytes.byteLength === headerBytes.byteLength && timingSafeEqual(cookieBytes, headerBytes)
}

export type PrincipalResolution =
  | ({ kind: 'authenticated'; expiresAt: string } & AuthenticatedSession)
  | { kind: 'anonymous'; clearCookie: boolean }
  | { kind: 'unavailable' }

export async function resolvePrincipal(
  request: Request,
  gateway: SessionGateway,
): Promise<PrincipalResolution> {
  const opaqueSession = readSessionCookie(request)
  if (!opaqueSession) return { kind: 'anonymous', clearCookie: false }

  try {
    const lookup = await gateway.lookup(opaqueSession)
    if (lookup.kind === 'authenticated') return lookup
    if (lookup.kind === 'invalid') return { kind: 'anonymous', clearCookie: true }
    return { kind: 'unavailable' }
  } catch {
    return { kind: 'unavailable' }
  }
}
