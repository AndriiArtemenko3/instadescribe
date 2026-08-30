import { expect, test, type BrowserContext, type Route } from '@playwright/test'

const APP_ORIGIN = 'https://127.0.0.1:3217'
const SESSION = 's'.repeat(43)
const CSRF = 'c'.repeat(43)
const ORG_SLUG = 'canary-university'
const PROJECT_ID = '11111111-1111-4111-8111-111111111111'
const JOB_ID = '22222222-2222-4222-8222-222222222222'
const REVIEW_ID = '33333333-3333-4333-8333-333333333333'
const REVIEW_PATH = `/orgs/${ORG_SLUG}/projects/${PROJECT_ID}/jobs/${JOB_ID}/review`
const MEDIA_ORIGIN = 'https://media.e2e.invalid'
const NOW = '2030-01-01T00:00:00.000Z'

async function addOptimisticSession(context: BrowserContext) {
  await context.addCookies([
    {
      name: '__Host-instadescribe_session',
      value: SESSION,
      url: APP_ORIGIN,
      secure: true,
      httpOnly: true,
      sameSite: 'Lax',
    },
    {
      name: '__Host-instadescribe_csrf',
      value: CSRF,
      url: APP_ORIGIN,
      secure: true,
      httpOnly: false,
      sameSite: 'Lax',
    },
  ])
}

