// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { InvestigationWorkspace } from './investigation-workspace'

const ID = '11111111-1111-4111-8111-111111111111'
const PROJECT = '22222222-2222-4222-8222-222222222222'
const JOB = '33333333-3333-4333-8333-333333333333'
const EVIDENCE = '44444444-4444-4444-8444-444444444444'
const EVIDENCE_TWO = '77777777-7777-4777-8777-777777777777'
const BELIEF = '55555555-5555-4555-8555-555555555555'
const STEP = '66666666-6666-4666-8666-666666666666'

const detail = {
  investigationId: ID,
  projectId: PROJECT,
  jobId: JOB,
  name: 'Rights-cleared station clip',
  kind: 'geolocateProvenance',
  connectivityPolicy: 'local',
  status: 'needsReview',
  abstained: false,
  calibratedConfidence: null,
  createdAt: '2026-08-30T10:00:00Z',
  updatedAt: '2026-08-30T10:05:00Z',
  traceId: null,
  modelProvenance: { modelId: null, modelDigest: null, promptDigest: null, executedLocally: false },
  runtimeProvenance: { runtime: null, runtimeVersion: null, platform: null },
  finalHypothesis: null,
  abstentionReason: null,
  completedAt: null,
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function browserApi(
  role: 'owner' | 'editor' | 'reviewer' | 'viewer',
  modelId: string | null = null,
  status = detail.status,
  beliefAbstained = false,
  evidenceCount = 1,
) {
  return vi.fn(async (url: string, _init?: RequestInit) => {
    if (url === `/api/bff/cloud/investigations/${ID}`) return json({
      ...detail,
      status,
      abstained: beliefAbstained,
      abstentionReason: beliefAbstained ? 'The recorded machine run did not separate the candidates.' : null,
      finalHypothesis: status === 'completed' && !beliefAbstained
        ? { id: 'unknown-region', label: 'Unknown region' }
        : null,
      completedAt: status === 'completed' ? '2026-08-30T10:06:00Z' : null,
      modelProvenance: { ...detail.modelProvenance, modelId },
    })
    if (url.endsWith('/keyframes')) return json({ data: [{
      evidenceId: EVIDENCE,
      frameTimeMs: 2000,
      observation: { summary: 'A frame selected from the uploaded source.' },
      bbox: { x: 0.2, y: 0.2, width: 0.3, height: 0.3 },
      createdAt: '2026-08-30T10:02:00Z',
    }] })
    if (url.endsWith('/evidence')) return json({ data: [EVIDENCE, EVIDENCE_TWO].slice(0, evidenceCount).map((evidenceId, index) => ({
      evidenceId,
      kind: 'keyframe',
      observation: { summary: index === 0 ? 'A frame selected from the uploaded source.' : 'A second proposed frame observation.' },
      frameTimeMs: 2000 + (index * 1000),
      bbox: { x: 0.2, y: 0.2, width: 0.3, height: 0.3 },
      polarity: 'neutral',
      reliability: 0.5,
      verificationState: 'proposed',
      correlationGroup: `frame-${index + 2}`,
      createdAt: '2026-08-30T10:02:00Z',
    })) })
    if (url.endsWith('/beliefs')) return json({ data: [{
      beliefSnapshotId: BELIEF,
      sequence: 1,
      candidates: [{ id: 'unknown-region', label: 'Unknown region', countryCode: null, region: null, city: null, latitude: null, longitude: null, summary: null, probability: 1 }],
      entropy: 0,
      abstained: beliefAbstained,
      createdAt: '2026-08-30T10:03:00Z',
    }] })
    if (url.endsWith('/steps')) return json({ data: [{
      stepId: STEP,
      sequence: 1,
      kind: 'fixture',
      tool: 'deterministic.fixture',
      state: 'completed',
      inputEvidenceIds: [],
      outputEvidenceIds: [EVIDENCE],
      modelDigest: null,
      promptDigest: null,
      latencyMs: 2,
      peakMemoryMb: 1,
      costMicrounits: 0,
      policyDecision: { decision: 'notRequired', decidedByPrincipalId: null, decidedAt: null },
      entropyBefore: null,
      entropyAfter: 0,
      startedAt: '2026-08-30T10:01:00Z',
      completedAt: '2026-08-30T10:01:01Z',
    }] })
    if (url === '/api/bff/session') return json({ user: {
      email: `${role}@example.com`,
      displayName: role,
      organizationId: 'org-1',
      role,
    } })
    return new Response(null, { status: 404 })
  })
}

function requestIdempotencyKey(call: [string, RequestInit?]): string | undefined {
  return (call[1]?.headers as Record<string, string> | undefined)?.['Idempotency-Key']
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('investigation workspace presentation boundary', () => {
  it('renders only API-backed observations and gives a viewer no mutation controls', async () => {
    vi.stubGlobal('fetch', browserApi('viewer'))
    const { container } = render(<InvestigationWorkspace investigationId={ID} />)

    expect(await screen.findByRole('heading', { name: 'Rights-cleared station clip' })).toBeTruthy()
    expect(screen.getAllByText('A frame selected from the uploaded source.').length).toBeGreaterThan(0)
    expect(screen.getByText('No analyst-finalized coordinates are present in the API response.')).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Keyframe metadata' })).toBeTruthy()
    expect(screen.getByText('Metadata overlay only · no source pixels are returned by this API.')).toBeTruthy()
    expect(screen.getByText('Proposed observation')).toBeTruthy()
    expect(screen.getByText('Policy: notRequired')).toBeTruthy()
    const keyframe = screen.getByRole('button', { name: /Frame 1/ })
    expect(keyframe.getAttribute('aria-pressed')).toBe('true')
    expect(keyframe.getAttribute('aria-controls')).toBe('keyframe-metadata-detail')
    const boundingBox = screen.getByRole('img', { name: 'Normalized bounding-box metadata overlay' })
    expect(boundingBox.getAttribute('aria-describedby')).toBe('keyframe-bbox-description')
    expect(screen.getByText('Any outlined region represents normalized bounding-box coordinates from metadata, not source pixels.')).toBeTruthy()
    expect(container.querySelector('main')).toBeNull()
    expect(screen.queryByText(/external egress/i)).toBeNull()
    expect(screen.queryByRole('button', { name: /^Accept evidence / })).toBeNull()
    expect((screen.getByRole('button', { name: 'Finalize investigation' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('shows evidence decisions and finalization to a reviewer', async () => {
    vi.stubGlobal('fetch', browserApi('reviewer'))
    render(<InvestigationWorkspace investigationId={ID} />)

    const accept = await screen.findByRole('button', { name: `Accept evidence ${EVIDENCE}` })
    const reject = screen.getByRole('button', { name: `Reject evidence ${EVIDENCE}` })
    expect(accept.getAttribute('aria-pressed')).toBe('false')
    expect(reject.getAttribute('aria-pressed')).toBe('false')
    fireEvent.click(accept)
    expect(accept.getAttribute('aria-pressed')).toBe('true')
    expect(reject.getAttribute('aria-pressed')).toBe('false')
    fireEvent.click(reject)
    expect(accept.getAttribute('aria-pressed')).toBe('false')
    expect(reject.getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByRole('button', { name: 'Finalize investigation' })).toBeTruthy()
  })

  it('keeps finalization disabled until every existing evidence item has an explicit decision', async () => {
    vi.stubGlobal('fetch', browserApi('reviewer', null, detail.status, false, 2))
    render(<InvestigationWorkspace investigationId={ID} />)

    const acceptButtons = await screen.findAllByRole('button', { name: /^Accept evidence / })
    expect(acceptButtons).toHaveLength(2)
    const finalizeButton = screen.getByRole('button', { name: 'Finalize investigation' }) as HTMLButtonElement
    expect(finalizeButton.disabled).toBe(true)

    fireEvent.click(acceptButtons[0]!)
    expect(finalizeButton.disabled).toBe(true)
    fireEvent.click(acceptButtons[1]!)
    expect(finalizeButton.disabled).toBe(false)
  })

  it('requires a reason when the analyst explicitly abstains', async () => {
    vi.stubGlobal('fetch', browserApi('reviewer'))
    render(<InvestigationWorkspace investigationId={ID} />)

    fireEvent.click(await screen.findByRole('button', { name: /^Accept evidence / }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Abstain: evidence is insufficient' }))
    fireEvent.submit(screen.getByRole('button', { name: 'Finalize investigation' }).closest('form')!)

    expect(await screen.findByText('Record why the available evidence is insufficient.')).toBeTruthy()
  })

  it('forces and explains abstention when the latest machine belief abstained', async () => {
    vi.stubGlobal('fetch', browserApi('reviewer', null, detail.status, true))
    render(<InvestigationWorkspace investigationId={ID} />)

    expect((await screen.findByRole('alert')).textContent).toContain(
      'Machine abstention constraint: the latest belief state abstained. Finalization must preserve abstention, so a final candidate cannot be selected.',
    )
    const candidate = screen.getByLabelText('Final candidate') as HTMLSelectElement
    expect(candidate.disabled).toBe(true)
    expect(candidate.value).toBe('')
    const abstainControl = screen.getByRole('checkbox', { name: 'Abstain: evidence is insufficient' }) as HTMLInputElement
    expect(abstainControl.checked).toBe(true)
    expect(abstainControl.disabled).toBe(true)
    await waitFor(() => expect((screen.getByRole('textbox', { name: 'Abstention reason' }) as HTMLInputElement).value)
      .toBe('The recorded machine run did not separate the candidates.'))
  })

  it('submits the machine abstention without promoting a candidate', async () => {
    vi.spyOn(document, 'cookie', 'get').mockReturnValue(`__Host-instadescribe_csrf=${'a'.repeat(43)}`)
    const reads = browserApi('reviewer', null, detail.status, true)
    const api = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === `/api/bff/cloud/investigations/${ID}/decision`) {
        return json({ code: 'fixture_stops_before_navigation' }, 503)
      }
      return reads(url, init)
    })
    vi.stubGlobal('fetch', api)
    render(<InvestigationWorkspace investigationId={ID} />)

    fireEvent.click(await screen.findByRole('button', { name: /^Accept evidence / }))
    await screen.findByRole('alert')
    fireEvent.submit(screen.getByRole('button', { name: 'Finalize investigation' }).closest('form')!)
    await waitFor(() => expect(api.mock.calls.some(([url]) => url.endsWith('/decision'))).toBe(true))

    const attempt = api.mock.calls.find(([url]) => url.endsWith('/decision'))!
    const body = JSON.parse(String(attempt[1]?.body)) as Record<string, unknown>
    expect(body.abstain).toBe(true)
    expect(body.abstentionReason).toBe('The recorded machine run did not separate the candidates.')
    expect(body).not.toHaveProperty('finalHypothesis')
  })

  it('allows a mandatory machine abstention to finalize with zero evidence decisions', async () => {
    vi.spyOn(document, 'cookie', 'get').mockReturnValue(`__Host-instadescribe_csrf=${'a'.repeat(43)}`)
    const reads = browserApi('reviewer', null, detail.status, true)
    const api = vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith('/keyframes') || url.endsWith('/evidence')) return json({ data: [] })
      if (url === `/api/bff/cloud/investigations/${ID}/decision`) {
        return json({ code: 'fixture_stops_before_navigation' }, 503)
      }
      return reads(url, init)
    })
    vi.stubGlobal('fetch', api)
    render(<InvestigationWorkspace investigationId={ID} />)

    await screen.findByRole('alert')
    await waitFor(() => expect((screen.getByRole('textbox', { name: 'Abstention reason' }) as HTMLInputElement).value)
      .toBe('The recorded machine run did not separate the candidates.'))
    const finalizeButton = screen.getByRole('button', { name: 'Finalize investigation' }) as HTMLButtonElement
    expect(finalizeButton.disabled).toBe(false)
    fireEvent.submit(finalizeButton.closest('form')!)
    await waitFor(() => expect(api.mock.calls.some(([url]) => url.endsWith('/decision'))).toBe(true))

    const attempt = api.mock.calls.find(([url]) => url.endsWith('/decision'))!
    const body = JSON.parse(String(attempt[1]?.body)) as Record<string, unknown>
    expect(body.evidenceDecisions).toEqual([])
    expect(body.abstain).toBe(true)
    expect(body).not.toHaveProperty('finalHypothesis')
  })

  it.each([
    ['owner', true, true, true],
    ['editor', true, false, false],
    ['reviewer', false, true, true],
    ['viewer', false, false, false],
  ] as const)('enforces cancel/review/finalize controls for %s', async (role, mayCancel, mayReview, mayFinalize) => {
    vi.stubGlobal('fetch', browserApi(role))
    render(<InvestigationWorkspace investigationId={ID} />)

    await screen.findByRole('heading', { name: 'Rights-cleared station clip' })
    expect(Boolean(screen.queryByRole('button', { name: 'Cancel' }))).toBe(mayCancel)
    expect(Boolean(screen.queryByRole('button', { name: /^Accept evidence / }))).toBe(mayReview)
    const finalizeButton = screen.getByRole('button', { name: 'Finalize investigation' }) as HTMLButtonElement
    expect(finalizeButton.disabled).toBe(true)
    if (mayReview) fireEvent.click(screen.getByRole('button', { name: /^Accept evidence / }))
    expect(finalizeButton.disabled).toBe(!mayFinalize)
  })

  it('reuses the cancel idempotency key across an ambiguous retry', async () => {
    vi.spyOn(document, 'cookie', 'get').mockReturnValue(`__Host-instadescribe_csrf=${'a'.repeat(43)}`)
    const generatedKey = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
    const randomUuid = vi.spyOn(crypto, 'randomUUID').mockReturnValue(generatedKey)
    const reads = browserApi('owner')
    const api = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === `/api/bff/cloud/investigations/${ID}/cancel`) {
        return json({ code: 'temporarily_unavailable' }, 503)
      }
      return reads(url, init)
    })
    vi.stubGlobal('fetch', api)
    render(<InvestigationWorkspace investigationId={ID} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }))
    await screen.findByText('Cancellation could not be confirmed.')
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(api.mock.calls.filter(([url]) => url.endsWith('/cancel'))).toHaveLength(2))

    const attempts = api.mock.calls.filter(([url]) => url.endsWith('/cancel'))
    expect(requestIdempotencyKey(attempts[0]!)).toBe(generatedKey)
    expect(requestIdempotencyKey(attempts[1]!)).toBe(generatedKey)
    expect(randomUuid).toHaveBeenCalledTimes(1)
  })

  it('reuses a finalization key for the same decision and rotates it when material review input changes', async () => {
    vi.spyOn(document, 'cookie', 'get').mockReturnValue(`__Host-instadescribe_csrf=${'a'.repeat(43)}`)
    const generatedKeys = [
      'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
    ] as const
    vi.spyOn(crypto, 'randomUUID')
      .mockReturnValueOnce(generatedKeys[0])
      .mockReturnValueOnce(generatedKeys[1])
    const reads = browserApi('reviewer')
    const api = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === `/api/bff/cloud/investigations/${ID}/decision`) {
        return json({ code: 'temporarily_unavailable' }, 503)
      }
      return reads(url, init)
    })
    vi.stubGlobal('fetch', api)
    render(<InvestigationWorkspace investigationId={ID} />)

    fireEvent.click(await screen.findByRole('button', { name: /^Accept evidence / }))
    const form = screen.getByRole('button', { name: 'Finalize investigation' }).closest('form')!
    fireEvent.submit(form)
    await screen.findByText('The analyst decision was not accepted.')
    fireEvent.submit(form)
    await waitFor(() => expect(api.mock.calls.filter(([url]) => url.endsWith('/decision'))).toHaveLength(2))

    fireEvent.change(screen.getByLabelText(/Notes/), { target: { value: 'Material analyst note.' } })
    fireEvent.submit(form)
    await waitFor(() => expect(api.mock.calls.filter(([url]) => url.endsWith('/decision'))).toHaveLength(3))

    const attempts = api.mock.calls.filter(([url]) => url.endsWith('/decision'))
    expect(requestIdempotencyKey(attempts[0]!)).toBe(generatedKeys[0])
    expect(requestIdempotencyKey(attempts[1]!)).toBe(generatedKeys[0])
    expect(requestIdempotencyKey(attempts[2]!)).toBe(generatedKeys[1])
  })

  it('labels deterministic fixture evidence without claiming model inference', async () => {
    vi.stubGlobal('fetch', browserApi('viewer', 'deterministic-fixture'))
    render(<InvestigationWorkspace investigationId={ID} />)

    expect(await screen.findByText('Deterministic fixture · no model inference')).toBeTruthy()
    expect(screen.queryByText('Model executed locally')).toBeNull()
  })

  it.each(['awaitingUpload', 'queued', 'preprocessing'])(
    'does not claim completed model execution while status is %s',
    async (status) => {
      vi.stubGlobal('fetch', browserApi('viewer', null, status))
      render(<InvestigationWorkspace investigationId={ID} />)

      expect(await screen.findByText('Local execution configured · pending')).toBeTruthy()
      expect(screen.queryByText('Model executed locally')).toBeNull()
    },
  )

  it('polls nonterminal investigations and clears the scheduled refresh on unmount', async () => {
    const setTimeoutSpy = vi.spyOn(globalThis, 'setTimeout')
    const clearTimeoutSpy = vi.spyOn(globalThis, 'clearTimeout')
    const api = browserApi('viewer', null, 'queued')
    vi.stubGlobal('fetch', api)
    const view = render(<InvestigationWorkspace investigationId={ID} />)

    await screen.findByRole('heading', { name: 'Rights-cleared station clip' })
    const detailCalls = () => api.mock.calls.filter(([url]) => url === `/api/bff/cloud/investigations/${ID}`).length
    expect(detailCalls()).toBe(1)

    const firstPollIndex = setTimeoutSpy.mock.calls.findIndex(([, delay]) => delay === 5_000)
    expect(firstPollIndex).toBeGreaterThanOrEqual(0)
    const firstPoll = setTimeoutSpy.mock.calls[firstPollIndex]?.[0]
    const firstTimer = setTimeoutSpy.mock.results[firstPollIndex]?.value
    clearTimeout(firstTimer)
    expect(typeof firstPoll).toBe('function')
    await act(async () => { (firstPoll as () => void)() })
    await waitFor(() => expect(detailCalls()).toBe(2))

    const pollIndexes = setTimeoutSpy.mock.calls
      .map(([, delay], index) => delay === 5_000 ? index : -1)
      .filter((index) => index >= 0)
    const latestPollIndex = pollIndexes.at(-1)!
    const latestTimer = setTimeoutSpy.mock.results[latestPollIndex]?.value
    const signal = api.mock.calls[0]?.[1]?.signal
    view.unmount()

    expect(clearTimeoutSpy).toHaveBeenCalledWith(latestTimer)
    expect(signal?.aborted).toBe(true)
  })

  it('does not schedule another poll after a terminal response', async () => {
    const setTimeoutSpy = vi.spyOn(globalThis, 'setTimeout')
    vi.stubGlobal('fetch', browserApi('viewer', null, 'completed'))
    render(<InvestigationWorkspace investigationId={ID} />)

    await screen.findByRole('heading', { name: 'Rights-cleared station clip' })
    expect(setTimeoutSpy.mock.calls.some(([, delay]) => delay === 5_000)).toBe(false)
  })
})
