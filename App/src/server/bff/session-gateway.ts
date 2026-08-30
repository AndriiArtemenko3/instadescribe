import { createHmac, randomBytes } from 'node:crypto'
import type {
  AuthChallenge,
  ChallengeAnswer,
  ChallengeResult,
  ForgotPasswordResult,
  MfaEnrollmentResult,
  ResetPasswordResult,
  RevokeResult,
  SessionGateway,
  SessionLookup,
  SignInCredentials,
  SignInResult,
} from './contracts'
import { EnvelopeCrypto } from './envelope'
import { canonicalBrowserEmail } from './browser-assertion'
import type {
  Clock,
  IdentityProviderPort,
  MembershipResolver,
  ProviderAuthenticationResult,
  ProviderChallengeState,
  ProviderTokens,
  SessionRecord,
  SessionRecordStore,
} from './ports'

const OPAQUE_BYTES = 32

export interface SecureSessionGatewayOptions {
  identity: IdentityProviderPort
  memberships: MembershipResolver
  records: SessionRecordStore
  crypto: EnvelopeCrypto
  cookieHmacSecret: Uint8Array
  sessionTtlSeconds: number
  challengeTtlSeconds: number
  refreshSkewSeconds: number
  clock?: Clock
  random?: (bytes: number) => Uint8Array
}

interface StoredTokenPayload {
  kind: 'provider_tokens'
  tokens: ProviderTokens
  principal: import('./contracts').SessionPrincipal
}

interface StoredChallengePayload {
  kind: 'provider_challenge'
  state: ProviderChallengeState
}

function isNonEmptyString(value: unknown, maximum = 16_384): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= maximum
}

function validTokens(value: unknown): value is ProviderTokens {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const token = value as Partial<ProviderTokens>
  return (
    isNonEmptyString(token.username, 254) &&
    isNonEmptyString(token.email, 254) &&
    typeof token.mfaVerified === 'boolean' &&
    isNonEmptyString(token.accessToken) &&
    (token.refreshToken === null || isNonEmptyString(token.refreshToken)) &&
    (token.idToken === null || isNonEmptyString(token.idToken)) &&
    isNonEmptyString(token.accessExpiresAt, 128) &&
    Number.isFinite(Date.parse(token.accessExpiresAt))
  )
}

function validResolvedPrincipal(value: unknown): value is import('./contracts').SessionPrincipal {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const principal = value as Partial<import('./contracts').SessionPrincipal>
  return (
    isNonEmptyString(principal.subject, 1024) &&
    isNonEmptyString(principal.email, 254) &&
    isNonEmptyString(principal.displayName, 1024) &&
    isNonEmptyString(principal.organizationId, 1024) &&
    typeof principal.role === 'string' &&
    ['owner', 'editor', 'reviewer', 'viewer'].includes(principal.role) &&
    typeof principal.mfaVerified === 'boolean'
  )
}

function validPrincipal(value: unknown): value is import('./contracts').SessionPrincipal {
  return validResolvedPrincipal(value) && (value.role !== 'owner' || value.mfaVerified)
}

function principalMatchesTokens(
  principal: import('./contracts').SessionPrincipal,
  tokens: ProviderTokens,
): boolean {
  const principalEmail = canonicalBrowserEmail(principal.email)
  return principalEmail !== null && principalEmail === canonicalBrowserEmail(tokens.email) &&
    principal.mfaVerified === tokens.mfaVerified
}

function validChallenge(value: unknown): value is AuthChallenge {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const challenge = value as Partial<AuthChallenge>
  if (challenge.type === 'software_token_mfa') return true
  if (challenge.type === 'mfa_setup') return isNonEmptyString(challenge.totpSecret, 512)
  return challenge.type === 'new_password_required' &&
    Array.isArray(challenge.requiredAttributes) &&
    challenge.requiredAttributes.every((item) => isNonEmptyString(item, 128))
}

function validChallengeState(value: unknown): value is ProviderChallengeState {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const state = value as Record<string, unknown>
  if (state.flow === 'cognito_session') {
    return validChallenge(state.challenge) &&
      isNonEmptyString(state.providerSession) &&
      isNonEmptyString(state.username, 254)
  }
  return state.flow === 'owner_mfa_enrollment' &&
    validChallenge(state.challenge) &&
    state.challenge.type === 'mfa_setup' &&
    validTokens(state.tokens)
}

function sessionContext(key: string): Readonly<Record<string, string>> {
  return { purpose: 'session-tokens', recordKey: key }
}

function challengeContext(key: string): Readonly<Record<string, string>> {
  return { purpose: 'auth-challenge', recordKey: key }
}

