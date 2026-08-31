import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getSessionId } from './session'

const SESSION_KEY = 'instascribe:studySessionId'
const EXISTING_UUID = ['12345678', '1234', '4123', '8123', '123456789abc'].join('-')
const ROTATED_UUID = ['aaaaaaaa', 'aaaa', '4aaa', '8aaa', 'aaaaaaaaaaaa'].join('-')
const RANDOM_BYTES_UUID = ['00010203', '0405', '4607', '8809', '0a0b0c0d0e0f'].join('-')
const LEGACY_WEAK_SESSION_ID = ['s', '1725123456789', '1234abcd'].join('-')
const sessionId = (uuid: string) => `s-${uuid}`

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  localStorage.clear()
})

describe('getSessionId', () => {
  it('uses Web Crypto randomUUID once and persists the anonymous id', () => {
    const randomUUID = vi.fn(() => EXISTING_UUID)
    vi.stubGlobal('crypto', { randomUUID })

    expect(getSessionId()).toBe(sessionId(EXISTING_UUID))
    expect(getSessionId()).toBe(sessionId(EXISTING_UUID))
    expect(localStorage.getItem(SESSION_KEY)).toBe(sessionId(EXISTING_UUID))
    expect(randomUUID).toHaveBeenCalledTimes(1)
  })

  it('preserves an existing secure session id without requesting new randomness', () => {
    const randomUUID = vi.fn(() => ROTATED_UUID)
    vi.stubGlobal('crypto', { randomUUID })
    localStorage.setItem(SESSION_KEY, sessionId(EXISTING_UUID))

    expect(getSessionId()).toBe(sessionId(EXISTING_UUID))
    expect(randomUUID).not.toHaveBeenCalled()
  })

  it('rotates a persisted legacy weak id using Web Crypto', () => {
    const randomUUID = vi.fn(() => ROTATED_UUID)
    vi.stubGlobal('crypto', { randomUUID })
    localStorage.setItem(SESSION_KEY, LEGACY_WEAK_SESSION_ID)

    expect(getSessionId()).toBe(sessionId(ROTATED_UUID))
    expect(localStorage.getItem(SESSION_KEY)).toBe(sessionId(ROTATED_UUID))
    expect(randomUUID).toHaveBeenCalledTimes(1)
  })

  it('rotates an uppercase UUID that the server rejects as noncanonical', () => {
    const randomUUID = vi.fn(() => ROTATED_UUID)
    vi.stubGlobal('crypto', { randomUUID })
    localStorage.setItem(SESSION_KEY, sessionId(EXISTING_UUID.toUpperCase()))

    expect(getSessionId()).toBe(sessionId(ROTATED_UUID))
    expect(localStorage.getItem(SESSION_KEY)).toBe(sessionId(ROTATED_UUID))
    expect(randomUUID).toHaveBeenCalledTimes(1)
  })

  it('uses getRandomValues when randomUUID is unavailable', () => {
    const getRandomValues = vi.fn((bytes: Uint8Array) => {
      bytes.set(Array.from({ length: 16 }, (_, index) => index))
      return bytes
    })
    vi.stubGlobal('crypto', { getRandomValues })

    expect(getSessionId()).toBe(sessionId(RANDOM_BYTES_UUID))
    expect(getRandomValues).toHaveBeenCalledTimes(1)
  })

  it('fails closed without Web Crypto and never calls weak randomness', () => {
    const weakRandom = vi.spyOn(Math, 'random')
    vi.stubGlobal('crypto', undefined)

    expect(() => getSessionId()).toThrow('Secure random identifier generation is unavailable')
    expect(localStorage.getItem(SESSION_KEY)).toBeNull()
    expect(weakRandom).not.toHaveBeenCalled()
  })
})
