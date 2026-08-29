import type {
  AuthChallenge,
  BffDependencies,
  ChallengeAnswer,
  ChallengeResult,
  SignInCredentials,
  SignInResult,
} from './contracts'
import {
  clearChallengeCookie,
  clearCsrfCookie,
  clearSessionCookie,
  generateCsrfToken,
  issueChallengeCookie,
  issueCsrfCookie,
  issueSessionCookie,
  jsonResponse,
  readChallengeCookie,
  readCsrfCookie,
  readSessionCookie,
  resolvePrincipal,
  sameOrigin,
  validMutationCsrf,
} from './http'

const JSON_LIMIT = 16_384

function authUnavailable(): Response {
  return jsonResponse(503, {
    error: { code: 'session_provider_unavailable', message: 'Sign-in is temporarily unavailable.' },
  })
}

function exactKeys(record: Record<string, unknown>, expected: readonly string[]): boolean {
  const actual = Object.keys(record).sort()
  const sortedExpected = [...expected].sort()
  return actual.length === sortedExpected.length && actual.every((key, index) => key === sortedExpected[index])
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function validEmail(value: unknown): value is string {
  return typeof value === 'string' && value.length >= 3 && value.length <= 254 && value.includes('@')
}

async function readJson(request: Request): Promise<unknown | 'invalid' | 'too_large' | 'unsupported'> {
  if (!request.headers.get('content-type')?.toLowerCase().startsWith('application/json')) return 'unsupported'
  const declaredLength = Number(request.headers.get('content-length') ?? 0)
  if (Number.isFinite(declaredLength) && declaredLength > JSON_LIMIT) return 'too_large'
  try {
    return await request.json()
  } catch {
    return 'invalid'
  }
}

function invalidJsonResponse(value: unknown | 'invalid' | 'too_large' | 'unsupported'): Response | null {
  if (value === 'unsupported') {
    return jsonResponse(415, { error: { code: 'json_required', message: 'JSON is required.' } })
  }
  if (value === 'too_large') {
    return jsonResponse(413, { error: { code: 'request_too_large', message: 'Request rejected.' } })
  }
  if (value === 'invalid') {
    return jsonResponse(400, { error: { code: 'invalid_json', message: 'Invalid JSON.' } })
  }
  return null
}

function challengeBody(challenge: AuthChallenge): { challenge: AuthChallenge } {
  if (challenge.type === 'new_password_required') {
    return { challenge: { type: challenge.type, requiredAttributes: [...challenge.requiredAttributes] } }
  }
  if (challenge.type === 'mfa_setup') {
    return { challenge: { type: challenge.type, totpSecret: challenge.totpSecret } }
  }
  return { challenge: { type: challenge.type } }
}

function authenticationResponse(result: SignInResult | ChallengeResult): Response {
  if (result.kind === 'unavailable') return authUnavailable()
  if (result.kind === 'invalid_credentials') {
    return jsonResponse(401, {
      error: { code: 'invalid_credentials', message: 'Email or password was not accepted.' },
    })
  }
  if (result.kind === 'invalid_challenge') {
    return jsonResponse(
      409,
      { error: { code: 'invalid_challenge', message: 'The sign-in challenge has expired or is invalid.' } },
      undefined,
      [clearChallengeCookie()],
    )
  }
  if (result.kind === 'challenge') {
    const cookie = issueChallengeCookie(result.opaqueChallenge, result.expiresAt)
    if (!cookie) return authUnavailable()
    return jsonResponse(202, challengeBody(result.challenge), undefined, [
      cookie,
      clearSessionCookie(),
      clearCsrfCookie(),
    ])
  }
  if (result.kind === 'reauthentication_required') {
    return jsonResponse(
      200,
      { reauthenticationRequired: true },
      undefined,
      [clearChallengeCookie(), clearSessionCookie(), clearCsrfCookie()],
    )
  }

  const sessionCookie = issueSessionCookie(result.opaqueSession, result.expiresAt)
  const csrfCookie = issueCsrfCookie(generateCsrfToken(), result.expiresAt)
  if (!sessionCookie || !csrfCookie) return authUnavailable()
  const { email, displayName, organizationId, role } = result.principal
  return jsonResponse(
    200,
    { user: { email, displayName, organizationId, role } },
    undefined,
    [sessionCookie, csrfCookie, clearChallengeCookie()],
  )
}

export async function handleSessionGet(
  request: Request,
  dependencies: BffDependencies,
): Promise<Response> {
  const resolution = await resolvePrincipal(request, dependencies.sessions)
  if (resolution.kind === 'unavailable') return authUnavailable()
  if (resolution.kind === 'anonymous') {
    return jsonResponse(
      401,
      { error: { code: 'not_authenticated', message: 'Sign in to continue.' } },
      undefined,
      resolution.clearCookie ? [clearSessionCookie(), clearCsrfCookie()] : [],
    )
  }

  const { email, displayName, organizationId, role } = resolution.principal
  const existingCsrf = readCsrfCookie(request)
  const csrfCookie = existingCsrf && /^[A-Za-z0-9_-]{43}$/.test(existingCsrf)
    ? null
    : issueCsrfCookie(generateCsrfToken(), resolution.expiresAt)
  return jsonResponse(
    200,
    { user: { email, displayName, organizationId, role } },
    undefined,
    csrfCookie ? [csrfCookie] : [],
  )
}

function validCredentials(value: unknown): value is SignInCredentials {
  const body = record(value)
  if (!body || !exactKeys(body, ['email', 'password'])) return false
  return validEmail(body.email) && typeof body.password === 'string' && body.password.length >= 1 && body.password.length <= 1024
}

export async function handleSessionPost(
  request: Request,
  dependencies: BffDependencies,
): Promise<Response> {
  if (!sameOrigin(request)) {
    return jsonResponse(403, { error: { code: 'cross_origin', message: 'Request rejected.' } })
  }
  const body = await readJson(request)
  const invalid = invalidJsonResponse(body)
  if (invalid) return invalid
  if (!validCredentials(body)) {
    return jsonResponse(400, { error: { code: 'invalid_credentials_shape', message: 'Invalid sign-in request.' } })
  }

  try {
    return authenticationResponse(await dependencies.sessions.signIn({
      email: body.email.trim(),
      password: body.password,
    }))
  } catch {
    return authUnavailable()
  }
}

export async function handleSessionDelete(
  request: Request,
  dependencies: BffDependencies,
): Promise<Response> {
  if (!sameOrigin(request)) {
    return jsonResponse(403, { error: { code: 'cross_origin', message: 'Request rejected.' } })
  }
  if (!validMutationCsrf(request)) {
    return jsonResponse(403, { error: { code: 'csrf_rejected', message: 'Request rejected.' } })
  }
  const clearCookies = [clearSessionCookie(), clearCsrfCookie(), clearChallengeCookie()]
  const opaqueSession = readSessionCookie(request)
  if (!opaqueSession) return jsonResponse(200, { signedOut: true }, undefined, clearCookies)

  try {
    const result = await dependencies.sessions.revoke(opaqueSession)
    if (result.kind === 'revoked') {
      return jsonResponse(200, { signedOut: true }, undefined, clearCookies)
    }
  } catch {
    // Browser cookies are still cleared; remote revocation remains unconfirmed.
  }
  return jsonResponse(
    503,
    { error: { code: 'session_provider_unavailable', message: 'Server sign-out could not be confirmed.' } },
    undefined,
    clearCookies,
  )
}

export async function handleMfaEnrollmentPost(
  request: Request,
  dependencies: BffDependencies,
): Promise<Response> {
  if (!sameOrigin(request)) {
    return jsonResponse(403, { error: { code: 'cross_origin', message: 'Request rejected.' } })
  }
  if (!validMutationCsrf(request)) {
    return jsonResponse(403, { error: { code: 'csrf_rejected', message: 'Request rejected.' } })
  }
  const opaqueSession = readSessionCookie(request)
  if (!opaqueSession) {
    return jsonResponse(
      401,
      { error: { code: 'not_authenticated', message: 'Sign in to continue.' } },
      undefined,
      [clearSessionCookie(), clearCsrfCookie()],
    )
  }
  try {
    const result = await dependencies.sessions.beginMfaEnrollment(opaqueSession)
    if (result.kind === 'challenge') return authenticationResponse(result)
    if (result.kind === 'already_enabled') {
      return jsonResponse(200, { alreadyEnabled: true })
    }
    if (result.kind === 'invalid') {
      return jsonResponse(
        401,
        { error: { code: 'not_authenticated', message: 'Sign in to continue.' } },
        undefined,
        [clearSessionCookie(), clearCsrfCookie()],
      )
    }
  } catch {
    // The fail-closed response below removes all browser-side authority.
  }
  return jsonResponse(
    503,
    { error: { code: 'session_provider_unavailable', message: 'MFA enrollment is temporarily unavailable.' } },
    undefined,
    [clearSessionCookie(), clearCsrfCookie(), clearChallengeCookie()],
  )
}

export async function handleChallengeGet(
  request: Request,
  dependencies: BffDependencies,
): Promise<Response> {
  const opaqueChallenge = readChallengeCookie(request)
  if (!opaqueChallenge) {
    return jsonResponse(404, { error: { code: 'challenge_not_found', message: 'No sign-in challenge is active.' } })
  }
  try {
    const challenge = await dependencies.sessions.inspectChallenge(opaqueChallenge)
    if (challenge === 'unavailable') return authUnavailable()
    if (!challenge) {
      return jsonResponse(
        404,
        { error: { code: 'challenge_not_found', message: 'The sign-in challenge has expired.' } },
        undefined,
        [clearChallengeCookie()],
      )
    }
    return jsonResponse(200, challengeBody(challenge))
  } catch {
    return authUnavailable()
  }
}

function challengeAnswer(value: unknown): ChallengeAnswer | null {
  const body = record(value)
  if (!body || !exactKeys(body, body.type === 'new_password_required' ? ['type', 'newPassword'] : ['type', 'code'])) {
    return null
  }
  if (body.type === 'new_password_required') {
    return typeof body.newPassword === 'string' && body.newPassword.length >= 1 && body.newPassword.length <= 1024
      ? { type: body.type, newPassword: body.newPassword }
      : null
  }
  if ((body.type === 'software_token_mfa' || body.type === 'mfa_setup') && typeof body.code === 'string' && /^\d{6}$/.test(body.code)) {
    return { type: body.type, code: body.code }
  }
  return null
}

export async function handleChallengePost(
  request: Request,
  dependencies: BffDependencies,
): Promise<Response> {
  if (!sameOrigin(request)) {
    return jsonResponse(403, { error: { code: 'cross_origin', message: 'Request rejected.' } })
  }
  const opaqueChallenge = readChallengeCookie(request)
  if (!opaqueChallenge) {
    return jsonResponse(409, { error: { code: 'invalid_challenge', message: 'No sign-in challenge is active.' } })
  }
  const body = await readJson(request)
  const invalid = invalidJsonResponse(body)
  if (invalid) return invalid
  const answer = challengeAnswer(body)
  if (!answer) {
    return jsonResponse(400, { error: { code: 'invalid_challenge_shape', message: 'Invalid challenge response.' } })
  }
  try {
    return authenticationResponse(await dependencies.sessions.continueChallenge(opaqueChallenge, answer))
  } catch {
    return authUnavailable()
  }
}

export async function handleForgotPasswordPost(
  request: Request,
  dependencies: BffDependencies,
): Promise<Response> {
  if (!sameOrigin(request)) {
    return jsonResponse(403, { error: { code: 'cross_origin', message: 'Request rejected.' } })
  }
  const body = await readJson(request)
  const invalid = invalidJsonResponse(body)
  if (invalid) return invalid
  const data = record(body)
  if (!data || !exactKeys(data, ['email']) || !validEmail(data.email)) {
    return jsonResponse(400, { error: { code: 'invalid_recovery_shape', message: 'Invalid recovery request.' } })
  }
  try {
    const result = await dependencies.sessions.forgotPassword(data.email.trim())
    if (result.kind === 'unavailable') return authUnavailable()
    return jsonResponse(202, { accepted: true })
  } catch {
    return authUnavailable()
  }
}

export async function handleResetPasswordPost(
  request: Request,
  dependencies: BffDependencies,
): Promise<Response> {
  if (!sameOrigin(request)) {
    return jsonResponse(403, { error: { code: 'cross_origin', message: 'Request rejected.' } })
  }
  const body = await readJson(request)
  const invalid = invalidJsonResponse(body)
  if (invalid) return invalid
  const data = record(body)
  if (
    !data || !exactKeys(data, ['email', 'code', 'newPassword']) || !validEmail(data.email) ||
    typeof data.code !== 'string' || !/^\d{6}$/.test(data.code) ||
    typeof data.newPassword !== 'string' || data.newPassword.length < 1 || data.newPassword.length > 1024
  ) {
    return jsonResponse(400, { error: { code: 'invalid_reset_shape', message: 'Invalid password reset request.' } })
  }
  try {
    const result = await dependencies.sessions.resetPassword(data.email.trim(), data.code, data.newPassword)
    if (result.kind === 'unavailable') return authUnavailable()
    if (result.kind === 'invalid_code') {
      return jsonResponse(400, { error: { code: 'invalid_confirmation_code', message: 'The confirmation code is invalid or expired.' } })
    }
    return jsonResponse(200, { reset: true })
  } catch {
    return authUnavailable()
  }
}

export async function handleProjectsGet(
  request: Request,
  dependencies: BffDependencies,
): Promise<Response> {
  const resolution = await resolvePrincipal(request, dependencies.sessions)
  if (resolution.kind === 'unavailable') return authUnavailable()
  if (resolution.kind === 'anonymous') {
    return jsonResponse(
      401,
      { error: { code: 'not_authenticated', message: 'Sign in to view projects.' } },
      undefined,
      resolution.clearCookie ? [clearSessionCookie(), clearCsrfCookie()] : [],
    )
  }

  let result
  try {
    result = await dependencies.projects.list({
      principal: resolution.principal,
      accessToken: resolution.accessToken,
    })
  } catch {
    return jsonResponse(503, {
      error: { code: 'project_provider_unavailable', message: 'Projects are temporarily unavailable.' },
    })
  }
  if (result.kind === 'unavailable') {
    return jsonResponse(503, {
      error: { code: 'project_provider_unavailable', message: 'Projects are temporarily unavailable.' },
    })
  }

  const projects = result.projects.map(({ id, orgSlug, currentJobId, name, status, updatedAt }) => ({
    id,
    orgSlug,
    currentJobId,
    name,
    status,
    updatedAt,
  }))
  return jsonResponse(200, { projects })
}
