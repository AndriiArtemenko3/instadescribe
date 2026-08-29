// G7 B1/B7: the portfolio token lives in module memory + sessionStorage
// ONLY, and logout clears it. It never reaches localStorage or Zustand.

import { afterEach, describe, expect, it } from 'vitest'
import {
  clearPortfolioToken,
  getPortfolioToken,
  hasPortfolioToken,
  setPortfolioToken,
} from './portfolioToken'

const SAMPLE = 'test-portfolio-token-value'

afterEach(() => {
  clearPortfolioToken()
  localStorage.clear()
  sessionStorage.clear()
})

describe('portfolioToken', () => {
  it('stores the token in memory and sessionStorage only', () => {
    setPortfolioToken(SAMPLE)
    expect(getPortfolioToken()).toBe(SAMPLE)
    expect(sessionStorage.getItem('instascribe:portfolioToken')).toBe(SAMPLE)
    // NEVER in localStorage.
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i)!
      expect(localStorage.getItem(key)).not.toContain(SAMPLE)
    }
  })

  it('survives a module-memory reset via sessionStorage (refresh continuity)', () => {
    setPortfolioToken(SAMPLE)
    clearPortfolioToken()
    expect(hasPortfolioToken()).toBe(false)
    sessionStorage.setItem('instascribe:portfolioToken', SAMPLE)
    expect(getPortfolioToken()).toBe(SAMPLE) // re-hydrates from sessionStorage
  })

  it('clear removes both memory and sessionStorage', () => {
    setPortfolioToken(SAMPLE)
    clearPortfolioToken()
    expect(hasPortfolioToken()).toBe(false)
    expect(sessionStorage.getItem('instascribe:portfolioToken')).toBeNull()
  })

  it('logout clears the token', async () => {
    const { useAppStore } = await import('@/store/appStore')
    setPortfolioToken(SAMPLE)
    useAppStore.getState().logout()
    expect(hasPortfolioToken()).toBe(false)
  })

  it('the persisted Zustand state never contains the token', async () => {
    const { useAppStore } = await import('@/store/appStore')
    setPortfolioToken(SAMPLE)
    useAppStore.getState().addProject({
      id: 'p-1',
      jobId: 'j-1',
      name: 'x',
      status: 'ready',
      createdAt: new Date().toISOString(),
    })
    const persisted = localStorage.getItem('instascribe-app') ?? ''
    expect(persisted).not.toContain(SAMPLE)
  })
})
