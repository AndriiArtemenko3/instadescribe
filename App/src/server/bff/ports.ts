import type {
  AuthChallenge,
  ChallengeAnswer,
  SessionPrincipal,
  SignInCredentials,
} from './contracts'

export interface ProviderTokens {
  username: string
  email: string
  /** Server-owned flow evidence; never inferred from JWT claims. */
  mfaVerified: boolean
  accessToken: string
  refreshToken: string | null
  idToken: string | null
  accessExpiresAt: string
}

export type ProviderChallengeState =
  | {
      flow: 'cognito_session'
      challenge: AuthChallenge
      providerSession: string
      username: string
    }
  | {
      flow: 'owner_mfa_enrollment'
      challenge: Extract<AuthChallenge, { type: 'mfa_setup' }>
      tokens: ProviderTokens
    }

export type ProviderAuthenticationResult =
  | { kind: 'tokens'; tokens: ProviderTokens }
  | { kind: 'challenge'; state: ProviderChallengeState }
  | { kind: 'invalid_credentials' }
  | { kind: 'invalid_challenge' }
  | { kind: 'reauthentication_required' }
  | { kind: 'unavailable' }

export interface IdentityProviderPort {
  authenticate(credentials: SignInCredentials): Promise<ProviderAuthenticationResult>
  beginMfaEnrollment(tokens: ProviderTokens): Promise<ProviderAuthenticationResult>
  continueChallenge(
    state: ProviderChallengeState,
    answer: ChallengeAnswer,
  ): Promise<ProviderAuthenticationResult>
  refresh(tokens: ProviderTokens): Promise<ProviderAuthenticationResult>
  forgotPassword(email: string): Promise<'accepted' | 'unavailable'>
  resetPassword(
    email: string,
    code: string,
    newPassword: string,
  ): Promise<'reset' | 'invalid_code' | 'unavailable'>
  revoke(tokens: ProviderTokens): Promise<'revoked' | 'unavailable'>
}

export interface MembershipResolver {
  resolve(tokens: ProviderTokens): Promise<SessionPrincipal | 'unavailable'>
}

export interface EncryptedEnvelope {
  version: 1
  encryptedKey: string
  iv: string
  ciphertext: string
  authTag: string
}

export interface SessionRecord {
  key: string
  version: number
  encryptedTokens: EncryptedEnvelope
  expiresAtEpochSeconds: number
}

export interface ChallengeRecord {
  key: string
  state: EncryptedEnvelope
  expiresAtEpochSeconds: number
}

/** Receives only keyed digests, never raw browser cookie values. */
export interface SessionRecordStore {
  getSession(key: string): Promise<SessionRecord | null | 'unavailable'>
  putSession(record: SessionRecord): Promise<'stored' | 'conflict' | 'unavailable'>
  replaceSession(
    record: SessionRecord,
    expectedVersion: number,
  ): Promise<'stored' | 'conflict' | 'unavailable'>
  deleteSession(key: string): Promise<'deleted' | 'unavailable'>
  getChallenge(key: string): Promise<ChallengeRecord | null | 'unavailable'>
  putChallenge(record: ChallengeRecord): Promise<'stored' | 'conflict' | 'unavailable'>
  deleteChallenge(key: string): Promise<'deleted' | 'unavailable'>
}

export interface DataKeyProvider {
  generate(context: Readonly<Record<string, string>>): Promise<{
    plaintextKey: Uint8Array
    encryptedKey: Uint8Array
  } | 'unavailable'>
  decrypt(
    encryptedKey: Uint8Array,
    context: Readonly<Record<string, string>>,
  ): Promise<Uint8Array | 'unavailable'>
}

export interface Clock {
  now(): Date
}