export class SecureSessionGateway implements SessionGateway {
  private readonly now: () => Date
  private readonly random: (bytes: number) => Uint8Array

  constructor(private readonly options: SecureSessionGatewayOptions) {
    if (options.cookieHmacSecret.byteLength < 32) throw new Error('cookie HMAC secret is too short')
    if (options.sessionTtlSeconds < 300 || options.challengeTtlSeconds < 60 || options.refreshSkewSeconds < 0) {
      throw new Error('invalid session lifetime configuration')
    }
    this.now = () => options.clock?.now() ?? new Date()
    this.random = options.random ?? ((bytes) => randomBytes(bytes))
  }

  async lookup(opaqueSession: string): Promise<SessionLookup> {
    const key = this.digest('session', opaqueSession)
    if (!key) return { kind: 'invalid' }
    return this.loadSession(key, true)
  }

  async signIn(credentials: SignInCredentials): Promise<SignInResult> {
    const result = await this.options.identity.authenticate(credentials)
    return this.persistAuthentication(result)
  }

  async inspectChallenge(opaqueChallenge: string): Promise<AuthChallenge | null | 'unavailable'> {
    const key = this.digest('challenge', opaqueChallenge)
    if (!key) return null
    const record = await this.options.records.getChallenge(key)
    if (record === 'unavailable') return 'unavailable'
    if (!record) return null
    const payload = await this.options.crypto.decrypt<StoredChallengePayload>(record.state, challengeContext(key))
    if (payload === 'unavailable' || payload.kind !== 'provider_challenge' || !validChallengeState(payload.state)) {
      return 'unavailable'
    }
    if (record.expiresAtEpochSeconds <= this.epochSeconds()) {
      await this.options.records.deleteChallenge(key)
      await this.revokeChallengeTokens(payload.state)
      return null
    }
    return payload.state.challenge
  }

  async continueChallenge(opaqueChallenge: string, answer: ChallengeAnswer): Promise<ChallengeResult> {
    const key = this.digest('challenge', opaqueChallenge)
    if (!key) return { kind: 'invalid_challenge' }
    const record = await this.options.records.getChallenge(key)
    if (record === 'unavailable') return { kind: 'unavailable' }
    if (!record) return { kind: 'invalid_challenge' }
    const payload = await this.options.crypto.decrypt<StoredChallengePayload>(record.state, challengeContext(key))
    if (payload === 'unavailable' || payload.kind !== 'provider_challenge' || !validChallengeState(payload.state)) {
      return { kind: 'unavailable' }
    }
    if (record.expiresAtEpochSeconds <= this.epochSeconds()) {
      await this.options.records.deleteChallenge(key)
      await this.revokeChallengeTokens(payload.state)
      return { kind: 'invalid_challenge' }
    }
    if (payload.state.challenge.type !== answer.type) return { kind: 'invalid_challenge' }

    // Consume before answering Cognito so the provider Session cannot be replayed.
    if (await this.options.records.deleteChallenge(key) !== 'deleted') return { kind: 'unavailable' }
    const result = await this.options.identity.continueChallenge(payload.state, answer)
    await this.revokeChallengeTokens(payload.state)
    if (result.kind === 'invalid_credentials' || result.kind === 'invalid_challenge') {
      return { kind: 'invalid_challenge' }
    }
    if (result.kind === 'reauthentication_required') return result
    return this.persistAuthentication(result)
  }

  async forgotPassword(email: string): Promise<ForgotPasswordResult> {
    const result = await this.options.identity.forgotPassword(email)
    return result === 'accepted' ? { kind: 'accepted' } : { kind: 'unavailable' }
  }

  async resetPassword(email: string, code: string, newPassword: string): Promise<ResetPasswordResult> {
    const result = await this.options.identity.resetPassword(email, code, newPassword)
    if (result === 'reset') return { kind: 'reset' }
    if (result === 'invalid_code') return { kind: 'invalid_code' }
    return { kind: 'unavailable' }
  }

  async beginMfaEnrollment(opaqueSession: string): Promise<MfaEnrollmentResult> {
    const key = this.digest('session', opaqueSession)
    if (!key) return { kind: 'invalid' }
    const record = await this.options.records.getSession(key)
    if (record === 'unavailable') return { kind: 'unavailable' }
    if (!record) return { kind: 'invalid' }
    if (record.expiresAtEpochSeconds <= this.epochSeconds()) {
      await this.options.records.deleteSession(key)
      return { kind: 'invalid' }
    }
    const payload = await this.decryptSession(record)
    if (payload === 'unavailable') return { kind: 'unavailable' }
    if (payload.tokens.mfaVerified || payload.principal.mfaVerified) {
      return { kind: 'already_enabled' }
    }

    // A voluntary enrollment deliberately consumes the web session before
    // Cognito receives the request. Provider tokens then exist only inside an
    // encrypted, short-lived challenge and are revoked on every failed path.
    if (await this.options.records.deleteSession(key) !== 'deleted') {
      await this.options.identity.revoke(payload.tokens)
      return { kind: 'unavailable' }
    }
    const enrollment = await this.options.identity.beginMfaEnrollment(payload.tokens)
    if (enrollment.kind !== 'challenge' || enrollment.state.flow !== 'owner_mfa_enrollment') {
      await this.options.identity.revoke(payload.tokens)
      return { kind: 'unavailable' }
    }
    const persisted = await this.persistChallenge(enrollment.state)
    return persisted.kind === 'challenge' ? persisted : { kind: 'unavailable' }
  }

