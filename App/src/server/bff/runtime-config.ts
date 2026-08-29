export interface BffRuntimeConfiguration {
  region: string
  userPoolId: string
  cognitoClientId: string
  cognitoClientSecret: string
  sessionTableName: string
  sessionKmsKeyId: string
  cookieHmacSecret: Uint8Array
  browserAssertionSecret: Uint8Array
  appApiOrigin: string
  appOrigin: string
  allowLoopbackHttp: boolean
  sessionTtlSeconds: number
  challengeTtlSeconds: number
  refreshSkewSeconds: number
}

function required(environment: NodeJS.ProcessEnv, key: string, maximum = 4096): string | null {
  const value = environment[key]
  return value && value.length <= maximum ? value : null
}

function seconds(environment: NodeJS.ProcessEnv, key: string, fallback: number, minimum: number, maximum: number): number | null {
  const raw = environment[key]
  if (!raw) return fallback
  if (!/^\d+$/.test(raw)) return null
  const value = Number(raw)
  return Number.isSafeInteger(value) && value >= minimum && value <= maximum ? value : null
}

function hmacSecret(value: string, minimum = 32, maximum = 128): Uint8Array | null {
  if (!/^[A-Za-z0-9_-]+$/.test(value)) return null
  try {
    const decoded = Buffer.from(value, 'base64url')
    if (decoded.toString('base64url') !== value) return null
    return decoded.byteLength >= minimum && decoded.byteLength <= maximum ? decoded : null
  } catch {
    return null
  }
}

function origin(value: string, allowLoopbackHttp: boolean): string | null {
  try {
    const parsed = new URL(value)
    const loopback = parsed.hostname === 'localhost' || parsed.hostname === '127.0.0.1' || parsed.hostname === '::1'
    if (parsed.protocol !== 'https:' && !(allowLoopbackHttp && parsed.protocol === 'http:' && loopback)) return null
    if (parsed.username || parsed.password || parsed.search || parsed.hash || (parsed.pathname !== '/' && parsed.pathname !== '')) {
      return null
    }
    return parsed.origin
  } catch {
    return null
  }
}

/** A partial configuration never boots a weaker adapter; it fails closed. */
export function readBffRuntimeConfiguration(
  environment: NodeJS.ProcessEnv = process.env,
): BffRuntimeConfiguration | null {
  const region = required(environment, 'AWS_REGION', 128)
  const userPoolId = required(environment, 'COGNITO_USER_POOL_ID', 256)
  const cognitoClientId = required(environment, 'COGNITO_APP_CLIENT_ID', 256)
  const cognitoClientSecret = required(environment, 'COGNITO_APP_CLIENT_SECRET')
  const sessionTableName = required(environment, 'WEB_SESSION_TABLE_NAME', 1024)
  const sessionKmsKeyId = required(environment, 'WEB_SESSION_KMS_KEY_ID', 2048)
  const rawHmacSecret = required(environment, 'WEB_SESSION_HMAC_SECRET', 256)
  const rawBrowserAssertionSecret = required(environment, 'BROWSER_ASSERTION_SECRET', 64)
  const rawAppApiOrigin = required(environment, 'APP_API_ORIGIN', 2048)
  const rawAppOrigin = required(environment, 'APP_ORIGIN', 2048)
  const sessionTtlSeconds = seconds(environment, 'WEB_SESSION_TTL_SECONDS', 2_592_000, 300, 2_592_000)
  const challengeTtlSeconds = seconds(environment, 'AUTH_CHALLENGE_TTL_SECONDS', 600, 60, 900)
  const refreshSkewSeconds = seconds(environment, 'ACCESS_TOKEN_REFRESH_SKEW_SECONDS', 60, 0, 300)
  if (
    !region || !userPoolId || !cognitoClientId || !cognitoClientSecret ||
    !sessionTableName || !sessionKmsKeyId || !rawHmacSecret || !rawBrowserAssertionSecret ||
    !rawAppApiOrigin || !rawAppOrigin ||
    sessionTtlSeconds === null || challengeTtlSeconds === null || refreshSkewSeconds === null
  ) return null
  const cookieHmacSecret = hmacSecret(rawHmacSecret)
  const browserAssertionSecret = hmacSecret(rawBrowserAssertionSecret, 32, 32)
  const allowLoopbackHttp = environment.NODE_ENV === 'development'
  const appApiOrigin = origin(rawAppApiOrigin, allowLoopbackHttp)
  const appOrigin = origin(rawAppOrigin, allowLoopbackHttp)
  if (!cookieHmacSecret || !browserAssertionSecret || !appApiOrigin || !appOrigin) return null
  return {
    region,
    userPoolId,
    cognitoClientId,
    cognitoClientSecret,
    sessionTableName,
    sessionKmsKeyId,
    cookieHmacSecret,
    browserAssertionSecret,
    appApiOrigin,
    appOrigin,
    allowLoopbackHttp,
    sessionTtlSeconds,
    challengeTtlSeconds,
    refreshSkewSeconds,
  }
}
