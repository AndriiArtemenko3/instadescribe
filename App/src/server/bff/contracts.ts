/** Safe identity fields available to server-side BFF adapters. */
export interface SessionPrincipal {
  subject: string
  email: string
  displayName: string
  organizationId: string
  /** Human memberships only. Service-account principals are never web sessions. */
  role: 'owner' | 'editor' | 'reviewer' | 'viewer'
  /** Derived by FastAPI from trusted Cognito context, never from browser input. */
  mfaVerified: boolean
}

export interface SignInCredentials {
  email: string
  password: string
}

export interface AuthenticatedSession {
  principal: SessionPrincipal
  /** Server-only bearer token. Handlers must never serialize this value. */
  accessToken: string
}

export type SessionLookup =
  | ({ kind: 'authenticated'; expiresAt: string } & AuthenticatedSession)
  | { kind: 'invalid' }
  | { kind: 'unavailable' }

export type AuthChallenge =
  | { type: 'new_password_required'; requiredAttributes: string[] }
  | { type: 'software_token_mfa' }
  | { type: 'mfa_setup'; totpSecret: string }

export type SignInResult =
  | {
      kind: 'authenticated'
      principal: SessionPrincipal
      opaqueSession: string
      expiresAt: string
    }
  | {
      kind: 'challenge'
      challenge: AuthChallenge
      opaqueChallenge: string
      expiresAt: string
    }
  | { kind: 'invalid_credentials' }
  | { kind: 'unavailable' }

export type ChallengeAnswer =
  | { type: 'new_password_required'; newPassword: string }
  | { type: 'software_token_mfa'; code: string }
  | { type: 'mfa_setup'; code: string }

export type ChallengeResult = SignInResult | { kind: 'invalid_challenge' }
  | { kind: 'reauthentication_required' }

export type ForgotPasswordResult = { kind: 'accepted' } | { kind: 'unavailable' }
export type ResetPasswordResult =
  | { kind: 'reset' }
  | { kind: 'invalid_code' }
  | { kind: 'unavailable' }

export type RevokeResult = { kind: 'revoked' } | { kind: 'unavailable' }
export type MfaEnrollmentResult =
  | Extract<SignInResult, { kind: 'challenge' }>
  | { kind: 'already_enabled' }
  | { kind: 'invalid' }
  | { kind: 'unavailable' }

/**
 * Cognito/Dynamo implementations belong behind this boundary. The browser
 * receives only an opaque HttpOnly cookie, never provider tokens or API keys.
 */
export interface SessionGateway {
  lookup(opaqueSession: string): Promise<SessionLookup>
  signIn(credentials: SignInCredentials): Promise<SignInResult>
  continueChallenge(opaqueChallenge: string, answer: ChallengeAnswer): Promise<ChallengeResult>
  inspectChallenge(opaqueChallenge: string): Promise<AuthChallenge | null | 'unavailable'>
  forgotPassword(email: string): Promise<ForgotPasswordResult>
  resetPassword(email: string, code: string, newPassword: string): Promise<ResetPasswordResult>
  beginMfaEnrollment(opaqueSession: string): Promise<MfaEnrollmentResult>
  revoke(opaqueSession: string): Promise<RevokeResult>
}

export type ProjectSummaryStatus =
  | 'confirmation_pending'
  | 'processing'
  | 'ready'
  | 'draft'
  | 'failed'

export interface ProjectSummary {
  id: string
  orgSlug: string
  currentJobId: string | null
  name: string
  status: ProjectSummaryStatus
  updatedAt: string
}

export type ProjectListResult =
  | { kind: 'ok'; projects: ProjectSummary[] }
  | { kind: 'unavailable' }

/** Metadata-only boundary. Media bytes and signed URLs are intentionally absent. */
export interface ProjectGateway {
  list(session: AuthenticatedSession): Promise<ProjectListResult>
}

export interface BffDependencies {
  sessions: SessionGateway
  projects: ProjectGateway
}
