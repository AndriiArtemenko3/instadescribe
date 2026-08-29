import { describe, expect, it, vi } from 'vitest'
import type { SessionPrincipal } from './contracts'
import { EnvelopeCrypto } from './envelope'
import type {
  ChallengeRecord,
  DataKeyProvider,
  IdentityProviderPort,
  ProviderTokens,
  SessionRecord,
  SessionRecordStore,
} from './ports'
import { SecureSessionGateway } from './session-gateway'

const principal: SessionPrincipal = {
  subject: 'human-1',
  email: 'editor@example.com',
  displayName: 'Editor',
  organizationId: 'org-1',
  role: 'editor',
  mfaVerified: false,
}

function tokens(accessToken = 'access-secret', expiresAt = '2030-01-01T01:00:00.000Z'): ProviderTokens {
  return {
    username: 'editor@example.com',
    email: 'editor@example.com',
    mfaVerified: false,
    accessToken,
    refreshToken: 'refresh-secret',
    idToken: 'id-secret',
    accessExpiresAt: expiresAt,
  }
}

class MemoryStore implements SessionRecordStore {
  sessions = new Map<string, SessionRecord>()
  challenges = new Map<string, ChallengeRecord>()

  async getSession(key: string) { return this.sessions.get(key) ?? null }
  async putSession(record: SessionRecord) {
    if (this.sessions.has(record.key)) return 'conflict' as const
    this.sessions.set(record.key, structuredClone(record))
    return 'stored' as const
  }
  async replaceSession(record: SessionRecord, expectedVersion: number) {
    if (this.sessions.get(record.key)?.version !== expectedVersion) return 'conflict' as const
    this.sessions.set(record.key, structuredClone(record))
    return 'stored' as const
  }
  async deleteSession(key: string) { this.sessions.delete(key); return 'deleted' as const }
  async getChallenge(key: string) { return this.challenges.get(key) ?? null }
  async putChallenge(record: ChallengeRecord) {
    if (this.challenges.has(record.key)) return 'conflict' as const
    this.challenges.set(record.key, structuredClone(record))
    return 'stored' as const
  }
  async deleteChallenge(key: string) { this.challenges.delete(key); return 'deleted' as const }
}

const keyProvider: DataKeyProvider = {
  generate: vi.fn().mockImplementation(async () => ({
    plaintextKey: new Uint8Array(32).fill(9),
    encryptedKey: new Uint8Array([1, 2, 3]),
  })),
  decrypt: vi.fn().mockImplementation(async () => new Uint8Array(32).fill(9)),
}

function identity(overrides: Partial<IdentityProviderPort> = {}): IdentityProviderPort {
  return {
    authenticate: vi.fn().mockResolvedValue({ kind: 'tokens', tokens: tokens() }),
    beginMfaEnrollment: vi.fn().mockResolvedValue({ kind: 'unavailable' }),
    continueChallenge: vi.fn().mockResolvedValue({ kind: 'tokens', tokens: tokens() }),
    refresh: vi.fn().mockResolvedValue({ kind: 'tokens', tokens: tokens('fresh-access') }),
    forgotPassword: vi.fn().mockResolvedValue('accepted'),
    resetPassword: vi.fn().mockResolvedValue('reset'),
    revoke: vi.fn().mockResolvedValue('revoked'),
    ...overrides,
  }
}

function gateway(
  store: MemoryStore,
  provider = identity(),
  now = new Date('2030-01-01T00:00:00.000Z'),
  resolvedPrincipal: SessionPrincipal = principal,
) {
  return new SecureSessionGateway({
    identity: provider,
    memberships: { resolve: vi.fn().mockResolvedValue(resolvedPrincipal) },
    records: store,
    crypto: new EnvelopeCrypto(keyProvider),
    cookieHmacSecret: new Uint8Array(32).fill(4),
    sessionTtlSeconds: 3600,
    challengeTtlSeconds: 600,
    refreshSkewSeconds: 60,
    clock: { now: () => now },
    random: (bytes) => new Uint8Array(bytes).fill(7),
  })
}