  async revoke(opaqueSession: string): Promise<RevokeResult> {
    const key = this.digest('session', opaqueSession)
    if (!key) return { kind: 'revoked' }
    const record = await this.options.records.getSession(key)
    if (record === 'unavailable') return { kind: 'unavailable' }
    if (!record) return { kind: 'revoked' }

    const payload = await this.decryptSession(record)
    // Local invalidation is authoritative for the BFF, even if Cognito is down.
    if (await this.options.records.deleteSession(key) !== 'deleted') return { kind: 'unavailable' }
    if (payload === 'unavailable') return { kind: 'unavailable' }
    const revoked = await this.options.identity.revoke(payload.tokens)
    return revoked === 'revoked' ? { kind: 'revoked' } : { kind: 'unavailable' }
  }

  private async loadSession(key: string, permitRefresh: boolean): Promise<SessionLookup> {
    const record = await this.options.records.getSession(key)
    if (record === 'unavailable') return { kind: 'unavailable' }
    if (!record) return { kind: 'invalid' }
    if (record.expiresAtEpochSeconds <= this.epochSeconds()) {
      await this.options.records.deleteSession(key)
      return { kind: 'invalid' }
    }
    const payload = await this.decryptSession(record)
    if (payload === 'unavailable') return { kind: 'unavailable' }
    const { tokens, principal: storedPrincipal } = payload
    const refreshAt = this.now().getTime() + this.options.refreshSkewSeconds * 1000
    if (Date.parse(tokens.accessExpiresAt) > refreshAt) {
      return {
        kind: 'authenticated',
        principal: storedPrincipal,
        accessToken: tokens.accessToken,
        expiresAt: new Date(record.expiresAtEpochSeconds * 1000).toISOString(),
      }
    }
    if (!permitRefresh || !tokens.refreshToken) {
      if (!tokens.refreshToken) await this.options.records.deleteSession(key)
      return tokens.refreshToken ? { kind: 'unavailable' } : { kind: 'invalid' }
    }

    const refreshed = await this.options.identity.refresh(tokens)
    if (refreshed.kind === 'invalid_credentials' || refreshed.kind === 'invalid_challenge') {
      await this.options.records.deleteSession(key)
      return { kind: 'invalid' }
    }
    if (refreshed.kind !== 'tokens') return { kind: 'unavailable' }
    if (!refreshed.tokens.refreshToken) refreshed.tokens.refreshToken = tokens.refreshToken
    const principal = await this.options.memberships.resolve(refreshed.tokens)
    if (principal === 'unavailable') return { kind: 'unavailable' }
    if (!validPrincipal(principal) || !principalMatchesTokens(principal, refreshed.tokens)) {
      await this.options.records.deleteSession(key)
      await this.options.identity.revoke(refreshed.tokens)
      return { kind: 'invalid' }
    }
    const encryptedTokens = await this.options.crypto.encrypt(
      { kind: 'provider_tokens', tokens: refreshed.tokens, principal } satisfies StoredTokenPayload,
      sessionContext(key),
    )
    if (encryptedTokens === 'unavailable') return { kind: 'unavailable' }
    const replacement: SessionRecord = {
      ...record,
      encryptedTokens,
      version: record.version + 1,
    }
    const replaced = await this.options.records.replaceSession(replacement, record.version)
    if (replaced === 'stored') {
      return {
        kind: 'authenticated',
        principal,
        accessToken: refreshed.tokens.accessToken,
        expiresAt: new Date(record.expiresAtEpochSeconds * 1000).toISOString(),
      }
    }
    if (replaced === 'conflict') return this.loadSession(key, false)
    return { kind: 'unavailable' }
  }

