import type { ReactNode } from 'react'
import { Navigate } from 'react-router-dom'
import { useAppStore } from '@/store/appStore'
import { isStudyMode, isDemoBuild } from '@/lib/session'
import { isCloudMode } from '@/lib/cloudMode'
import { hasPortfolioToken } from '@/lib/portfolioToken'

interface GuardProps {
  children: ReactNode
}

export type GuardDecision = 'allow' | 'login' | 'dashboard'

export function authGuardDecision(
  isAuthenticated: boolean,
  cloud: boolean,
  tokenPresent: boolean,
): GuardDecision {
  if (!isAuthenticated) return 'login'
  if (cloud && !tokenPresent) return 'login'
  return 'allow'
}

export function guestGuardDecision(
  isAuthenticated: boolean,
  cloud: boolean,
  tokenPresent: boolean,
): GuardDecision {
  return isAuthenticated && (!cloud || tokenPresent) ? 'dashboard' : 'allow'
}

export function AuthGuard({ children }: GuardProps) {
  const isAuthenticated = useAppStore((s) => s.isAuthenticated)
  if (isStudyMode() || isDemoBuild()) return <>{children}</>   // study/demo build: no login wall
  if (authGuardDecision(isAuthenticated, isCloudMode(), hasPortfolioToken()) === 'login') {
    return <Navigate to="/login" replace />
  }
  // G7.1 B: a cloud session requires BOTH the demo-login state and a
  // current portfolio token — a restored login with no session token goes
  // back to /login instead of an unusable authenticated shell.
  return <>{children}</>
}

export function GuestGuard({ children }: GuardProps) {
  const isAuthenticated = useAppStore((s) => s.isAuthenticated)
  // G7.1 B: in cloud mode a restored login WITHOUT a token must be able to
  // reach /login again (no GuestGuard trap).
  if (guestGuardDecision(isAuthenticated, isCloudMode(), hasPortfolioToken()) === 'dashboard') {
    return <Navigate to="/dashboard" replace />
  }
  return <>{children}</>
}
