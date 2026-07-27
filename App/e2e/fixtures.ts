import { test as base, expect, type Page } from '@playwright/test'

// Global observation for the whole browser suite: every page in every test is
// watched for external-origin requests, /api/ paths, page errors and
// unexpected console errors; any hit fails the test in teardown. (The
// local-only browser-speech rule cannot be observed as a page request —
// speech services don't surface here — so it is enforced separately by the
// localVoice unit tests and the honest-unavailable e2e assertions.)

export const DEMO_ORIGIN = 'http://localhost:4174'

// Deliberate checks log these resource errors (404-boundary tests; the
// route-abort in the fixture-retry test); everything else fails.
const EXPECTED_CONSOLE = [
  /Failed to load resource: the server responded with a status of 404/,
  /Failed to load resource: net::ERR_FAILED/,
]

export interface Observation {
  external: string[]
  api: string[]
  consoleErrors: string[]
  pageErrors: string[]
}

export function observePage(page: Page): Observation {
  const obs: Observation = { external: [], api: [], consoleErrors: [], pageErrors: [] }
  page.on('request', (r) => {
    const u = new URL(r.url())
    // Non-network schemes (the CSP test's data: parent page) are not requests
    // that could leave the machine.
    if (!/^https?:$/.test(u.protocol)) return
    if (u.origin !== DEMO_ORIGIN) obs.external.push(r.url())
    if (u.pathname.includes('/api/')) obs.api.push(r.url())
  })
  page.on('console', (m) => {
    if (m.type() === 'error' && !EXPECTED_CONSOLE.some((re) => re.test(m.text()))) {
      obs.consoleErrors.push(m.text())
    }
  })
  page.on('pageerror', (e) => obs.pageErrors.push(String(e)))
  return obs
}

export function assertObservationClean(obs: Observation): void {
  expect(obs.external, 'external-origin requests').toEqual([])
  expect(obs.api, '/api/ requests').toEqual([])
  expect(obs.pageErrors, 'page errors').toEqual([])
  expect(obs.consoleErrors, 'unexpected console errors').toEqual([])
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
