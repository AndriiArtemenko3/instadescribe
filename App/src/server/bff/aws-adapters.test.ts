import {
  AssociateSoftwareTokenCommand,
  GetUserCommand,
  InitiateAuthCommand,
  RespondToAuthChallengeCommand,
  SetUserMFAPreferenceCommand,
  VerifySoftwareTokenCommand,
} from '@aws-sdk/client-cognito-identity-provider'
import { describe, expect, it, vi } from 'vitest'
import type { ProviderTokens } from './ports'
import { CognitoIdentityAdapter } from './aws-adapters'

const providerTokens: ProviderTokens = {
  username: 'owner@example.com',
  email: 'owner@example.com',
  mfaVerified: false,
  accessToken: 'pre-mfa-access-token',
  refreshToken: 'pre-mfa-refresh-token',
  idToken: 'pre-mfa-id-token',
  accessExpiresAt: '2030-01-01T01:00:00.000Z',
}

describe('CognitoIdentityAdapter owner MFA enrollment', () => {
  it('gets authoritative email from Cognito and starts password auth as non-MFA', async () => {
    const send = vi.fn().mockImplementation(async (command: unknown) => {
      if (command instanceof InitiateAuthCommand) {
        return {
          AuthenticationResult: {
            AccessToken: 'access-from-password',
            RefreshToken: 'refresh-from-password',
            IdToken: 'not-a-source-of-email-or-mfa',
            ExpiresIn: 3600,
          },
        }
      }
      if (command instanceof GetUserCommand) {
        return {
          Username: 'cognito-generated-uuid',
          UserAttributes: [{ Name: 'email', Value: 'Owner@Example.com' }],
        }
      }
      throw new Error('unexpected command')
    })
    const adapter = new CognitoIdentityAdapter(
      { send },
      { clientId: 'client-id', clientSecret: 'client-secret' },
      () => new Date('2030-01-01T00:00:00.000Z'),
    )

    const result = await adapter.authenticate({ email: 'owner@example.com', password: 'password' })

    expect(result).toMatchObject({
      kind: 'tokens',
      tokens: {
        username: 'cognito-generated-uuid',
        email: 'Owner@Example.com',
        mfaVerified: false,
        accessToken: 'access-from-password',
      },
    })
    expect(send).toHaveBeenCalledTimes(2)
    expect((send.mock.calls[1]?.[0] as GetUserCommand).input).toEqual({
      AccessToken: 'access-from-password',
    })
  })

  it('associates a software token with the authenticated access token', async () => {
    const send = vi.fn().mockResolvedValue({ SecretCode: 'TOTP-SETUP-SECRET' })
    const adapter = new CognitoIdentityAdapter(
      { send },
      { clientId: 'client-id', clientSecret: 'client-secret' },
    )

    const result = await adapter.beginMfaEnrollment(providerTokens)

    expect(result).toEqual({
      kind: 'challenge',
      state: {
        flow: 'owner_mfa_enrollment',
        tokens: providerTokens,
        challenge: { type: 'mfa_setup', totpSecret: 'TOTP-SETUP-SECRET' },
      },
    })
    expect(send).toHaveBeenCalledOnce()
    const command = send.mock.calls[0]?.[0]
    expect(command).toBeInstanceOf(AssociateSoftwareTokenCommand)
    expect((command as AssociateSoftwareTokenCommand).input).toEqual({
      AccessToken: 'pre-mfa-access-token',
    })
  })

  it('verifies TOTP, makes it preferred, and returns only a fresh-login requirement', async () => {
    const send = vi.fn().mockImplementation(async (command: unknown) => {
      if (command instanceof VerifySoftwareTokenCommand) return { Status: 'SUCCESS' }
      if (command instanceof SetUserMFAPreferenceCommand) return {}
      throw new Error('unexpected command')
    })
    const adapter = new CognitoIdentityAdapter(
      { send },
      { clientId: 'client-id', clientSecret: 'client-secret' },
    )

    const result = await adapter.continueChallenge({
      flow: 'owner_mfa_enrollment',
      tokens: providerTokens,
      challenge: { type: 'mfa_setup', totpSecret: 'TOTP-SETUP-SECRET' },
    }, { type: 'mfa_setup', code: '123456' })

    expect(result).toEqual({ kind: 'reauthentication_required' })
    expect(send).toHaveBeenCalledTimes(2)
    expect((send.mock.calls[0]?.[0] as VerifySoftwareTokenCommand).input).toEqual({
      AccessToken: 'pre-mfa-access-token',
      UserCode: '123456',
      FriendlyDeviceName: 'InstaDescribe',
    })
    expect((send.mock.calls[1]?.[0] as SetUserMFAPreferenceCommand).input).toEqual({
      AccessToken: 'pre-mfa-access-token',
      SoftwareTokenMfaSettings: { Enabled: true, PreferredMfa: true },
    })
    expect(send.mock.calls.some(([command]) => command instanceof RespondToAuthChallengeCommand)).toBe(false)
  })

  it('preserves Cognito Session-based MFA_SETUP completion', async () => {
    const send = vi.fn().mockImplementation(async (command: unknown) => {
      if (command instanceof VerifySoftwareTokenCommand) {
        return { Status: 'SUCCESS', Session: 'verified-provider-session' }
      }
      if (command instanceof RespondToAuthChallengeCommand) {
        return {
          AuthenticationResult: {
            AccessToken: 'mfa-access-token',
            RefreshToken: 'mfa-refresh-token',
            ExpiresIn: 3600,
          },
        }
      }
      if (command instanceof GetUserCommand) {
        return {
          Username: 'cognito-generated-username',
          UserAttributes: [{ Name: 'email', Value: 'owner@example.com' }],
        }
      }
      throw new Error('unexpected command')
    })
    const adapter = new CognitoIdentityAdapter(
      { send },
      { clientId: 'client-id', clientSecret: 'client-secret' },
      () => new Date('2030-01-01T00:00:00.000Z'),
    )

    const result = await adapter.continueChallenge({
      flow: 'cognito_session',
      username: 'owner@example.com',
      providerSession: 'initial-provider-session',
      challenge: { type: 'mfa_setup', totpSecret: 'TOTP-SETUP-SECRET' },
    }, { type: 'mfa_setup', code: '123456' })

    expect(result).toMatchObject({
      kind: 'tokens',
      tokens: {
        username: 'cognito-generated-username',
        email: 'owner@example.com',
        mfaVerified: true,
        accessToken: 'mfa-access-token',
        refreshToken: 'mfa-refresh-token',
      },
    })
    expect((send.mock.calls[0]?.[0] as VerifySoftwareTokenCommand).input).toMatchObject({
      Session: 'initial-provider-session',
      UserCode: '123456',
    })
    expect((send.mock.calls[1]?.[0] as RespondToAuthChallengeCommand).input).toMatchObject({
      ChallengeName: 'MFA_SETUP',
      Session: 'verified-provider-session',
    })
  })

  it('carries prior MFA evidence through refresh and re-resolves GetUser', async () => {
    const send = vi.fn().mockImplementation(async (command: unknown) => {
      if (command instanceof InitiateAuthCommand) {
        return { AuthenticationResult: { AccessToken: 'refreshed-access', ExpiresIn: 3600 } }
      }
      if (command instanceof GetUserCommand) {
        return {
          Username: 'cognito-generated-username',
          UserAttributes: [{ Name: 'email', Value: 'owner@example.com' }],
        }
      }
      throw new Error('unexpected command')
    })
    const adapter = new CognitoIdentityAdapter(
      { send },
      { clientId: 'client-id', clientSecret: 'client-secret' },
    )

    const result = await adapter.refresh({ ...providerTokens, mfaVerified: true })

    expect(result).toMatchObject({
      kind: 'tokens',
      tokens: {
        email: 'owner@example.com',
        mfaVerified: true,
        accessToken: 'refreshed-access',
        refreshToken: 'pre-mfa-refresh-token',
      },
    })
  })
})