describe('SecureSessionGateway', () => {
  it('consumes a non-owner web session into an encrypted voluntary MFA challenge', async () => {
    const store = new MemoryStore()
    const enrollmentTokens = tokens()
    const provider = identity({
      beginMfaEnrollment: vi.fn().mockResolvedValue({
        kind: 'challenge',
        state: {
          flow: 'owner_mfa_enrollment',
          tokens: enrollmentTokens,
          challenge: { type: 'mfa_setup', totpSecret: 'VOLUNTARY-TOTP-SEED' },
        },
      }),
    })
    const secure = gateway(store, provider)
    const signedIn = await secure.signIn({ email: principal.email, password: 'temporary' })
    expect(signedIn.kind).toBe('authenticated')
    if (signedIn.kind !== 'authenticated') return

    const result = await secure.beginMfaEnrollment(signedIn.opaqueSession)

    expect(result).toMatchObject({
      kind: 'challenge',
      challenge: { type: 'mfa_setup', totpSecret: 'VOLUNTARY-TOTP-SEED' },
    })
    expect(store.sessions.size).toBe(0)
    expect(store.challenges.size).toBe(1)
    const persisted = JSON.stringify([...store.challenges.entries()])
    expect(persisted).not.toContain('VOLUNTARY-TOTP-SEED')
    expect(persisted).not.toContain('access-secret')
    expect(provider.beginMfaEnrollment).toHaveBeenCalledWith(enrollmentTokens)
  })

  it('keeps an already MFA-enabled session without creating another seed', async () => {
    const store = new MemoryStore()
    const verifiedTokens = { ...tokens(), mfaVerified: true }
    const provider = identity({
      authenticate: vi.fn().mockResolvedValue({ kind: 'tokens', tokens: verifiedTokens }),
    })
    const verifiedPrincipal = { ...principal, mfaVerified: true }
    const secure = gateway(
      store,
      provider,
      new Date('2030-01-01T00:00:00.000Z'),
      verifiedPrincipal,
    )
    const signedIn = await secure.signIn({ email: principal.email, password: 'temporary' })
    expect(signedIn.kind).toBe('authenticated')
    if (signedIn.kind !== 'authenticated') return

    await expect(secure.beginMfaEnrollment(signedIn.opaqueSession))
      .resolves.toEqual({ kind: 'already_enabled' })
    expect(store.sessions.size).toBe(1)
    expect(provider.beginMfaEnrollment).not.toHaveBeenCalled()
  })

  it('stores only a digest lookup key and an encrypted provider-token envelope', async () => {
    const store = new MemoryStore()
    const result = await gateway(store).signIn({ email: principal.email, password: 'temporary' })
    expect(result.kind).toBe('authenticated')
    if (result.kind !== 'authenticated') return

    const serialized = JSON.stringify([...store.sessions.entries()])
    expect([...store.sessions.keys()][0]).toMatch(/^s#[A-Za-z0-9_-]{43}$/)
    expect(serialized).not.toContain(result.opaqueSession)
    expect(serialized).not.toContain('access-secret')
    expect(serialized).not.toContain('refresh-secret')
    expect(serialized).not.toContain('id-secret')
    expect(serialized).not.toContain(principal.email)
    expect(serialized).not.toContain(principal.organizationId)

    const lookup = await gateway(store).lookup(result.opaqueSession)
    expect(lookup).toMatchObject({ kind: 'authenticated', accessToken: 'access-secret', principal })
  })

  it('refreshes near-expiry access tokens with an optimistic record version', async () => {
    const store = new MemoryStore()
    const provider = identity({
      authenticate: vi.fn().mockResolvedValue({
        kind: 'tokens',
        tokens: tokens('old-access', '2030-01-01T00:00:30.000Z'),
      }),
    })
    const secure = gateway(store, provider)
    const signedIn = await secure.signIn({ email: principal.email, password: 'secret' })
    expect(signedIn.kind).toBe('authenticated')
    if (signedIn.kind !== 'authenticated') return

    const lookup = await secure.lookup(signedIn.opaqueSession)
    expect(lookup).toMatchObject({ kind: 'authenticated', accessToken: 'fresh-access' })
    expect(provider.refresh).toHaveBeenCalledOnce()
    expect([...store.sessions.values()][0]?.version).toBe(2)
  })

  it('consumes encrypted Cognito challenge state before responding', async () => {
    const store = new MemoryStore()
    const provider = identity({
      authenticate: vi.fn().mockResolvedValue({
        kind: 'challenge',
        state: {
          flow: 'cognito_session',
          username: principal.email,
          providerSession: 'cognito-provider-session-secret',
          challenge: { type: 'new_password_required', requiredAttributes: [] },
        },
      }),
    })
    const secure = gateway(store, provider)
    const signIn = await secure.signIn({ email: principal.email, password: 'temporary' })
    expect(signIn.kind).toBe('challenge')
    if (signIn.kind !== 'challenge') return
    expect(JSON.stringify([...store.challenges.entries()])).not.toContain('cognito-provider-session-secret')

    const completed = await secure.continueChallenge(signIn.opaqueChallenge, {
      type: 'new_password_required',
      newPassword: 'A-new-long-password!1',
    })
    expect(completed.kind).toBe('authenticated')
    expect(store.challenges.size).toBe(0)
    expect(await secure.continueChallenge(signIn.opaqueChallenge, {
      type: 'new_password_required',
      newPassword: 'A-new-long-password!1',
    })).toEqual({ kind: 'invalid_challenge' })
  })

  it('deletes the local record even when Cognito revocation cannot be confirmed', async () => {
    const store = new MemoryStore()
    const secure = gateway(store, identity({ revoke: vi.fn().mockResolvedValue('unavailable') }))
    const signIn = await secure.signIn({ email: principal.email, password: 'secret' })
    expect(signIn.kind).toBe('authenticated')
    if (signIn.kind !== 'authenticated') return

    expect(await secure.revoke(signIn.opaqueSession)).toEqual({ kind: 'unavailable' })
    expect(store.sessions.size).toBe(0)
    expect(await secure.lookup(signIn.opaqueSession)).toEqual({ kind: 'invalid' })
  })

  it('starts encrypted TOTP enrollment instead of creating a session for an unverified owner', async () => {
    const store = new MemoryStore()
    const owner = { ...principal, role: 'owner' as const, mfaVerified: false }
    const provider = identity({
      beginMfaEnrollment: vi.fn().mockImplementation(async (providerTokens: ProviderTokens) => ({
        kind: 'challenge',
        state: {
          flow: 'owner_mfa_enrollment',
          tokens: providerTokens,
          challenge: { type: 'mfa_setup', totpSecret: 'TOP-SECRET-SEED' },
        },
      })),
    })
    const secure = new SecureSessionGateway({
      identity: provider,
      memberships: { resolve: vi.fn().mockResolvedValue(owner) },
      records: store,
      crypto: new EnvelopeCrypto(keyProvider),
      cookieHmacSecret: new Uint8Array(32).fill(4),
      sessionTtlSeconds: 3600,
      challengeTtlSeconds: 600,
      refreshSkewSeconds: 60,
      clock: { now: () => new Date('2030-01-01T00:00:00.000Z') },
      random: (bytes) => new Uint8Array(bytes).fill(8),
    })

    const result = await secure.signIn({ email: owner.email, password: 'secret' })

    expect(result).toMatchObject({
      kind: 'challenge',
      challenge: { type: 'mfa_setup', totpSecret: 'TOP-SECRET-SEED' },
    })
    expect(store.sessions.size).toBe(0)
    expect(provider.beginMfaEnrollment).toHaveBeenCalledWith(tokens())
    const persisted = JSON.stringify([...store.challenges.entries()])
    expect(persisted).not.toContain('TOP-SECRET-SEED')
    expect(persisted).not.toContain('access-secret')
    expect(persisted).not.toContain('refresh-secret')
  })

  it('consumes owner enrollment, revokes pre-MFA tokens, and requires a fresh login', async () => {
    const store = new MemoryStore()
    const owner = { ...principal, role: 'owner' as const, mfaVerified: false }
    const provider = identity({
      beginMfaEnrollment: vi.fn().mockImplementation(async (providerTokens: ProviderTokens) => ({
        kind: 'challenge',
        state: {
          flow: 'owner_mfa_enrollment',
          tokens: providerTokens,
          challenge: { type: 'mfa_setup', totpSecret: 'TOP-SECRET-SEED' },
        },
      })),
      continueChallenge: vi.fn().mockResolvedValue({ kind: 'reauthentication_required' }),
    })
    const secure = new SecureSessionGateway({
      identity: provider,
      memberships: { resolve: vi.fn().mockResolvedValue(owner) },
      records: store,
      crypto: new EnvelopeCrypto(keyProvider),
      cookieHmacSecret: new Uint8Array(32).fill(4),
      sessionTtlSeconds: 3600,
      challengeTtlSeconds: 600,
      refreshSkewSeconds: 60,
      clock: { now: () => new Date('2030-01-01T00:00:00.000Z') },
      random: (bytes) => new Uint8Array(bytes).fill(8),
    })
    const signIn = await secure.signIn({ email: owner.email, password: 'secret' })
    expect(signIn.kind).toBe('challenge')
    if (signIn.kind !== 'challenge') return

    await expect(secure.continueChallenge(signIn.opaqueChallenge, {
      type: 'mfa_setup',
      code: '123456',
    })).resolves.toEqual({ kind: 'reauthentication_required' })
    expect(store.sessions.size).toBe(0)
    expect(store.challenges.size).toBe(0)
    expect(provider.revoke).toHaveBeenCalledWith(tokens())
    await expect(secure.continueChallenge(signIn.opaqueChallenge, {
      type: 'mfa_setup',
      code: '123456',
    })).resolves.toEqual({ kind: 'invalid_challenge' })
  })

  it('expires owner enrollment state, revokes its tokens, and cannot replay it', async () => {
    const store = new MemoryStore()
    const owner = { ...principal, role: 'owner' as const, mfaVerified: false }
    const provider = identity({
      beginMfaEnrollment: vi.fn().mockImplementation(async (providerTokens: ProviderTokens) => ({
        kind: 'challenge',
        state: {
          flow: 'owner_mfa_enrollment',
          tokens: providerTokens,
          challenge: { type: 'mfa_setup', totpSecret: 'TOP-SECRET-SEED' },
        },
      })),
    })
    let current = new Date('2030-01-01T00:00:00.000Z')
    const secure = new SecureSessionGateway({
      identity: provider,
      memberships: { resolve: vi.fn().mockResolvedValue(owner) },
      records: store,
      crypto: new EnvelopeCrypto(keyProvider),
      cookieHmacSecret: new Uint8Array(32).fill(4),
      sessionTtlSeconds: 3600,
      challengeTtlSeconds: 600,
      refreshSkewSeconds: 60,
      clock: { now: () => current },
      random: (bytes) => new Uint8Array(bytes).fill(8),
    })
    const signIn = await secure.signIn({ email: owner.email, password: 'secret' })
    expect(signIn.kind).toBe('challenge')
    if (signIn.kind !== 'challenge') return
    current = new Date('2030-01-01T00:10:01.000Z')

    await expect(secure.inspectChallenge(signIn.opaqueChallenge)).resolves.toBeNull()
    expect(provider.revoke).toHaveBeenCalledWith(tokens())
    expect(store.challenges.size).toBe(0)
    await expect(secure.continueChallenge(signIn.opaqueChallenge, {
      type: 'mfa_setup', code: '123456',
    })).resolves.toEqual({ kind: 'invalid_challenge' })
  })
})
