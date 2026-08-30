import { createHash, createHmac } from 'node:crypto'

export const BROWSER_ASSERTION_HEADER = 'X-InstaDescribe-Browser-Assertion'

export interface BrowserAssertionIdentity {
  email: string
  mfaVerified: boolean
}

export function canonicalBrowserEmail(value: string): string | null {
  const email = value.trim().toLowerCase()
  if (
    email.length < 3 ||
    Buffer.byteLength(email, 'utf8') > 254 ||
    !/^[\x21-\x7E]+$/.test(email)
  ) return null
  const separator = email.indexOf('@')
  if (separator < 1 || separator !== email.lastIndexOf('@') || separator === email.length - 1) return null
  return email
}

export function createBrowserAssertion(
  secret: Uint8Array,
  accessToken: string,
  identity: BrowserAssertionIdentity,
  nowEpochSeconds = Math.floor(Date.now() / 1000),
): string | null {
  if (secret.byteLength !== 32 || accessToken.length < 1 || accessToken.length > 32_768) return null
  if (!Number.isSafeInteger(nowEpochSeconds) || nowEpochSeconds < 1) return null
  const email = canonicalBrowserEmail(identity.email)
  if (!email) return null
  const mfa = identity.mfaVerified ? '1' : '0'
  const tokenDigest = createHash('sha256').update(accessToken, 'utf8').digest('base64url')
  const message = `v1\n${nowEpochSeconds}\n${tokenDigest}\n${email}\n${mfa}`
  const signature = createHmac('sha256', secret).update(message, 'utf8').digest('base64url')
  const encodedEmail = Buffer.from(email, 'utf8').toString('base64url')
  return `v1.${nowEpochSeconds}.${mfa}.${encodedEmail}.${signature}`
}
