import { test as base, expect, type Page } from '@playwright/test'

// Global observation for the whole browser suite: every page in every test is
// watched for external-origin requests, /api/ paths, page errors and
// unexpected console errors; any hit fails the test in teardown. (The
// local-only browser-speech rule cannot be observed as a page request —
// speech services don't surface here — so it is enforced separately by the
// localVoice unit tests and the honest-unavailable e2e assertions.)

export const DEMO_ORIGIN = 'http://localhost:4174'

// No global allowances: tests that INTENTIONALLY trigger 404s or aborted
// requests declare it via observation.allow(...). Everything else fails —
// including same-origin 4xx/5xx responses and failed requests.
export interface Observation {
  external: string[]
  api: string[]
  consoleErrors: string[]
  pageErrors: string[]
  badResponses: string[]
  failedRequests: string[]
  /** Scope an expected pattern (status-URL, console text, failure) to THIS test. */
  allow: (pattern: RegExp) => void
}

export function observePage(page: Page): Observation {
  const allowed: RegExp[] = []
  const ok = (text: string) => allowed.some((re) => re.test(text))
  const obs: Observation = {
    external: [],
    api: [],
    consoleErrors: [],
    pageErrors: [],
    badResponses: [],
    failedRequests: [],
    allow: (pattern) => allowed.push(pattern),
  }
  page.on('request', (r) => {
    const u = new URL(r.url())
    // Non-network schemes (the CSP test's data: parent page) cannot leave the machine.
    if (!/^https?:$/.test(u.protocol)) return
    if (u.origin !== DEMO_ORIGIN) obs.external.push(r.url())
    if (u.pathname.includes('/api/')) obs.api.push(r.url())
  })
  page.on('response', (r) => {
    const label = `${r.status()} ${r.url()}`
    if (r.status() >= 400 && !ok(label)) obs.badResponses.push(label)
  })
  page.on('requestfailed', (r) => {
    const errorText = r.failure()?.errorText ?? 'failed'
    // net::ERR_ABORTED is the browser cancelling its own speculative media
    // fetches (video preload/range churn) — routine engine behavior, not an
    // application network failure. Every other failure is fatal unless a
    // test explicitly allows it.
    if (errorText === 'net::ERR_ABORTED') return
    const label = `${errorText} ${r.url()}`
    if (!ok(label)) obs.failedRequests.push(label)
  })
  page.on('console', (m) => {
    if (m.type() === 'error' && !ok(m.text())) obs.consoleErrors.push(m.text())
  })
  page.on('pageerror', (e) => obs.pageErrors.push(String(e)))
  return obs
}

export function assertObservationClean(obs: Observation): void {
  expect(obs.external, 'external-origin requests').toEqual([])
  expect(obs.api, '/api/ requests').toEqual([])
  expect(obs.pageErrors, 'page errors').toEqual([])
  expect(obs.consoleErrors, 'unexpected console errors').toEqual([])
  expect(obs.badResponses, 'unexpected same-origin 4xx/5xx responses').toEqual([])
  expect(obs.failedRequests, 'unexpected failed requests').toEqual([])
}

export const test = base.extend<{ observation: Observation }>({
  observation: [
    async ({ page }, use) => {
      const obs = observePage(page)
      await use(obs)
      assertObservationClean(obs)
    },
    { auto: true },
  ],
})

export { expect }
