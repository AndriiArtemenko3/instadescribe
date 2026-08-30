import type { BffDependencies } from './contracts'
import { BROWSER_ASSERTION_HEADER, createBrowserAssertion } from './browser-assertion'
import {
  clearCsrfCookie,
  clearSessionCookie,
  jsonResponse,
  resolvePrincipal,
  sameOrigin,
  validMutationCsrf,
} from './http'

type ServerFetch = (input: string | URL | Request, init?: RequestInit) => Promise<Response>

const MAX_REQUEST_BYTES = 64 * 1024
const MAX_RESPONSE_BYTES = 2 * 1024 * 1024
const UUID = '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
const SCENE = 'scene_[1-9][0-9]*'

const ROUTES: ReadonlyArray<{ method: string; pattern: RegExp }> = [
  { method: 'POST', pattern: /^jobs$/ },
  { method: 'GET', pattern: new RegExp(`^jobs/${UUID}$`) },
  { method: 'POST', pattern: new RegExp(`^jobs/${UUID}/uploads/complete$`) },
  { method: 'POST', pattern: new RegExp(`^jobs/${UUID}/cancel$`) },
  { method: 'GET', pattern: new RegExp(`^jobs/${UUID}/manifest$`) },
  { method: 'GET', pattern: new RegExp(`^jobs/${UUID}/overrides$`) },
  { method: 'PATCH', pattern: new RegExp(`^jobs/${UUID}/scenes/${SCENE}$`) },
  { method: 'POST', pattern: new RegExp(`^jobs/${UUID}/scenes/${SCENE}/tts-previews$`) },
  { method: 'GET', pattern: new RegExp(`^jobs/${UUID}/review$`) },
  { method: 'POST', pattern: new RegExp(`^jobs/${UUID}/review/finish$`) },
  { method: 'GET', pattern: new RegExp(`^jobs/${UUID}/render$`) },
  { method: 'GET', pattern: new RegExp(`^jobs/${UUID}/deliverables$`) },
  { method: 'GET', pattern: new RegExp(`^deliverables/${UUID}/content$`) },
  { method: 'GET', pattern: new RegExp(`^tts-previews/${UUID}$`) },
  { method: 'GET', pattern: new RegExp(`^tts-previews/${UUID}/content$`) },
  { method: 'PATCH', pattern: new RegExp(`^projects/${UUID}$`) },
  { method: 'POST', pattern: /^organization\/invitations$/ },
]

function allowed(method: string, path: string): boolean {
  return ROUTES.some((route) => route.method === method && route.pattern.test(path))
}

function normalizedOrigin(value: string): string | null {
  try {
    const url = new URL(value)
    const loopback = url.hostname === 'localhost' || url.hostname === '127.0.0.1' || url.hostname === '::1'
    if (url.protocol !== 'https:' && !(process.env.NODE_ENV !== 'production' && url.protocol === 'http:' && loopback)) return null
    if (url.username || url.password || url.search || url.hash || (url.pathname !== '/' && url.pathname !== '')) return null
    return url.origin
  } catch {
    return null
  }
}

async function boundedBody(response: Response): Promise<ArrayBuffer | null> {
  if (!response.body) return null
  const reader = response.body.getReader()
  const chunks: Uint8Array[] = []
  let received = 0
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    received += value.byteLength
    if (received > MAX_RESPONSE_BYTES) {
      await reader.cancel()
      return null
    }
    chunks.push(value)
  }
  const body = new Uint8Array(received)
  let offset = 0
  for (const chunk of chunks) {
    body.set(chunk, offset)
    offset += chunk.byteLength
  }
  return body.buffer
}

function unavailable(): Response {
  return jsonResponse(503, {
    error: { code: 'app_api_unavailable', message: 'The application API is temporarily unavailable.' },
  })
}

/**
 * Authenticated metadata-only relay. It accepts a small exact JSON allowlist;
 * upload and deliverable bytes continue directly between the browser and S3.
 */
