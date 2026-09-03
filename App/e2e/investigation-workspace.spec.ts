import { expect, test, type BrowserContext, type Page, type Route } from '@playwright/test'
import { fileURLToPath } from 'node:url'

const APP_ORIGIN = 'https://127.0.0.1:3217'
const UPLOAD_ORIGIN = 'https://upload.e2e.invalid'
const SESSION = 's'.repeat(43)
const CSRF = 'c'.repeat(43)
const INVESTIGATION_ID = '55555555-5555-4555-8555-555555555555'
const PROJECT_ID = '11111111-1111-4111-8111-111111111111'
const JOB_ID = '22222222-2222-4222-8222-222222222222'
const TRACE_ID = '33333333-3333-4333-8333-333333333333'
const BELIEF_ID = '44444444-4444-4444-8444-444444444444'
const SOURCE_ID = '66666666-6666-4666-8666-666666666666'
const FIRST_EVIDENCE_ID = '77777777-7777-4777-8777-777777777777'
const SECOND_EVIDENCE_ID = '88888888-8888-4888-8888-888888888888'
const DECISION_ID = '99999999-9999-4999-8999-999999999999'
const PRINCIPAL_ID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const NOW = '2030-01-01T00:00:00.000Z'
const COMPLETED_AT = '2030-01-01T00:00:03.000Z'
const DEFAULT_MACHINE_ABSTENTION_REASON = 'The machine belief state abstained because the available evidence was insufficient to support a candidate.'
const CAPTURE_PATH = fileURLToPath(
  new URL('../../docs/assets/investigation-workspace.png', import.meta.url),
)

type BrowserRole = 'owner' | 'editor' | 'reviewer' | 'viewer'

const hypotheses = [
  {
    id: 'synthetic-north',
    label: 'Synthetic northern candidate',
    countryCode: null,
    region: null,
    city: null,
    latitude: null,
    longitude: null,
    summary: 'A test-only hypothesis assembled from synthetic observations.',
    probability: 0.52,
  },
  {
    id: 'synthetic-south',
    label: 'Synthetic southern candidate',
    countryCode: null,
    region: null,
    city: null,
    latitude: null,
    longitude: null,
    summary: 'A mutually exclusive test-only alternative.',
    probability: 0.48,
  },
]

const observations = [
  {
    evidenceId: FIRST_EVIDENCE_ID,
    kind: 'keyframe',
    observation: {
      summary: 'Synthetic frame A contains a generated blue rectangle.',
    },
    frameTimeMs: 4_000,
    bbox: { x: 0.16, y: 0.2, width: 0.3, height: 0.24 },
    polarity: 'supports',
    reliability: 0.62,
    verificationState: 'proposed',
    correlationGroup: 'synthetic-frame-a',
    createdAt: NOW,
  },
  {
    evidenceId: SECOND_EVIDENCE_ID,
    kind: 'metadata',
    observation: {
      summary: 'Fixture metadata identifies a generated, non-geographic source.',
    },
    frameTimeMs: 12_500,
    bbox: null,
    polarity: 'contradicts',
    reliability: 0.61,
    verificationState: 'verified',
    correlationGroup: 'synthetic-metadata',
    createdAt: NOW,
  },
] as const

const keyframes = observations.map(({ evidenceId, observation, frameTimeMs, bbox, createdAt }) => ({
  evidenceId,
  observation,
  frameTimeMs,
  bbox,
  createdAt,
}))

const belief = {
  beliefSnapshotId: BELIEF_ID,
  sequence: 1,
  candidates: hypotheses,
  entropy: 0.692,
  abstained: true,
  createdAt: NOW,
}

