import { describe, expect, it } from 'vitest'
import {
  BROWSER_ASSERTION_HEADER,
  canonicalBrowserEmail,
  createBrowserAssertion,
} from './browser-assertion'

describe('browser API assertion', () => {
  it('has a stable exact wire format bound to token, identity, MFA, and timestamp', () => {
    const assertion = createBrowserAssertion(
      new Uint8Array(32).fill(7),
      'cognito-access-token',
      { email: ' Owner@Example.COM ', mfaVerified: true },
      1_900_000_000,
    )

    expect(BROWSER_ASSERTION_HEADER).toBe('X-InstaDescribe-Browser-Assertion')
    expect(assertion).toBe(
      'v1.1900000000.1.b3duZXJAZXhhbXBsZS5jb20.cxUw7XNzzdPTCNevcH7XCG_y1wgjoquoFRYKXHA8avU',
    )
  })

  it('fails closed for malformed email, token, secret, or timestamp', () => {
    const secret = new Uint8Array(32).fill(7)
    expect(canonicalBrowserEmail('OWNER@Example.com')).toBe('owner@example.com')
    expect(canonicalBrowserEmail('owner@@example.com')).toBeNull()
    expect(canonicalBrowserEmail('owner\n@example.com')).toBeNull()
    expect(createBrowserAssertion(new Uint8Array(31), 'token', {
      email: 'owner@example.com', mfaVerified: false,
    }, 1)).toBeNull()
    expect(createBrowserAssertion(secret, '', {
      email: 'owner@example.com', mfaVerified: false,
    }, 1)).toBeNull()
    expect(createBrowserAssertion(secret, 'token', {
      email: 'owner@example.com', mfaVerified: false,
    }, 0)).toBeNull()
  })
})
