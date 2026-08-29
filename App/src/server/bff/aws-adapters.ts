import { createHmac } from 'node:crypto'
import {
  AssociateSoftwareTokenCommand,
  ConfirmForgotPasswordCommand,
  ForgotPasswordCommand,
  GetUserCommand,
  GlobalSignOutCommand,
  InitiateAuthCommand,
  RespondToAuthChallengeCommand,
  RevokeTokenCommand,
  SetUserMFAPreferenceCommand,
  VerifySoftwareTokenCommand,
} from '@aws-sdk/client-cognito-identity-provider'
import { DecryptCommand, GenerateDataKeyCommand } from '@aws-sdk/client-kms'
import {
  DeleteCommand,
  GetCommand,
  PutCommand,
} from '@aws-sdk/lib-dynamodb'
import type { ChallengeAnswer, SignInCredentials } from './contracts'
import type {
  ChallengeRecord,
  DataKeyProvider,
  EncryptedEnvelope,
  IdentityProviderPort,
  ProviderAuthenticationResult,
  ProviderChallengeState,
  ProviderTokens,
  SessionRecord,
  SessionRecordStore,
} from './ports'

interface CommandSender {
  send(command: unknown): Promise<unknown>
}

interface CognitoConfiguration {
  clientId: string
  clientSecret: string
}

interface AuthResponse {
  AuthenticationResult?: {
    AccessToken?: string
    RefreshToken?: string
    IdToken?: string
    ExpiresIn?: number
  }
  ChallengeName?: string
  ChallengeParameters?: Record<string, string>
  Session?: string
}

function errorName(error: unknown): string {
  return error !== null && typeof error === 'object' && 'name' in error
    ? String((error as { name: unknown }).name)
    : ''
}

function requiredAttributes(parameters: Record<string, string> | undefined): string[] {
  const raw = parameters?.requiredAttributes
  if (!raw) return []
  try {
    const parsed = JSON.parse(raw) as unknown
    if (!Array.isArray(parsed)) return []
    return parsed
      .filter((value): value is string => typeof value === 'string' && value.length <= 128)
      .map((value) => value.replace(/^userAttributes\./, ''))
      .slice(0, 32)
  } catch {
    return []
  }
}

function challengeUsername(response: AuthResponse, fallback: string): string {
  const candidate = response.ChallengeParameters?.USER_ID_FOR_SRP ?? response.ChallengeParameters?.USERNAME
  return typeof candidate === 'string' && candidate.length > 0 && candidate.length <= 254
    ? candidate
    : fallback
}

export class CognitoIdentityAdapter implements IdentityProviderPort {
  constructor(
    private readonly client: CommandSender,
    private readonly configuration: CognitoConfiguration,
    private readonly now: () => Date = () => new Date(),
  ) {}

  async authenticate(credentials: SignInCredentials): Promise<ProviderAuthenticationResult> {
    try {
      const response = await this.client.send(new InitiateAuthCommand({
        AuthFlow: 'USER_PASSWORD_AUTH',
        ClientId: this.configuration.clientId,
        AuthParameters: {
          USERNAME: credentials.email,
          PASSWORD: credentials.password,
          SECRET_HASH: this.secretHash(credentials.email),
        },
      })) as AuthResponse
      return this.normalize(response, credentials.email)
    } catch (error) {
      const name = errorName(error)
      if (name === 'NotAuthorizedException' || name === 'UserNotFoundException') {
        return { kind: 'invalid_credentials' }
      }
      return { kind: 'unavailable' }
    }
  }

  async beginMfaEnrollment(tokens: ProviderTokens): Promise<ProviderAuthenticationResult> {
    try {
      const associated = await this.client.send(new AssociateSoftwareTokenCommand({
        AccessToken: tokens.accessToken,
      })) as { SecretCode?: string }
      if (!associated.SecretCode) return { kind: 'unavailable' }
      return {
        kind: 'challenge',
        state: {
          flow: 'owner_mfa_enrollment',
          tokens,
          challenge: { type: 'mfa_setup', totpSecret: associated.SecretCode },
        },
      }
    } catch {
      return { kind: 'unavailable' }
    }
  }