const steps = [
  {
    stepId: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
    sequence: 1,
    kind: 'inspect',
    tool: 'fixture.media-inspector',
    state: 'completed',
    inputEvidenceIds: [],
    outputEvidenceIds: [],
    modelDigest: null,
    promptDigest: null,
    latencyMs: 8,
    peakMemoryMb: 12,
    costMicrounits: 0,
    policyDecision: { decision: 'notRequired', decidedByPrincipalId: null, decidedAt: null },
    entropyBefore: null,
    entropyAfter: null,
    startedAt: NOW,
    completedAt: NOW,
  },
  {
    stepId: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
    sequence: 2,
    kind: 'observe',
    tool: 'deterministic-fixture',
    state: 'completed',
    inputEvidenceIds: [],
    outputEvidenceIds: [FIRST_EVIDENCE_ID, SECOND_EVIDENCE_ID],
    modelDigest: 'a'.repeat(64),
    promptDigest: 'b'.repeat(64),
    latencyMs: 4,
    peakMemoryMb: 10,
    costMicrounits: 0,
    policyDecision: { decision: 'notRequired', decidedByPrincipalId: null, decidedAt: null },
    entropyBefore: null,
    entropyAfter: 0.692,
    startedAt: NOW,
    completedAt: NOW,
  },
  {
    stepId: 'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
    sequence: 3,
    kind: 'review',
    tool: 'baseline.belief',
    state: 'completed',
    inputEvidenceIds: [FIRST_EVIDENCE_ID, SECOND_EVIDENCE_ID],
    outputEvidenceIds: [],
    modelDigest: null,
    promptDigest: null,
    latencyMs: 1,
    peakMemoryMb: 2,
    costMicrounits: 0,
    policyDecision: { decision: 'notRequired', decidedByPrincipalId: null, decidedAt: null },
    entropyBefore: 0.693,
    entropyAfter: 0.692,
    startedAt: NOW,
    completedAt: NOW,
  },
] as const

function detail(status: 'awaitingUpload' | 'queued' | 'needsReview' | 'completed' = 'needsReview') {
  const completed = status === 'completed'
  return {
    investigationId: INVESTIGATION_ID,
    projectId: PROJECT_ID,
    jobId: JOB_ID,
    name: 'Deterministic no-model abstention fixture',
    kind: 'geolocateProvenance',
    connectivityPolicy: 'local',
    status,
    abstained: status === 'needsReview' || completed,
    calibratedConfidence: null,
    createdAt: NOW,
    updatedAt: completed ? COMPLETED_AT : NOW,
    traceId: status === 'awaitingUpload' ? null : TRACE_ID,
    modelProvenance: status === 'awaitingUpload'
      ? { modelId: null, modelDigest: null, promptDigest: null, executedLocally: false }
      : {
          modelId: 'deterministic-fixture',
          modelDigest: 'a'.repeat(64),
          promptDigest: 'b'.repeat(64),
          executedLocally: true,
        },
    runtimeProvenance: status === 'awaitingUpload'
      ? { runtime: null, runtimeVersion: null, platform: null }
      : { runtime: 'fixture', runtimeVersion: '1', platform: 'synthetic-e2e' },
    finalHypothesis: null,
    abstentionReason: status === 'needsReview'
      ? DEFAULT_MACHINE_ABSTENTION_REASON
      : completed
      ? 'The synthetic clues do not support a unique location.'
      : null,
    completedAt: completed ? COMPLETED_AT : null,
  }
}

function summary() {
  const {
    traceId: _traceId,
    modelProvenance: _modelProvenance,
    runtimeProvenance: _runtimeProvenance,
    finalHypothesis: _finalHypothesis,
    abstentionReason: _abstentionReason,
    completedAt: _completedAt,
    ...value
  } = detail('needsReview')
  return value
}

function analystDecision(
  evidenceDecisions = observations.map(({ evidenceId }) => ({ evidenceId, decision: 'rejected' as const })),
  abstentionReason = 'The synthetic clues do not support a unique location.',
) {
  return {
    decisionId: DECISION_ID,
    status: 'final',
    evidenceDecisions,
    finalHypothesis: null,
    abstained: true,
    abstentionReason,
    notes: 'Reviewed only deterministic fixture evidence.',
    decidedByPrincipalId: PRINCIPAL_ID,
    createdAt: COMPLETED_AT,
  }
}

