import { CognitoIdentityProviderClient } from '@aws-sdk/client-cognito-identity-provider'
import { DynamoDBClient } from '@aws-sdk/client-dynamodb'
import { KMSClient } from '@aws-sdk/client-kms'
import { DynamoDBDocumentClient } from '@aws-sdk/lib-dynamodb'
import type { BffDependencies } from './contracts'
import { AppApiGateway } from './app-api-gateway'
import {
  CognitoIdentityAdapter,
  DynamoSessionRecordStore,
  KmsDataKeyProvider,
} from './aws-adapters'
import { EnvelopeCrypto } from './envelope'
import { defaultBffDependencies } from './providers'
import { readBffRuntimeConfiguration } from './runtime-config'
import { SecureSessionGateway } from './session-gateway'

let cached: BffDependencies | undefined

/**
 * Production-only wiring. AWS clients use the task/instance role; no static
 * credentials, service API keys, or browser-visible provider tokens exist.
 */
export function getBffDependencies(): BffDependencies {
  if (cached) return cached
  const configuration = readBffRuntimeConfiguration()
  if (!configuration) return defaultBffDependencies

  try {
    const cognitoClient = new CognitoIdentityProviderClient({ region: configuration.region })
    const dynamoClient = DynamoDBDocumentClient.from(new DynamoDBClient({ region: configuration.region }), {
      marshallOptions: { removeUndefinedValues: true },
    })
    const kmsClient = new KMSClient({ region: configuration.region })
    const identity = new CognitoIdentityAdapter(
      { send: (command) => cognitoClient.send(command as never) },
      {
        clientId: configuration.cognitoClientId,
        clientSecret: configuration.cognitoClientSecret,
      },
    )
    const records = new DynamoSessionRecordStore(
      { send: (command) => dynamoClient.send(command as never) },
      configuration.sessionTableName,
    )
    const keys = new KmsDataKeyProvider(
      { send: (command) => kmsClient.send(command as never) },
      configuration.sessionKmsKeyId,
    )
    const appApi = new AppApiGateway(
      configuration.appApiOrigin,
      configuration.browserAssertionSecret,
      fetch,
      configuration.allowLoopbackHttp,
    )
    const sessions = new SecureSessionGateway({
      identity,
      memberships: appApi,
      records,
      crypto: new EnvelopeCrypto(keys),
      cookieHmacSecret: configuration.cookieHmacSecret,
      sessionTtlSeconds: configuration.sessionTtlSeconds,
      challengeTtlSeconds: configuration.challengeTtlSeconds,
      refreshSkewSeconds: configuration.refreshSkewSeconds,
    })
    cached = Object.freeze({ sessions, projects: appApi })
    return cached
  } catch {
    return defaultBffDependencies
  }
}

export function resetBffDependenciesForTests(): void {
  cached = undefined
}