  async continueChallenge(
    state: ProviderChallengeState,
    answer: ChallengeAnswer,
  ): Promise<ProviderAuthenticationResult> {
    if (state.challenge.type !== answer.type) return { kind: 'invalid_challenge' }
    if (state.flow === 'owner_mfa_enrollment') {
      if (answer.type !== 'mfa_setup') return { kind: 'invalid_challenge' }
      try {
        const verified = await this.client.send(new VerifySoftwareTokenCommand({
          AccessToken: state.tokens.accessToken,
          UserCode: answer.code,
          FriendlyDeviceName: 'InstaDescribe',
        })) as { Status?: string }
        if (verified.Status !== 'SUCCESS') return { kind: 'invalid_challenge' }
        await this.client.send(new SetUserMFAPreferenceCommand({
          AccessToken: state.tokens.accessToken,
          SoftwareTokenMfaSettings: { Enabled: true, PreferredMfa: true },
        }))
        return { kind: 'reauthentication_required' }
      } catch (error) {
        const name = errorName(error)
        if (
          name === 'CodeMismatchException' ||
          name === 'ExpiredCodeException' ||
          name === 'NotAuthorizedException'
        ) return { kind: 'invalid_challenge' }
        return { kind: 'unavailable' }
      }
    }
    try {
      let providerSession = state.providerSession
      let challengeName: 'NEW_PASSWORD_REQUIRED' | 'SOFTWARE_TOKEN_MFA' | 'MFA_SETUP'
      let responses: Record<string, string>
      if (answer.type === 'new_password_required') {
        challengeName = 'NEW_PASSWORD_REQUIRED'
        responses = {
          USERNAME: state.username,
          NEW_PASSWORD: answer.newPassword,
          SECRET_HASH: this.secretHash(state.username),
        }
      } else if (answer.type === 'software_token_mfa') {
        challengeName = 'SOFTWARE_TOKEN_MFA'
        responses = {
          USERNAME: state.username,
          SOFTWARE_TOKEN_MFA_CODE: answer.code,
          SECRET_HASH: this.secretHash(state.username),
        }
      } else {
        const verified = await this.client.send(new VerifySoftwareTokenCommand({
          Session: providerSession,
          UserCode: answer.code,
          FriendlyDeviceName: 'InstaDescribe',
        })) as { Status?: string; Session?: string }
        if (verified.Status !== 'SUCCESS' || !verified.Session) return { kind: 'invalid_challenge' }
        providerSession = verified.Session
        challengeName = 'MFA_SETUP'
        responses = {
          USERNAME: state.username,
          SECRET_HASH: this.secretHash(state.username),
        }
      }
      const response = await this.client.send(new RespondToAuthChallengeCommand({
        ClientId: this.configuration.clientId,
        ChallengeName: challengeName,
        ChallengeResponses: responses,
        Session: providerSession,
      })) as AuthResponse
      return this.normalize(response, state.username, null, challengeName !== 'NEW_PASSWORD_REQUIRED')
    } catch (error) {
      const name = errorName(error)
      if (
        name === 'CodeMismatchException' ||
        name === 'ExpiredCodeException' ||
        name === 'NotAuthorizedException' ||
        name === 'InvalidPasswordException'
      ) return { kind: 'invalid_challenge' }
      return { kind: 'unavailable' }
    }
  }

  async refresh(tokens: ProviderTokens): Promise<ProviderAuthenticationResult> {
    if (!tokens.refreshToken) return { kind: 'invalid_credentials' }
    try {
      const response = await this.client.send(new InitiateAuthCommand({
        AuthFlow: 'REFRESH_TOKEN_AUTH',
        ClientId: this.configuration.clientId,
        AuthParameters: {
          REFRESH_TOKEN: tokens.refreshToken,
          SECRET_HASH: this.secretHash(tokens.username),
        },
      })) as AuthResponse
      return this.normalize(response, tokens.username, tokens.refreshToken, tokens.mfaVerified)
    } catch (error) {
      return errorName(error) === 'NotAuthorizedException'
        ? { kind: 'invalid_credentials' }
        : { kind: 'unavailable' }
    }
  }

  async forgotPassword(email: string): Promise<'accepted' | 'unavailable'> {
    try {
      await this.client.send(new ForgotPasswordCommand({
        ClientId: this.configuration.clientId,
        Username: email,
        SecretHash: this.secretHash(email),
      }))
      return 'accepted'
    } catch (error) {
      // UserNotFound remains indistinguishable from an existing account.
      return [
        'UserNotFoundException',
        'InvalidParameterException',
        'LimitExceededException',
      ].includes(errorName(error)) ? 'accepted' : 'unavailable'
    }
  }

  async resetPassword(
    email: string,
    code: string,
    newPassword: string,
  ): Promise<'reset' | 'invalid_code' | 'unavailable'> {
    try {
      await this.client.send(new ConfirmForgotPasswordCommand({
        ClientId: this.configuration.clientId,
        Username: email,
        ConfirmationCode: code,
        Password: newPassword,
        SecretHash: this.secretHash(email),
      }))
      return 'reset'
    } catch (error) {
      const name = errorName(error)
      return name === 'CodeMismatchException' || name === 'ExpiredCodeException' ||
        name === 'UserNotFoundException' || name === 'NotAuthorizedException'
        ? 'invalid_code'
        : 'unavailable'
    }
  }

