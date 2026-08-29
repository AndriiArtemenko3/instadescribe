import { describe, expect, it } from 'vitest'
import { readBffRuntimeConfiguration } from './runtime-config'

const complete = {
  AWS_REGION: 'eu-west-2',
  COGNITO_USER_POOL_ID: 'eu-west-2_pool',
  COGNITO_APP_CLIENT_ID: 'client-id',
  COGNITO_APP_CLIENT_SECRET: 'client-secret',
  WEB_SESSION_TABLE_NAME: 'beta-web-sessions',
  WEB_SESSION_KMS_KEY_ID: 'alias/beta-web-sessions',
  WEB_SESSION_HMAC_SECRET: Buffer.alloc(32, 4).toString('base64url'),
  BROWSER_ASSERTION_SECRET: Buffer.alloc(32, 5).toString('base64url'),
  APP_API_ORIGIN: 'https://api.example',
  APP_ORIGIN: 'https://app.example',
  NODE_ENV: 'production',
} satisfies NodeJS.ProcessEnv

describe('BFF runtime configuration', () => {
  it('fails closed when any required secret or resource binding is absent', () => {
    const { COGNITO_APP_CLIENT_SECRET: _, ...missingSecret } = complete
    expect(readBffRuntimeConfiguration(missingSecret)).toBeNull()
    expect(readBffRuntimeConfiguration({ ...complete, WEB_SESSION_HMAC_SECRET: 'short' })).toBeNull()
    expect(readBffRuntimeConfiguration({ ...complete, BROWSER_ASSERTION_SECRET: Buffer.alloc(31).toString('base64url') })).toBeNull()
  })

  it('loads bounded lifetimes only from a complete configuration', () => {
    expect(readBffRuntimeConfiguration(complete)).toMatchObject({
      region: 'eu-west-2',
      sessionTtlSeconds: 2_592_000,
      challengeTtlSeconds: 600,
      refreshSkewSeconds: 60,
      allowLoopbackHttp: false,
    })
    expect(readBffRuntimeConfiguration({ ...complete, WEB_SESSION_TTL_SECONDS: '999999999' })).toBeNull()
    expect(readBffRuntimeConfiguration({ ...complete, APP_ORIGIN: 'https://app.example/path' })).toBeNull()
  })
})