function fulfillJson(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

test('unknown routes return a real HTTP 404', async ({ page }) => {
  const response = await page.goto('/this-route-does-not-exist')
  expect(response?.status()).toBe(404)
  await expect(page.getByRole('heading', { name: 'That page does not exist.' })).toBeVisible()
})

test('an unauthenticated canonical deep link redirects to login with a bounded returnTo', async ({ page }) => {
  await page.goto(REVIEW_PATH)

  await expect(page).toHaveURL((url) => (
    url.origin === APP_ORIGIN &&
    url.pathname === '/login' &&
    url.searchParams.get('returnTo') === REVIEW_PATH
  ))
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
})

test('BFF mutations reject cross-origin and missing-CSRF requests on the production HTTPS boundary', async ({ request }) => {
  const crossOrigin = await request.post('/api/bff/session', {
    headers: { Origin: 'https://attacker.invalid' },
    data: { email: 'reviewer@example.test', password: 'not-a-real-secret' },
  })
  expect(crossOrigin.status()).toBe(403)
  expect(await crossOrigin.json()).toMatchObject({ error: { code: 'cross_origin' } })

  const missingCsrf = await request.delete('/api/bff/session', {
    headers: { Origin: APP_ORIGIN },
  })
  expect(missingCsrf.status()).toBe(403)
  expect(await missingCsrf.json()).toMatchObject({ error: { code: 'csrf_rejected' } })

  const matchedCsrf = await request.delete('/api/bff/session', {
    headers: {
      Origin: APP_ORIGIN,
      Cookie: `__Host-instadescribe_csrf=${CSRF}`,
      'X-CSRF-Token': CSRF,
    },
  })
  expect(matchedCsrf.status()).toBe(200)
  expect(await matchedCsrf.json()).toEqual({ signedOut: true })
})

test('canonical review binds org + project + job and keeps signed media outside Next', async ({ context, page }) => {
  await addOptimisticSession(context)
  const bffPaths: string[] = []
  const directRequests: Array<{ url: string; headers: Record<string, string> }> = []

  await page.route(`${APP_ORIGIN}/api/bff/**`, async (route) => {
    const url = new URL(route.request().url())
    bffPaths.push(url.pathname)

    if (url.pathname === '/api/bff/projects') {
      return fulfillJson(route, {
        projects: [{
          id: PROJECT_ID,
          orgSlug: ORG_SLUG,
          currentJobId: JOB_ID,
          name: 'E2E Review Project',
          status: 'ready',
          updatedAt: NOW,
        }],
      })
    }
    if (url.pathname === '/api/bff/session') {
      return fulfillJson(route, {
        user: {
          email: 'viewer@example.test',
          displayName: 'Canary Viewer',
          organizationId: '44444444-4444-4444-8444-444444444444',
          role: 'viewer',
        },
      })
    }
    if (url.pathname === `/api/bff/cloud/jobs/${JOB_ID}`) {
      return fulfillJson(route, { id: JOB_ID, projectId: PROJECT_ID, state: 'needs_review' })
    }
    if (url.pathname === `/api/bff/cloud/jobs/${JOB_ID}/manifest`) {
      const ref = (file: string, contentType: string) => ({
        url: `${MEDIA_ORIGIN}/${file}?versionId=e2e-version&signature=redacted`,
        contentType,
        sizeBytes: 2,
        checksumSha256: '0'.repeat(64),
      })
      return fulfillJson(route, {
        projectId: PROJECT_ID,
        jobId: JOB_ID,
        pipelineRevision: 'e2e-fixture',
        expiresAt: '2099-01-01T00:00:00.000Z',
        artifacts: {
          video: ref('source.mp4', 'video/mp4'),
          scenes: ref('scenes.json', 'application/json'),
          entities: ref('entities.json', 'application/json'),
          audioEvents: ref('audio-events.json', 'application/json'),
          placementGaps: ref('placement-gaps.json', 'application/json'),
          transcript: ref('transcript.json', 'application/json'),
          posterJpg: null,
          posterAvif: null,
        },
      })
    }
    if (url.pathname === `/api/bff/cloud/jobs/${JOB_ID}/overrides`) {
      return fulfillJson(route, {})
    }
    if (url.pathname === `/api/bff/cloud/jobs/${JOB_ID}/review`) {
      return fulfillJson(route, {
        id: REVIEW_ID,
        object: 'review',
        jobId: JOB_ID,
        state: 'open',
        version: 1,
        locked: false,
        sceneCount: 0,
        decidedSceneCount: 0,
        approvedSceneCount: 0,
        rejectedSceneCount: 0,
        zeroAdConfirmed: false,
        lockedAt: null,
        completedAt: null,
        expiresAt: '2099-01-01T00:00:00.000Z',
        createdAt: NOW,
        updatedAt: NOW,
      })
    }
    return fulfillJson(route, { error: { code: 'unexpected_test_route' } }, 500)
  })

  await page.route(`${MEDIA_ORIGIN}/**`, async (route) => {
    const request = route.request()
    directRequests.push({ url: request.url(), headers: request.headers() })
    const pathname = new URL(request.url()).pathname
    if (pathname === '/source.mp4') {
      return route.fulfill({ status: 200, contentType: 'video/mp4', body: 'e2e' })
    }
    return fulfillJson(route, [])
  })

  await page.goto(REVIEW_PATH)
  await expect(page.getByText('E2E Review Project')).toBeVisible()
  await expect(page.getByText('Read-only review')).toBeVisible()

  const videoSource = await page.locator('video').getAttribute('src')
  expect(videoSource).not.toBeNull()
  expect(new URL(videoSource!).origin).toBe(MEDIA_ORIGIN)
  expect(bffPaths).toContain(`/api/bff/cloud/jobs/${JOB_ID}/manifest`)
  expect(bffPaths.some((path) => path.includes('source.mp4'))).toBe(false)

  await expect.poll(() => directRequests.some(({ url }) => new URL(url).pathname === '/scenes.json')).toBe(true)
  const artifactRequests = directRequests.filter(({ url }) => url.endsWith('.json?versionId=e2e-version&signature=redacted'))
  expect(artifactRequests.length).toBeGreaterThan(0)
  for (const request of artifactRequests) {
    expect(request.headers.authorization).toBeUndefined()
    expect(request.headers.cookie).toBeUndefined()
    expect(request.headers.referer).toBeUndefined()
  }

  await page.goto(`/orgs/another-tenant/projects/${PROJECT_ID}/jobs/${JOB_ID}/review`)
  await expect(page.getByRole('heading', { name: 'Review job not found.' })).toBeVisible()
})
