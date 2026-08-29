import { afterEach, describe, expect, it, vi } from 'vitest'
import { sameOrigin } from './http'

afterEach(() => vi.unstubAllEnvs())

describe('fixed origin validation', () => {
  it('does not trust the request Host as the production CSRF authority', () => {
    vi.stubEnv('NODE_ENV', 'production')
    vi.stubEnv('APP_ORIGIN', undefined)
    const request = new Request('https://attacker.example/api/bff/session', {
      headers: { Origin: 'https://attacker.example' },
    })
    expect(sameOrigin(request)).toBe(false)
  })

  it('accepts only the configured HTTPS application origin', () => {
    vi.stubEnv('NODE_ENV', 'production')
    vi.stubEnv('APP_ORIGIN', 'https://app.example')
    expect(sameOrigin(new Request('https://internal-host/api/bff/session', {
      headers: { Origin: 'https://app.example' },
    }))).toBe(true)
    expect(sameOrigin(new Request('https://app.example/api/bff/session', {
      headers: { Origin: 'https://other.example' },
    }))).toBe(false)
  })
})