  async revoke(tokens: ProviderTokens): Promise<'revoked' | 'unavailable'> {
    let confirmed = true
    if (tokens.refreshToken) {
      try {
        await this.client.send(new RevokeTokenCommand({
          ClientId: this.configuration.clientId,
          ClientSecret: this.configuration.clientSecret,
          Token: tokens.refreshToken,
        }))
      } catch {
        confirmed = false
      }
    }
    try {
      await this.client.send(new GlobalSignOutCommand({ AccessToken: tokens.accessToken }))
    } catch {
      confirmed = false
    }
    return confirmed ? 'revoked' : 'unavailable'
  }

  private async normalize(
    response: AuthResponse,
    username: string,
    refreshFallback: string | null = null,
    mfaVerified = false,
  ): Promise<ProviderAuthenticationResult> {
    const result = response.AuthenticationResult
    if (result?.AccessToken) {
      let user: { Username?: string; UserAttributes?: Array<{ Name?: string; Value?: string }> }
      try {
        user = await this.client.send(new GetUserCommand({ AccessToken: result.AccessToken })) as typeof user
      } catch {
        return { kind: 'unavailable' }
      }
      const providerUsername = user.Username
      const email = user.UserAttributes?.find((attribute) => attribute.Name === 'email')?.Value
      if (
        !providerUsername || providerUsername.length > 254 ||
        !email || email.length > 254 || !email.includes('@')
      ) return { kind: 'unavailable' }
      return {
        kind: 'tokens',
        tokens: {
          username: providerUsername,
          email,
          mfaVerified,
          accessToken: result.AccessToken,
          refreshToken: result.RefreshToken ?? refreshFallback,
          idToken: result.IdToken ?? null,
          accessExpiresAt: new Date(this.now().getTime() + Math.max(60, result.ExpiresIn ?? 3600) * 1000).toISOString(),
        },
      }
    }
    if (!response.ChallengeName || !response.Session) return { kind: 'unavailable' }
    const providerUsername = challengeUsername(response, username)
    if (response.ChallengeName === 'NEW_PASSWORD_REQUIRED') {
      return {
        kind: 'challenge',
        state: {
          flow: 'cognito_session',
          username: providerUsername,
          providerSession: response.Session,
          challenge: {
            type: 'new_password_required',
            requiredAttributes: requiredAttributes(response.ChallengeParameters),
          },
        },
      }
    }
    if (response.ChallengeName === 'SOFTWARE_TOKEN_MFA') {
      return {
        kind: 'challenge',
        state: {
          flow: 'cognito_session',
          username: providerUsername,
          providerSession: response.Session,
          challenge: { type: 'software_token_mfa' },
        },
      }
    }
    if (response.ChallengeName === 'MFA_SETUP') {
      try {
        const associated = await this.client.send(new AssociateSoftwareTokenCommand({
          Session: response.Session,
        })) as { SecretCode?: string; Session?: string }
        if (!associated.SecretCode || !associated.Session) return { kind: 'unavailable' }
        return {
          kind: 'challenge',
          state: {
            flow: 'cognito_session',
            username: providerUsername,
            providerSession: associated.Session,
            challenge: { type: 'mfa_setup', totpSecret: associated.SecretCode },
          },
        }
      } catch {
        return { kind: 'unavailable' }
      }
    }
    return { kind: 'unavailable' }
  }

  private secretHash(username: string): string {
    return createHmac('sha256', this.configuration.clientSecret)
      .update(username)
      .update(this.configuration.clientId)
      .digest('base64')
  }
}

function positiveInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value > 0
}

function stringValue(value: unknown, maximum = 65_536): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= maximum
}

function envelope(value: unknown): EncryptedEnvelope | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const item = value as Partial<EncryptedEnvelope>
  if (
    item.version !== 1 ||
    !stringValue(item.encryptedKey) ||
    !stringValue(item.iv) ||
    !stringValue(item.ciphertext) ||
    !stringValue(item.authTag)
  ) return null
  return {
    version: 1,
    encryptedKey: item.encryptedKey,
    iv: item.iv,
    ciphertext: item.ciphertext,
    authTag: item.authTag,
  }
}

export class DynamoSessionRecordStore implements SessionRecordStore {
  constructor(private readonly client: CommandSender, private readonly tableName: string) {}