function report(decision = analystDecision()) {
  return {
    investigation: detail('completed'),
    source: {
      sourceRecordId: SOURCE_ID,
      publisherUrl: 'https://fixtures.example.test/public-domain/generated-video',
      publishedAt: NOW,
      collectedAt: NOW,
      legalBasis: 'publicDomain',
      license: null,
      mediaSha256: 'c'.repeat(64),
      redistributionPolicy: 'permitted',
      retentionDays: 14,
      purgeAfter: '2030-01-15T00:00:00.000Z',
    },
    decision,
    evidence: observations,
    latestBelief: belief,
  }
}

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

interface FixtureOptions {
  role: () => BrowserRole
  list?: boolean
  create?: boolean
  onDecision?: (value: Record<string, unknown>) => void
}

async function installFixture(page: Page, options: FixtureOptions) {
  const unexpectedBff: string[] = []
  const unexpectedExternal: string[] = []
  const uploads: Array<{ headers: Record<string, string>; url: string }> = []
  let finalized = analystDecision()

  await page.route('**/*', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const requestKey = `${request.method()} ${url.pathname}`

    if (url.origin === UPLOAD_ORIGIN && request.method() === 'POST' && url.pathname === '/fixture') {
      uploads.push({ headers: request.headers(), url: request.url() })
      return route.fulfill({ status: 204, body: '' })
    }

    if (url.origin !== APP_ORIGIN) {
      unexpectedExternal.push(request.url())
      return route.abort('blockedbyclient')
    }
    if (!url.pathname.startsWith('/api/bff/')) return route.continue()

    if (requestKey === 'GET /api/bff/session') {
      return fulfillJson(route, {
        user: {
          email: 'fixture-reviewer@example.test',
          displayName: 'Fixture Reviewer',
          organizationId: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee',
          role: options.role(),
        },
      })
    }
    if (requestKey === 'GET /api/bff/projects') return fulfillJson(route, { projects: [] })
    if (requestKey === 'GET /api/bff/cloud/investigations') {
      return fulfillJson(route, { data: options.list === false ? [] : [summary()] })
    }
    if (requestKey === 'POST /api/bff/cloud/investigations' && options.create) {
      return fulfillJson(route, {
        investigation: detail('awaitingUpload'),
        upload: {
          method: 'POST',
          url: `${UPLOAD_ORIGIN}/fixture`,
          fields: { key: 'synthetic-fixture/source.webm', policy: 'local-test-only' },
          expiresAt: '2099-01-01T00:00:00.000Z',
        },
      }, 201)
    }
    if (requestKey === `POST /api/bff/cloud/jobs/${JOB_ID}/uploads/complete` && options.create) {
      return fulfillJson(route, { id: JOB_ID, state: 'queued' }, 202)
    }
    if (requestKey === `GET /api/bff/cloud/investigations/${INVESTIGATION_ID}`) {
      return fulfillJson(route, detail('needsReview'))
    }
    if (requestKey === `GET /api/bff/cloud/investigations/${INVESTIGATION_ID}/keyframes`) {
      return fulfillJson(route, { data: keyframes })
    }
    if (requestKey === `GET /api/bff/cloud/investigations/${INVESTIGATION_ID}/evidence`) {
      return fulfillJson(route, { data: observations })
    }
    if (requestKey === `GET /api/bff/cloud/investigations/${INVESTIGATION_ID}/beliefs`) {
      return fulfillJson(route, { data: [belief] })
    }
    if (requestKey === `GET /api/bff/cloud/investigations/${INVESTIGATION_ID}/steps`) {
      return fulfillJson(route, { data: steps })
    }
    if (requestKey === `POST /api/bff/cloud/investigations/${INVESTIGATION_ID}/decision`) {
      const value = request.postDataJSON() as Record<string, unknown>
      options.onDecision?.(value)
      finalized = analystDecision(
        value.evidenceDecisions as Array<{ evidenceId: string; decision: 'rejected' }>,
        value.abstentionReason as string,
      )
      return fulfillJson(route, { investigation: detail('completed'), decision: finalized })
    }
    if (requestKey === `GET /api/bff/cloud/investigations/${INVESTIGATION_ID}/report`) {
      return fulfillJson(route, report(finalized))
    }

    unexpectedBff.push(requestKey)
    return fulfillJson(route, { error: { code: 'unexpected_test_route' } }, 500)
  })

  return { unexpectedBff, unexpectedExternal, uploads }
}