  private async persistAuthentication(result: ProviderAuthenticationResult): Promise<SignInResult> {
    if (result.kind === 'invalid_credentials') return { kind: 'invalid_credentials' }
    if (
      result.kind === 'invalid_challenge' ||
      result.kind === 'reauthentication_required' ||
      result.kind === 'unavailable'
    ) return { kind: 'unavailable' }
    if (result.kind === 'challenge') return this.persistChallenge(result.state)

    const principal = await this.options.memberships.resolve(result.tokens)
    if (principal === 'unavailable') {
      await this.options.identity.revoke(result.tokens)
      return { kind: 'unavailable' }
    }
    if (!validResolvedPrincipal(principal) || !principalMatchesTokens(principal, result.tokens)) {
      await this.options.identity.revoke(result.tokens)
      return { kind: 'unavailable' }
    }
    if (principal.role === 'owner' && !principal.mfaVerified) {
      const enrollment = await this.options.identity.beginMfaEnrollment(result.tokens)
      if (enrollment.kind !== 'challenge' || enrollment.state.flow !== 'owner_mfa_enrollment') {
        await this.options.identity.revoke(result.tokens)
        return { kind: 'unavailable' }
      }
      return this.persistChallenge(enrollment.state)
    }
    if (!validPrincipal(principal)) {
      await this.options.identity.revoke(result.tokens)
      return { kind: 'unavailable' }
    }
    const opaqueSession = this.opaqueToken()
    const key = this.digest('session', opaqueSession)
    if (!key) {
      await this.options.identity.revoke(result.tokens)
      return { kind: 'unavailable' }
    }
    const expiresAtEpochSeconds = this.epochSeconds() + this.options.sessionTtlSeconds
    const encryptedTokens = await this.options.crypto.encrypt(
      { kind: 'provider_tokens', tokens: result.tokens, principal } satisfies StoredTokenPayload,
      sessionContext(key),
    )
    if (encryptedTokens === 'unavailable') {
      await this.options.identity.revoke(result.tokens)
      return { kind: 'unavailable' }
    }
    const stored = await this.options.records.putSession({
      key,
      version: 1,
      encryptedTokens,
      expiresAtEpochSeconds,
    })
    if (stored !== 'stored') {
      await this.options.identity.revoke(result.tokens)
      return { kind: 'unavailable' }
    }
    return {
      kind: 'authenticated',
      principal,
      opaqueSession,
      expiresAt: new Date(expiresAtEpochSeconds * 1000).toISOString(),
    }
  }

  private async persistChallenge(state: ProviderChallengeState): Promise<SignInResult> {
    const opaqueChallenge = this.opaqueToken()
    const key = this.digest('challenge', opaqueChallenge)
    if (!key) {
      await this.revokeChallengeTokens(state)
      return { kind: 'unavailable' }
    }
    const expiresAtEpochSeconds = this.epochSeconds() + this.options.challengeTtlSeconds
    const encrypted = await this.options.crypto.encrypt(
      { kind: 'provider_challenge', state } satisfies StoredChallengePayload,
      challengeContext(key),
    )
    if (encrypted === 'unavailable') {
      await this.revokeChallengeTokens(state)
      return { kind: 'unavailable' }
    }
    const stored = await this.options.records.putChallenge({ key, state: encrypted, expiresAtEpochSeconds })
    if (stored !== 'stored') {
      await this.revokeChallengeTokens(state)
      return { kind: 'unavailable' }
    }
    return {
      kind: 'challenge',
      challenge: state.challenge,
      opaqueChallenge,
      expiresAt: new Date(expiresAtEpochSeconds * 1000).toISOString(),
    }
  }

  private async decryptSession(record: SessionRecord): Promise<StoredTokenPayload | 'unavailable'> {
    const payload = await this.options.crypto.decrypt<StoredTokenPayload>(
      record.encryptedTokens,
      sessionContext(record.key),
    )
    if (
      payload === 'unavailable' ||
      payload.kind !== 'provider_tokens' ||
      !validTokens(payload.tokens) ||
      !validPrincipal(payload.principal)
    ) {
      return 'unavailable'
    }
    return payload
  }

  private async revokeChallengeTokens(state: ProviderChallengeState): Promise<void> {
    if (state.flow === 'owner_mfa_enrollment') await this.options.identity.revoke(state.tokens)
  }

  private opaqueToken(): string {
    return Buffer.from(this.random(OPAQUE_BYTES)).toString('base64url')
  }

  private digest(kind: 'session' | 'challenge', opaque: string): string | null {
    if (!/^[A-Za-z0-9_-]{43}$/.test(opaque)) return null
    const digest = createHmac('sha256', this.options.cookieHmacSecret)
      .update(kind)
      .update('\0')
      .update(opaque)
      .digest('base64url')
    return `${kind === 'session' ? 's' : 'c'}#${digest}`
  }

  private epochSeconds(): number {
    return Math.floor(this.now().getTime() / 1000)
  }
}
