import { afterEach, describe, expect, it, vi } from 'vitest'
import { legacyApiBase, publicApiBaseOverride, publicFlag } from './runtimeEnv'

afterEach(() => {
  vi.unstubAllEnvs()
})

describe('public runtime environment boundary', () => {
  it('preserves an explicitly empty Vite API base', () => {
    vi.stubEnv('VITE_API_BASE', '')
    vi.stubEnv('VITE_CLOUD_MODE', '1')

    expect(publicApiBaseOverride()).toBe('')
    expect(legacyApiBase()).toBe('')
    expect(publicFlag('cloudMode')).toBe(true)
  })

  it('gives allowlisted Next public values precedence', () => {
    vi.stubEnv('VITE_API_BASE', 'https://vite.example')
    vi.stubEnv('NEXT_PUBLIC_API_BASE', 'https://next.example')
    vi.stubEnv('NEXT_PUBLIC_DEMO_MODE', '1')

    expect(publicApiBaseOverride()).toBe('https://next.example')
    expect(publicFlag('demoMode')).toBe(true)
  })

  it('uses legacy loopback only when neither build defines a base', () => {
    vi.stubEnv('VITE_API_BASE', undefined)
    vi.stubEnv('NEXT_PUBLIC_API_BASE', undefined)

    expect(publicApiBaseOverride()).toBeUndefined()
    expect(legacyApiBase()).toBe('http://localhost:8765')
  })
})
