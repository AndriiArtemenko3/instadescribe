// Portfolio access token (G7, ADR-0006): ONE portfolio-wide access token —
// not customer authentication and not multi-tenancy.
//
// The plaintext token lives ONLY in module memory plus sessionStorage (for
// refresh continuity). It is never placed in Zustand, localStorage, a URL or
// query string, a Vite variable, logs, analytics, error messages, or
// committed fixtures. Only cloudApi.ts attaches it, and only to protected
// API-relative paths — never to S3 or signed artifact URLs.

// Stable legacy key for the `/api/v1` portfolio-token rollback contract.
const SESSION_KEY = 'instascribe:portfolioToken'

let tokenInMemory: string | null = null
let sessionGeneration = 0

/** Opaque identity for one accepted token session. It deliberately contains
    no token bytes and is never persisted. */
export interface PortfolioSessionIdentity {
  readonly generation: number
}

let sessionIdentity: PortfolioSessionIdentity | null = null

function beginSession(token: string): void {
  tokenInMemory = token
  sessionGeneration += 1
  sessionIdentity = Object.freeze({ generation: sessionGeneration })
}

export function setPortfolioToken(token: string): void {
  beginSession(token)
  try {
    sessionStorage.setItem(SESSION_KEY, token)
  } catch {
    /* sessionStorage unavailable: memory-only still works for this tab */
  }
}

export function getPortfolioToken(): string | null {
  if (tokenInMemory) return tokenInMemory
  try {
    const restored = sessionStorage.getItem(SESSION_KEY)
    if (restored) beginSession(restored)
  } catch {
    tokenInMemory = null
    sessionIdentity = null
  }
  return tokenInMemory
}

export function hasPortfolioToken(): boolean {
  return !!getPortfolioToken()
}

/** Capture the exact current in-memory session without exposing its token. */
export function getPortfolioSessionIdentity(): PortfolioSessionIdentity | null {
  if (!getPortfolioToken()) return null
  return sessionIdentity
}

export function isCurrentPortfolioSession(identity: PortfolioSessionIdentity): boolean {
  return sessionIdentity === identity && tokenInMemory !== null
}

export function clearPortfolioToken(): void {
  tokenInMemory = null
  sessionIdentity = null
  sessionGeneration += 1
  try {
    sessionStorage.removeItem(SESSION_KEY)
  } catch {
    /* nothing to clear */
  }
}