test('unauthenticated investigation deep links preserve a bounded return destination', async ({ page }) => {
  const path = `/investigations/${INVESTIGATION_ID}/report`
  await page.goto(path)

  await expect(page).toHaveURL((url) => (
    url.origin === APP_ORIGIN &&
    url.pathname === '/login' &&
    url.searchParams.get('returnTo') === path
  ))
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible()
})

test('primary list and create surfaces keep audio description in the legacy route', async ({ context, page }) => {
  await addOptimisticSession(context)
  let role: BrowserRole = 'owner'
  const fixture = await installFixture(page, { role: () => role })

  await page.goto('/')
  await expect(page).toHaveURL('/investigations')
  await expect(page.getByRole('heading', { name: 'Investigations' })).toBeVisible()
  await expect(page.getByText('Deterministic no-model abstention fixture')).toBeVisible()
  const navigation = page.getByRole('navigation', { name: 'Product' })
  await expect(navigation.locator('a[href="/investigations"]')).toBeVisible()
  await expect(navigation.locator('a[href="/investigations/new"]')).toBeVisible()
  await expect(navigation.locator('a[href="/account"]')).toBeVisible()
  await expect(navigation.locator('a[href="/legacy/audio-description"]')).toHaveCount(0)

  await page.goto('/investigations/new')
  await expect(page.getByRole('heading', { name: 'New investigation' })).toBeVisible()
  await expect(page.getByText('Geolocation & provenance', { exact: true })).toBeVisible()
  await expect(page.getByText('Local only', { exact: true })).toBeVisible()
  await expect(page.locator('input[name="kind"]')).toHaveValue('geolocateProvenance')
  await expect(page.locator('input[name="connectivityPolicy"]')).toHaveValue('local')
  await expect(page.getByText(/Other investigation modes are unavailable/)).toBeVisible()
  await expect(page.getByText(
    'Local mode means no public-internet retrieval during analysis. Authenticated BFF requests and the direct private-storage upload remain transport paths.',
  )).toBeVisible()
  await expect(page.getByText(/Visual change review|Approved image crops/)).toHaveCount(0)

  role = 'viewer'
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Read-only membership' })).toBeVisible()

  await page.goto('/legacy/audio-description')
  await expect(page.getByRole('heading', { name: 'Audio description' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Return to investigations' })).toBeVisible()
  expect(fixture.unexpectedBff).toEqual([])
  expect(fixture.unexpectedExternal).toEqual([])
})

test('owner creation uses only the local mode and an intercepted direct upload', async ({ context, page }) => {
  await addOptimisticSession(context)
  let createdBody: Record<string, unknown> | null = null
  const fixture = await installFixture(page, { role: () => 'owner', create: true })

  await page.addInitScript(() => {
    Object.defineProperty(HTMLMediaElement.prototype, 'duration', {
      configurable: true,
      get: () => 30,
    })
    const source = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'src')
    if (source?.set) {
      Object.defineProperty(HTMLMediaElement.prototype, 'src', {
        configurable: true,
        get: source.get,
        set(value: string) {
          source.set!.call(this, value)
          queueMicrotask(() => this.dispatchEvent(new Event('loadedmetadata')))
        },
      })
    }
  })
  await page.route(`${APP_ORIGIN}/api/bff/cloud/investigations`, async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    createdBody = route.request().postDataJSON() as Record<string, unknown>
    return route.fallback()
  })

  await page.goto('/investigations/new')
  await page.getByLabel('Investigation name').fill('Synthetic direct-upload journey')
  await page.getByLabel('Source video').setInputFiles({
    name: 'synthetic-source.webm',
    mimeType: 'video/webm',
    buffer: Buffer.from('deterministic fixture bytes'),
  })
  await page.getByRole('button', { name: 'Create and upload' }).click()

  await expect(page).toHaveURL(`/investigations/${INVESTIGATION_ID}`)
  await expect(page.getByRole('heading', { name: 'Deterministic no-model abstention fixture' })).toBeVisible()
  expect(createdBody).toMatchObject({
    kind: 'geolocateProvenance',
    connectivityPolicy: 'local',
    video: { fileName: 'synthetic-source.webm', contentType: 'video/webm', durationSeconds: 30 },
    source: { legalBasis: 'analystAuthorized', redistributionPolicy: 'metadataOnly' },
  })
  expect(fixture.uploads).toHaveLength(1)
  expect(fixture.uploads[0].headers.authorization).toBeUndefined()
  expect(fixture.uploads[0].headers.cookie).toBeUndefined()
  expect(fixture.uploads[0].headers.referer).toBeUndefined()
  expect(fixture.unexpectedBff).toEqual([])
  expect(fixture.unexpectedExternal).toEqual([])
})

test('workspace role controls remain distinct for all human roles', async ({ context, page }) => {
  await addOptimisticSession(context)
  let role: BrowserRole = 'owner'
  const fixture = await installFixture(page, { role: () => role })

  for (const currentRole of ['owner', 'editor', 'reviewer', 'viewer'] as const) {
    role = currentRole
    await page.goto(`/investigations/${INVESTIGATION_ID}`)
    await expect(page.getByText(`Role: ${currentRole}`)).toBeVisible()

    const mayCancel = currentRole === 'owner' || currentRole === 'editor'
    const mayReview = currentRole === 'owner' || currentRole === 'reviewer'
    await expect(page.getByRole('button', { name: 'Cancel', exact: true })).toHaveCount(mayCancel ? 1 : 0)
    await expect(page.getByRole('button', { name: /^Accept evidence / })).toHaveCount(
      mayReview ? observations.length : 0,
    )
    const finalize = page.getByRole('button', { name: 'Finalize investigation' })
    await expect(finalize).toBeDisabled()
    if (mayReview) {
      for (const button of await page.getByRole('button', { name: /^Accept evidence / }).all()) {
        await button.click()
      }
      await expect(finalize).toBeEnabled()
    }
  }

  expect(fixture.unexpectedBff).toEqual([])
  expect(fixture.unexpectedExternal).toEqual([])
})

test('deterministic workspace exposes uncertainty and completes an explicit abstention', async ({
  context,
  page,
}, testInfo) => {
  await addOptimisticSession(context)
  let submittedDecision: Record<string, unknown> | null = null
  const fixture = await installFixture(page, {
    role: () => 'reviewer',
    onDecision: (value) => { submittedDecision = value },
  })

  await page.goto(`/investigations/${INVESTIGATION_ID}`)
  await expect(page.getByRole('heading', { name: 'Deterministic no-model abstention fixture' })).toBeVisible()
  await expect(page.getByText('Deterministic fixture · no model inference')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Keyframe metadata', exact: true })).toBeVisible()
  await expect(page.getByText('Metadata overlay only · no source pixels are returned by this API.')).toBeVisible()
  const selectedFrame = page.getByRole('button', { pressed: true }).filter({ hasText: 'Frame 1' })
  await expect(selectedFrame).toHaveAttribute('aria-controls', 'keyframe-metadata-detail')
  await expect(page.getByRole('img', { name: 'Normalized bounding-box metadata overlay' }))
    .toHaveAttribute('aria-describedby', 'keyframe-bbox-description')
  await expect(page.getByText(
    'Any outlined region represents normalized bounding-box coordinates from metadata, not source pixels.',
  )).toBeVisible()
  await expect(page.getByText('Proposed observation')).toBeVisible()
  await expect(page.getByText('Verified by tool')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Baseline posterior (uncalibrated)' })).toBeVisible()
  await expect(page.getByText('The model-side belief state is abstaining.')).toBeVisible()
  await expect(page.locator('#machine-abstention-constraint')).toHaveText(
    'Machine abstention constraint: the latest belief state abstained. Finalization must preserve abstention, so a final candidate cannot be selected.',
  )
  await expect(page.getByLabel('Final candidate')).toBeDisabled()
  const abstainControl = page.getByRole('checkbox', { name: 'Abstain: evidence is insufficient' })
  await expect(abstainControl).toBeChecked()
  await expect(abstainControl).toBeDisabled()
  await expect(page.getByLabel('Abstention reason')).toHaveValue(DEFAULT_MACHINE_ABSTENTION_REASON)
  await expect(page.getByRole('heading', { name: 'Objective tool trace' })).toBeVisible()
  await expect(page.getByText('Entropy 0.693 → 0.692')).toBeVisible()
  const finalize = page.getByRole('button', { name: 'Finalize investigation' })
  await expect(finalize).toBeDisabled()

  if (process.env.UPDATE_PUBLIC_CAPTURE === '1' && testInfo.project.name === 'chromium') {
    await page.screenshot({ path: CAPTURE_PATH, fullPage: true, animations: 'disabled' })
  }

  const rejectButtons = await page.getByRole('button', { name: /^Reject evidence / }).all()
  await rejectButtons[0].click()
  await expect(finalize).toBeDisabled()
  await rejectButtons[1].click()
  await expect(finalize).toBeEnabled()
  await page.getByLabel('Abstention reason').fill(
    'The synthetic clues do not support a unique location.',
  )
  await page.getByLabel(/Notes/).fill('Reviewed only deterministic fixture evidence.')
  await finalize.click()

  await expect(page).toHaveURL(`/investigations/${INVESTIGATION_ID}/report`)
  await expect(page.getByRole('heading', { name: 'Source lineage' })).toBeVisible()
  await expect(page.getByText('Abstained', { exact: true })).toBeVisible()
  await expect(page.getByText('The synthetic clues do not support a unique location.')).toBeVisible()
  await expect(page.getByText('Latest baseline posterior (uncalibrated)')).toBeVisible()
  await expect(page.getByText('deterministic-fixture')).toBeVisible()
  const rejectedEvidence = page.getByRole('heading', { name: 'Rejected evidence' }).locator('xpath=ancestor::section[1]')
  await expect(rejectedEvidence.getByText('Proposed observation')).toBeVisible()
  await expect(rejectedEvidence.getByText('Verified by tool')).toBeVisible()
  expect(submittedDecision).toMatchObject({
    evidenceDecisions: observations.map(({ evidenceId }) => ({ evidenceId, decision: 'rejected' })),
    abstain: true,
    abstentionReason: 'The synthetic clues do not support a unique location.',
    notes: 'Reviewed only deterministic fixture evidence.',
  })
  expect(fixture.unexpectedBff).toEqual([])
  expect(fixture.unexpectedExternal).toEqual([])
})

test('the BFF rejects egress and arbitrary nested investigation resources', async ({ request }) => {
  const headers = {
    Cookie: `__Host-instadescribe_session=${SESSION}; __Host-instadescribe_csrf=${CSRF}`,
    Origin: APP_ORIGIN,
    'X-CSRF-Token': CSRF,
  }
  const egress = await request.post(
    `/api/bff/cloud/investigations/${INVESTIGATION_ID}/egress/${TRACE_ID}/decision`,
    { headers, data: { decision: 'approved' } },
  )
  const arbitrary = await request.get(
    `/api/bff/cloud/investigations/${INVESTIGATION_ID}/export`,
    { headers },
  )

  expect(egress.status()).toBe(404)
  expect(arbitrary.status()).toBe(404)
})