export async function handleCloudProxy(
  request: Request,
  pathSegments: readonly string[],
  dependencies: BffDependencies,
  appApiOrigin: string,
  browserAssertionSecret: Uint8Array,
  serverFetch: ServerFetch = fetch,
): Promise<Response> {
  const path = pathSegments.join('/')
  const method = request.method.toUpperCase()
  if (!allowed(method, path)) {
    return jsonResponse(404, { error: { code: 'not_found', message: 'Not found.' } })
  }
  const origin = normalizedOrigin(appApiOrigin)
  if (!origin) return unavailable()

  const resolution = await resolvePrincipal(request, dependencies.sessions)
  if (resolution.kind === 'unavailable') return unavailable()
  if (resolution.kind === 'anonymous') {
    return jsonResponse(
      401,
      { error: { code: 'not_authenticated', message: 'Sign in to continue.' } },
      undefined,
      resolution.clearCookie ? [clearSessionCookie(), clearCsrfCookie()] : [],
    )
  }

  const mutation = method !== 'GET' && method !== 'HEAD'
  if (mutation && (!sameOrigin(request) || !validMutationCsrf(request))) {
    return jsonResponse(403, { error: { code: 'csrf_rejected', message: 'Request rejected.' } })
  }

  let body: ArrayBuffer | undefined
  if (mutation) {
    if (!request.headers.get('content-type')?.toLowerCase().startsWith('application/json')) {
      return jsonResponse(415, { error: { code: 'json_required', message: 'JSON is required.' } })
    }
    const declared = Number(request.headers.get('content-length') ?? 0)
    if (Number.isFinite(declared) && declared > MAX_REQUEST_BYTES) {
      return jsonResponse(413, { error: { code: 'request_too_large', message: 'Request rejected.' } })
    }
    const bytes = await request.arrayBuffer()
    if (bytes.byteLength > MAX_REQUEST_BYTES) {
      return jsonResponse(413, { error: { code: 'request_too_large', message: 'Request rejected.' } })
    }
    body = bytes
  }

  const headers = new Headers({
    Accept: 'application/json, application/problem+json',
    Authorization: `Bearer ${resolution.accessToken}`,
  })
  const assertion = createBrowserAssertion(
    browserAssertionSecret,
    resolution.accessToken,
    resolution.principal,
  )
  if (!assertion) return unavailable()
  headers.set(BROWSER_ASSERTION_HEADER, assertion)
  if (body !== undefined) headers.set('Content-Type', 'application/json')
  for (const name of ['Idempotency-Key', 'If-Match']) {
    const value = request.headers.get(name)
    if (value) headers.set(name, value)
  }

  let upstream: Response
  try {
    upstream = await serverFetch(new URL(`/api/app/v1/${path}`, origin), {
      method,
      headers,
      ...(body === undefined ? {} : { body }),
      cache: 'no-store',
      credentials: 'omit',
      redirect: 'manual',
      referrerPolicy: 'no-referrer',
      signal: AbortSignal.timeout(15_000),
    })
  } catch {
    return unavailable()
  }

  const responseHeaders = new Headers({
    'Cache-Control': 'private, no-store',
    'X-Content-Type-Options': 'nosniff',
    Vary: 'Cookie',
  })
  for (const name of ['content-type', 'etag', 'idempotent-replayed', 'retry-after', 'x-request-id']) {
    const value = upstream.headers.get(name)
    if (value) responseHeaders.set(name, value)
  }
  if (upstream.status === 303) {
    const location = upstream.headers.get('location')
    if (!location) return unavailable()
    responseHeaders.set('Location', location)
    return new Response(null, { status: 303, headers: responseHeaders })
  }
  if (upstream.status === 204) return new Response(null, { status: 204, headers: responseHeaders })

  const contentType = upstream.headers.get('content-type')?.toLowerCase() ?? ''
  if (!contentType.startsWith('application/json') && !contentType.startsWith('application/problem+json')) {
    return unavailable()
  }
  const responseBody = await boundedBody(upstream)
  if (responseBody === null) return unavailable()
  return new Response(responseBody, { status: upstream.status, headers: responseHeaders })
}