  async getSession(key: string): Promise<SessionRecord | null | 'unavailable'> {
    try {
      const response = await this.client.send(new GetCommand({
        TableName: this.tableName,
        Key: { session_id: key },
        ConsistentRead: true,
      })) as { Item?: Record<string, unknown> }
      const item = response.Item
      if (!item) return null
      const encryptedTokens = envelope(item.encrypted_tokens)
      if (
        item.record_type !== 'session' ||
        item.session_id !== key ||
        !positiveInteger(item.version) ||
        !positiveInteger(item.expires_at) ||
        !encryptedTokens
      ) return 'unavailable'
      return {
        key,
        version: item.version,
        expiresAtEpochSeconds: item.expires_at,
        encryptedTokens,
      }
    } catch {
      return 'unavailable'
    }
  }

  async putSession(record: SessionRecord): Promise<'stored' | 'conflict' | 'unavailable'> {
    return this.put(this.sessionItem(record), 'attribute_not_exists(session_id)')
  }

  async replaceSession(
    record: SessionRecord,
    expectedVersion: number,
  ): Promise<'stored' | 'conflict' | 'unavailable'> {
    return this.put(
      this.sessionItem(record),
      '#record_type = :record_type AND #version = :expected_version',
      { '#record_type': 'record_type', '#version': 'version' },
      { ':record_type': 'session', ':expected_version': expectedVersion },
    )
  }

  async deleteSession(key: string): Promise<'deleted' | 'unavailable'> {
    return this.remove(key)
  }

  async getChallenge(key: string): Promise<ChallengeRecord | null | 'unavailable'> {
    try {
      const response = await this.client.send(new GetCommand({
        TableName: this.tableName,
        Key: { session_id: key },
        ConsistentRead: true,
      })) as { Item?: Record<string, unknown> }
      const item = response.Item
      if (!item) return null
      const state = envelope(item.challenge_state)
      if (item.record_type !== 'challenge' || item.session_id !== key || !positiveInteger(item.expires_at) || !state) {
        return 'unavailable'
      }
      return { key, state, expiresAtEpochSeconds: item.expires_at }
    } catch {
      return 'unavailable'
    }
  }

  async putChallenge(record: ChallengeRecord): Promise<'stored' | 'conflict' | 'unavailable'> {
    return this.put({
      session_id: record.key,
      record_type: 'challenge',
      challenge_state: record.state,
      expires_at: record.expiresAtEpochSeconds,
    }, 'attribute_not_exists(session_id)')
  }

  async deleteChallenge(key: string): Promise<'deleted' | 'unavailable'> {
    return this.remove(key)
  }

  private sessionItem(record: SessionRecord): Record<string, unknown> {
    return {
      session_id: record.key,
      record_type: 'session',
      version: record.version,
      encrypted_tokens: record.encryptedTokens,
      expires_at: record.expiresAtEpochSeconds,
    }
  }

  private async put(
    item: Record<string, unknown>,
    condition: string,
    names?: Record<string, string>,
    values?: Record<string, unknown>,
  ): Promise<'stored' | 'conflict' | 'unavailable'> {
    try {
      await this.client.send(new PutCommand({
        TableName: this.tableName,
        Item: item,
        ConditionExpression: condition,
        ExpressionAttributeNames: names,
        ExpressionAttributeValues: values,
      }))
      return 'stored'
    } catch (error) {
      return errorName(error) === 'ConditionalCheckFailedException' ? 'conflict' : 'unavailable'
    }
  }

  private async remove(key: string): Promise<'deleted' | 'unavailable'> {
    try {
      await this.client.send(new DeleteCommand({
        TableName: this.tableName,
        Key: { session_id: key },
      }))
      return 'deleted'
    } catch {
      return 'unavailable'
    }
  }
}

export class KmsDataKeyProvider implements DataKeyProvider {
  constructor(private readonly client: CommandSender, private readonly keyId: string) {}

  async generate(context: Readonly<Record<string, string>>) {
    try {
      const response = await this.client.send(new GenerateDataKeyCommand({
        KeyId: this.keyId,
        KeySpec: 'AES_256',
        EncryptionContext: { ...context },
      })) as { Plaintext?: Uint8Array; CiphertextBlob?: Uint8Array }
      if (!response.Plaintext || !response.CiphertextBlob) return 'unavailable' as const
      return { plaintextKey: response.Plaintext, encryptedKey: response.CiphertextBlob }
    } catch {
      return 'unavailable' as const
    }
  }

  async decrypt(encryptedKey: Uint8Array, context: Readonly<Record<string, string>>) {
    try {
      const response = await this.client.send(new DecryptCommand({
        KeyId: this.keyId,
        CiphertextBlob: encryptedKey,
        EncryptionContext: { ...context },
      })) as { Plaintext?: Uint8Array }
      return response.Plaintext ?? 'unavailable' as const
    } catch {
      return 'unavailable' as const
    }
  }
}
