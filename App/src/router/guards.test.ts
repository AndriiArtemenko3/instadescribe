import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DEMO_USER } from '@/features/auth/constants'
import { useAppStore } from '@/store/appStore'
import { authGuardDecision, guestGuardDecision } from './guards'
import {
  clearPortfolioToken,
  getPortfolioToken,
  hasPortfolioToken,
  setPortfolioToken,
} from '@/lib/portfolioToken'
import { validatePortfolioToken } from '@/lib/cloudApi'

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  clearPortfolioToken()
  useAppStore.setState({ isAuthenticated: false, currentUser: null, projects: [] })
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('cloud route/session guards', () => {
  it('valid cloud login reaches protected routes and GuestGuard redirects to dashboard', () => {
    setPortfolioToken('valid')
    expect(useAppStore.getState().login(DEMO_USER.email, DEMO_USER.password)).toBe(true)
    expect(authGuardDecision(true, true, hasPortfolioToken())).toBe('allow')
    expect(guestGuardDecision(true, true, hasPortfolioToken())).toBe('dashboard')
  })

  it('wrong token never creates auth/token state and wrong-token re-entry remains on login', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', {
      status: 401, headers: { 'Content-Type': 'application/json' },
    })))
    expect(await validatePortfolioToken('wrong')).toBe(false)
    expect(getPortfolioToken()).toBeNull()
    expect(useAppStore.getState().isAuthenticated).toBe(false)
    expect(guestGuardDecision(false, true, false)).toBe('allow')
  })

  it('restored auth without a token is sent to login and not trapped by GuestGuard', () => {
    useAppStore.setState({ isAuthenticated: true })
    expect(authGuardDecision(true, true, false)).toBe('login')
    expect(guestGuardDecision(true, true, false)).toBe('allow')
  })

  it('session token refresh continuity keeps the route allowed', () => {
    sessionStorage.setItem('instascribe:portfolioToken', 'restored')
    expect(getPortfolioToken()).toBe('restored')
    expect(authGuardDecision(true, true, hasPortfolioToken())).toBe('allow')
  })

  it('logout revokes route access and allows token re-entry', () => {
    setPortfolioToken('valid')
    useAppStore.setState({ isAuthenticated: true })
    useAppStore.getState().logout()
    expect(hasPortfolioToken()).toBe(false)
    expect(authGuardDecision(false, true, false)).toBe('login')
    expect(guestGuardDecision(false, true, false)).toBe('allow')
  })
})
