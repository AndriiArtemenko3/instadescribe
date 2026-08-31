// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  canCancelInvestigation,
  canCreateInvestigation,
  canFinalizeInvestigation,
  canReviewInvestigation,
  cancelInvestigation,
  createInvestigation,
  finalizeInvestigation,
  InvestigationApiError,
  listInvestigations,
  loadBrowserSession,
  loadInvestigationReport,
  loadInvestigationWorkspace,
  parseInvestigationDetail,
} from './investigations'

const INVESTIGATION_ID = '11111111-1111-4111-8111-111111111111'
const PROJECT_ID = '22222222-2222-4222-8222-222222222222'
const JOB_ID = '33333333-3333-4333-8333-333333333333'
const EVIDENCE_ID = '44444444-4444-4444-8444-444444444444'
const BELIEF_ID = '55555555-5555-4555-8555-555555555555'
const STEP_ID = '66666666-6666-4666-8666-666666666666'
const DECISION_ID = '77777777-7777-4777-8777-777777777777'
const PRINCIPAL_ID = '88888888-8888-4888-8888-888888888888'

function summary() {
  return {
    investigationId: INVESTIGATION_ID,
    projectId: PROJECT_ID,
    jobId: JOB_ID,
    name: 'Public square provenance',
    kind: 'geolocateProvenance',
    connectivityPolicy: 'local',
    status: 'needsReview',
    abstained: false,
    calibratedConfidence: null,
    createdAt: '2026-08-30T09:00:00Z',
    updatedAt: '2026-08-30T09:10:00Z',
  }
}

function detail() {
  return {
    ...summary(),
    traceId: null,
    modelProvenance: { modelId: 'local-vlm', executedLocally: true },
    runtimeProvenance: { runtime: 'ollama', platform: 'darwin-arm64' },
    finalHypothesis: null,
    abstentionReason: null,
    completedAt: null,
  }
}

function evidence() {
  return {
    evidenceId: EVIDENCE_ID,
    kind: 'keyframe',
    observation: { summary: 'A permitted source frame contains a transit sign.' },
    frameTimeMs: 4200,
    bbox: { x: 0.1, y: 0.2, width: 0.3, height: 0.2 },
    polarity: 'supports',
    reliability: 0.8,
    verificationState: 'proposed',
    correlationGroup: 'frame-4200-sign',
    createdAt: '2026-08-30T09:04:00Z',
  }
}

function keyframe() {
  const item = evidence()
  return {
    evidenceId: item.evidenceId,
    frameTimeMs: item.frameTimeMs,
    observation: item.observation,
    bbox: item.bbox,
    createdAt: item.createdAt,
  }
}

function belief() {
  return {
    beliefSnapshotId: BELIEF_ID,
    sequence: 1,
    candidates: [
      { id: 'candidate-a', label: 'Candidate A', probability: 0.7 },
      { id: 'candidate-b', label: 'Candidate B', probability: 0.3 },
    ],
    entropy: 0.611,
    abstained: false,
    createdAt: '2026-08-30T09:05:00Z',
  }
}

function step() {
  return {
    stepId: STEP_ID,
    sequence: 1,
    kind: 'observe',
    tool: 'local-vlm',
    state: 'completed',
    inputEvidenceIds: [],
    outputEvidenceIds: [EVIDENCE_ID],
    modelDigest: null,
    promptDigest: null,
    latencyMs: 25,
    peakMemoryMb: 128,
    costMicrounits: 0,
    policyDecision: { decision: 'notRequired', decidedByPrincipalId: null, decidedAt: null },
    entropyBefore: 1.2,
    entropyAfter: 0.611,
    startedAt: '2026-08-30T09:03:00Z',
    completedAt: '2026-08-30T09:05:00Z',
  }
}

function json(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
}

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('investigation browser contract', () => {
  it('normalizes documented sparse detail fields while rejecting extra or malformed fields', () => {
    expect(parseInvestigationDetail(detail())).toMatchObject({
      investigationId: INVESTIGATION_ID,
      modelProvenance: { modelId: 'local-vlm', modelDigest: null, promptDigest: null, executedLocally: true },
      runtimeProvenance: { runtime: 'ollama', runtimeVersion: null, platform: 'darwin-arm64' },
    })
    expect(parseInvestigationDetail({ ...detail(), providerPrompt: 'secret' })).toBeNull()
    expect(parseInvestigationDetail({
      ...detail(),
      modelProvenance: { executedLocally: true, providerPrompt: 'secret' },
    })).toBeNull()
    expect(parseInvestigationDetail({
      ...detail(),
      runtimeProvenance: { platform: 'darwin-arm64', providerRuntime: 'secret' },
    })).toBeNull()
    expect(parseInvestigationDetail({ ...detail(), calibratedConfidence: 1.1 })).toBeNull()
    expect(parseInvestigationDetail({
      ...detail(),
      finalHypothesis: { id: 'candidate', label: 'Candidate', latitude: 91 },
    })).toBeNull()
    expect(parseInvestigationDetail({ ...detail(), kind: 'liveTargeting' })).toBeNull()
    expect(parseInvestigationDetail({ ...detail(), connectivityPolicy: 'internet' })).toBeNull()
  })

  it('requires coherent abstention, final-hypothesis and completion fields', () => {
    const hypothesis = { id: 'candidate-a', label: 'Candidate A' }
    const completedAt = '2026-08-30T09:20:00Z'
    expect(parseInvestigationDetail({
      ...detail(),
      abstained: true,
      abstentionReason: 'insufficientIndependentEvidence',
    })).toMatchObject({
      abstained: true,
      finalHypothesis: null,
      abstentionReason: 'insufficientIndependentEvidence',
      completedAt: null,
    })
    expect(parseInvestigationDetail({
      ...detail(),
      status: 'completed',
      finalHypothesis: hypothesis,
      completedAt,
    })).toMatchObject({ status: 'completed', finalHypothesis: hypothesis, completedAt })
    expect(parseInvestigationDetail({
      ...detail(),
      status: 'completed',
      abstained: true,
      abstentionReason: 'insufficientIndependentEvidence',
      completedAt,
    })).toMatchObject({
      status: 'completed',
      abstained: true,
      finalHypothesis: null,
      abstentionReason: 'insufficientIndependentEvidence',
      completedAt,
    })

    for (const malformed of [
      { ...detail(), abstained: true },
      { ...detail(), abstained: true, abstentionReason: '   ' },
      { ...detail(), abstained: true, abstentionReason: 'insufficient', finalHypothesis: hypothesis },
      { ...detail(), abstentionReason: 'unexpected' },
      { ...detail(), finalHypothesis: hypothesis },
      { ...detail(), completedAt },
      { ...detail(), status: 'completed' },
    ]) {
      expect(parseInvestigationDetail(malformed)).toBeNull()
    }
  })

  it('parses documented future-mode records but leaves availability to the backend', async () => {
    expect(parseInvestigationDetail({
      ...detail(),
      kind: 'damageChange',
      connectivityPolicy: 'connected',
    })).toMatchObject({ kind: 'damageChange', connectivityPolicy: 'connected' })

    vi.spyOn(document, 'cookie', 'get').mockReturnValue(`__Host-instadescribe_csrf=${'a'.repeat(43)}`)
    const unavailableFetch = vi.fn().mockResolvedValue(json({
      type: 'https://api.instadescribe.com/problems/investigation_mode_unavailable',
      title: 'Investigation mode unavailable',
      status: 422,
      detail: 'This beta build supports only local geolocation and provenance investigations.',
      instance: '/api/app/v1/investigations',
      code: 'investigation_mode_unavailable',
      requestId: '99999999-9999-4999-8999-999999999999',
      retryable: false,
    }, 422))

    await expect(createInvestigation({
      name: 'Future change review',
      kind: 'damageChange',
      connectivityPolicy: 'connected',
      video: {
        fileName: 'source.mp4',
        contentType: 'video/mp4',
        sizeBytes: 1024,
        durationSeconds: 30,
      },
      source: {
        legalBasis: 'analystAuthorized',
        redistributionPolicy: 'metadataOnly',
      },
    }, 'future-mode-attempt-1', unavailableFetch)).rejects.toMatchObject({
      code: 'investigation_mode_unavailable',
      status: 422,
    })
    expect(JSON.parse(String(unavailableFetch.mock.calls[0]?.[1]?.body))).toMatchObject({
      kind: 'damageChange',
      connectivityPolicy: 'connected',
    })
  })

  it('loads only exact list envelopes', async () => {
    const okFetch = vi.fn().mockResolvedValue(json({ data: [summary()] }))
    await expect(listInvestigations(undefined, okFetch)).resolves.toHaveLength(1)
    expect(okFetch).toHaveBeenCalledWith('/api/bff/cloud/investigations', expect.objectContaining({ credentials: 'same-origin' }))

    const extraFetch = vi.fn().mockResolvedValue(json({ data: [summary()], next: 'hidden-cursor' }))
    await expect(listInvestigations(undefined, extraFetch)).rejects.toMatchObject({ code: 'invalid_response' })
  })

  it('accepts a sparse detail in the create envelope', async () => {
    vi.spyOn(document, 'cookie', 'get').mockReturnValue(`__Host-instadescribe_csrf=${'a'.repeat(43)}`)
    const createFetch = vi.fn().mockResolvedValue(json({
      investigation: {
        ...detail(),
        connectivityPolicy: 'local',
        status: 'awaitingUpload',
        modelProvenance: { executedLocally: true },
        runtimeProvenance: {},
      },
      upload: {
        method: 'POST',
        url: 'https://uploads.example.test/source',
        fields: { key: 'private/source.mp4' },
        expiresAt: '2026-08-30T09:15:00Z',
      },
    }, 201))

    await expect(createInvestigation({
      name: 'Local provenance run',
      kind: 'geolocateProvenance',
      connectivityPolicy: 'local',
      video: {
        fileName: 'source.mp4',
        contentType: 'video/mp4',
        sizeBytes: 1024,
        durationSeconds: 30,
      },
      source: {
        legalBasis: 'analystAuthorized',
        redistributionPolicy: 'metadataOnly',
        retentionDays: 30,
      },
    }, 'create-local-attempt-1', createFetch)).resolves.toMatchObject({
      investigation: {
        modelProvenance: { modelId: null, modelDigest: null, promptDigest: null, executedLocally: true },
        runtimeProvenance: { runtime: null, runtimeVersion: null, platform: null },
      },
    })
    expect(new Headers(createFetch.mock.calls[0]?.[1]?.headers).get('idempotency-key'))
      .toBe('create-local-attempt-1')
  })

  it('rejects invalid caller-owned idempotency keys before any request', async () => {
    const createFetch = vi.fn()
    const input = {
      name: 'Local provenance run',
      kind: 'geolocateProvenance' as const,
      connectivityPolicy: 'local' as const,
      video: {
        fileName: 'source.mp4',
        contentType: 'video/mp4' as const,
        sizeBytes: 1024,
        durationSeconds: 30,
      },
      source: {
        legalBasis: 'analystAuthorized',
        redistributionPolicy: 'metadataOnly',
      },
    }
    for (const key of ['', 'contains a space', 'line\nbreak', 'non-ascii-é', 'x'.repeat(256)]) {
      await expect(createInvestigation(input, key, createFetch))
        .rejects.toMatchObject({ code: 'invalid_idempotency_key' })
    }
    expect(createFetch).not.toHaveBeenCalled()
  })

  it('forwards caller-owned idempotency keys for cancel and finalization', async () => {
    vi.spyOn(document, 'cookie', 'get').mockReturnValue(`__Host-instadescribe_csrf=${'a'.repeat(43)}`)
    const hypothesis = { id: 'candidate-a', label: 'Candidate A' }
    const operationFetch = vi.fn(async (url: string) => {
      if (url.endsWith('/cancel')) return json({ ...detail(), status: 'cancelled' })
      return json({
        investigation: {
          ...detail(),
          status: 'completed',
          finalHypothesis: hypothesis,
          completedAt: '2026-08-30T09:20:00Z',
        },
        decision: {
          decisionId: DECISION_ID,
          status: 'final',
          evidenceDecisions: [{ evidenceId: EVIDENCE_ID, decision: 'accepted' }],
          finalHypothesis: hypothesis,
          abstained: false,
          abstentionReason: null,
          notes: null,
          decidedByPrincipalId: PRINCIPAL_ID,
          createdAt: '2026-08-30T09:20:00Z',
        },
      })
    })

    await expect(cancelInvestigation(
      INVESTIGATION_ID,
      'cancel-attempt-1',
      operationFetch,
    )).resolves.toMatchObject({ status: 'cancelled' })
    await expect(finalizeInvestigation(
      INVESTIGATION_ID,
      {
        evidenceDecisions: [{ evidenceId: EVIDENCE_ID, decision: 'accepted' }],
        finalHypothesis: hypothesis,
        abstain: false,
      },
      'finalize-attempt-1',
      operationFetch,
    )).resolves.toMatchObject({ investigation: { status: 'completed' } })

    expect(operationFetch.mock.calls.map(([, init]) => (
      new Headers(init?.headers).get('idempotency-key')
    ))).toEqual(['cancel-attempt-1', 'finalize-attempt-1'])
  })

  it('loads the workspace in parallel and rejects non-normalized beliefs', async () => {
    const investigationFetch = vi.fn(async (url: string) => {
      if (url === `/api/bff/cloud/investigations/${INVESTIGATION_ID}`) return json(detail())
      if (url.endsWith('/keyframes')) return json({ data: [keyframe()] })
      if (url.endsWith('/evidence')) return json({ data: [evidence()] })
      if (url.endsWith('/beliefs')) return json({ data: [belief()] })
      if (url.endsWith('/steps')) return json({ data: [step()] })
      if (url === '/api/bff/session') return json({ user: {
        email: 'reviewer@example.com',
        displayName: 'Reviewer',
        organizationId: 'org-1',
        role: 'reviewer',
      } })
      return json({}, 404)
    })

    await expect(loadInvestigationWorkspace(INVESTIGATION_ID, undefined, investigationFetch)).resolves.toMatchObject({
      role: 'reviewer',
      evidence: [{ evidenceId: EVIDENCE_ID, observation: { summary: evidence().observation.summary } }],
      beliefs: [{
        beliefSnapshotId: BELIEF_ID,
        candidates: [{ countryCode: null, latitude: null, longitude: null }, { countryCode: null }],
      }],
    })

    const malformedFetch = vi.fn(async (url: string, init?: RequestInit) => {
      const response = await investigationFetch(url, init)
      if (url.endsWith('/beliefs')) return json({ data: [{
        ...belief(),
        candidates: [
          { id: 'candidate-a', label: 'Candidate A', probability: 0.7 },
          { id: 'candidate-b', label: 'Candidate B', probability: 0.4 },
        ],
      }] })
      return response
    })
    await expect(loadInvestigationWorkspace(INVESTIGATION_ID, undefined, malformedFetch)).rejects.toBeInstanceOf(InvestigationApiError)
  })

  it('validates optional observation details without retaining internal material', async () => {
    const fetchWithDetails = (details: unknown) => vi.fn(async (url: string) => {
      const observation = { summary: 'Visible clue', details }
      if (url === `/api/bff/cloud/investigations/${INVESTIGATION_ID}`) return json(detail())
      if (url.endsWith('/keyframes')) return json({ data: [{ ...keyframe(), observation }] })
      if (url.endsWith('/evidence')) return json({ data: [{ ...evidence(), observation }] })
      if (url.endsWith('/beliefs')) return json({ data: [belief()] })
      if (url.endsWith('/steps')) return json({ data: [step()] })
      if (url === '/api/bff/session') return json({ user: {
        email: 'reviewer@example.com',
        displayName: 'Reviewer',
        organizationId: 'org-1',
        role: 'reviewer',
      } })
      return json({}, 404)
    })

    const sentinel = 'INTERNAL_OBSERVATION_SENTINEL'
    const workspace = await loadInvestigationWorkspace(
      INVESTIGATION_ID,
      undefined,
      fetchWithDetails({ nested: { sentinel } }),
    )
    expect(workspace.evidence[0]?.observation).toEqual({ summary: 'Visible clue' })
    expect(workspace.keyframes[0]?.observation).toEqual({ summary: 'Visible clue' })
    expect(JSON.stringify(workspace)).not.toContain(sentinel)

    await expect(loadInvestigationWorkspace(
      INVESTIGATION_ID,
      undefined,
      fetchWithDetails({ oversized: 'x'.repeat(16_385) }),
    )).rejects.toMatchObject({ code: 'invalid_response' })

    const tooManyNodes = {
      values: Array.from({ length: 256 }, () => Array.from({ length: 8 }, () => 0)),
    }
    await expect(loadInvestigationWorkspace(
      INVESTIGATION_ID,
      undefined,
      fetchWithDetails(tooManyNodes),
    )).rejects.toMatchObject({ code: 'invalid_response' })

    let tooDeep: Record<string, unknown> = { leaf: true }
    for (let depth = 0; depth < 7; depth += 1) tooDeep = { nested: tooDeep }
    await expect(loadInvestigationWorkspace(
      INVESTIGATION_ID,
      undefined,
      fetchWithDetails(tooDeep),
    )).rejects.toMatchObject({ code: 'invalid_response' })
  })

  it('rejects unknown nested evidence and belief fields', async () => {
    const workspaceFetch = (override: 'evidence' | 'beliefs') => vi.fn(async (url: string) => {
      if (url === `/api/bff/cloud/investigations/${INVESTIGATION_ID}`) return json(detail())
      if (url.endsWith('/keyframes')) return json({ data: [keyframe()] })
      if (url.endsWith('/evidence')) return json({ data: [{
        ...evidence(),
        observation: override === 'evidence'
          ? { summary: 'Visible clue', rawModelOutput: 'must not cross the boundary' }
          : evidence().observation,
      }] })
      if (url.endsWith('/beliefs')) return json({ data: [{
        ...belief(),
        candidates: override === 'beliefs'
          ? [{ ...belief().candidates[0], providerScore: 0.7 }, belief().candidates[1]]
          : belief().candidates,
      }] })
      if (url.endsWith('/steps')) return json({ data: [step()] })
      if (url === '/api/bff/session') return json({ user: {
        email: 'reviewer@example.com',
        displayName: 'Reviewer',
        organizationId: 'org-1',
        role: 'reviewer',
      } })
      return json({}, 404)
    })

    await expect(loadInvestigationWorkspace(INVESTIGATION_ID, undefined, workspaceFetch('evidence')))
      .rejects.toMatchObject({ code: 'invalid_response' })
    await expect(loadInvestigationWorkspace(INVESTIGATION_ID, undefined, workspaceFetch('beliefs')))
      .rejects.toMatchObject({ code: 'invalid_response' })
  })

  it('rejects unknown roles and extra session fields', async () => {
    const session = {
      email: 'reviewer@example.com',
      displayName: 'Reviewer',
      organizationId: 'org-1',
      role: 'reviewer',
    }
    await expect(loadBrowserSession(undefined, vi.fn().mockResolvedValue(json({
      user: { ...session, role: 'operator' },
    })))).rejects.toMatchObject({ code: 'invalid_response' })
    await expect(loadBrowserSession(undefined, vi.fn().mockResolvedValue(json({
      user: { ...session, providerToken: 'must-not-cross' },
    })))).rejects.toMatchObject({ code: 'invalid_response' })
  })

  it('parses a final report and keeps every evidence decision explicit', async () => {
    const hypothesis = { id: 'candidate-a', label: 'Candidate A', countryCode: 'GB' }
    const reportFetch = vi.fn().mockResolvedValue(json({
      investigation: { ...detail(), status: 'completed', finalHypothesis: hypothesis, completedAt: '2026-08-30T09:20:00Z' },
      source: {
        sourceRecordId: '99999999-9999-4999-8999-999999999999',
        collectedAt: '2026-08-30T08:55:00Z',
        legalBasis: 'publicDomain',
        redistributionPolicy: 'metadataOnly',
        retentionDays: 30,
        purgeAfter: '2026-09-29T08:55:00Z',
      },
      decision: {
        decisionId: DECISION_ID,
        status: 'final',
        evidenceDecisions: [{ evidenceId: EVIDENCE_ID, decision: 'accepted' }],
        finalHypothesis: hypothesis,
        abstained: false,
        abstentionReason: null,
        notes: null,
        decidedByPrincipalId: PRINCIPAL_ID,
        createdAt: '2026-08-30T09:20:00Z',
      },
      evidence: [evidence()],
      latestBelief: belief(),
    }))
    await expect(loadInvestigationReport(INVESTIGATION_ID, undefined, reportFetch)).resolves.toMatchObject({
      investigation: { finalHypothesis: { countryCode: 'GB', region: null, latitude: null } },
      source: { publisherUrl: null, publishedAt: null, license: null, mediaSha256: null },
      decision: {
        finalHypothesis: { countryCode: 'GB', region: null, latitude: null },
        evidenceDecisions: [{ evidenceId: EVIDENCE_ID, decision: 'accepted' }],
      },
    })

    const invalidSourceFetch = vi.fn().mockResolvedValue(json({
      investigation: { ...detail(), status: 'completed', finalHypothesis: hypothesis, completedAt: '2026-08-30T09:20:00Z' },
      source: {
        sourceRecordId: '99999999-9999-4999-8999-999999999999',
        collectedAt: '2026-08-30T08:55:00Z',
        legalBasis: 'publicDomain',
        redistributionPolicy: 'metadataOnly',
        retentionDays: 30,
        purgeAfter: '2026-09-29T08:55:00Z',
        rawMediaUrl: 'https://private.example.test/source.mp4',
      },
      decision: null,
      evidence: [],
      latestBelief: null,
    }))
    await expect(loadInvestigationReport(INVESTIGATION_ID, undefined, invalidSourceFetch))
      .rejects.toMatchObject({ code: 'invalid_response' })
  })

  it('maps product roles without expanding write authority', () => {
    const expected = {
      owner: { create: true, cancel: true, review: true, finalize: true },
      editor: { create: true, cancel: true, review: false, finalize: false },
      reviewer: { create: false, cancel: false, review: true, finalize: true },
      viewer: { create: false, cancel: false, review: false, finalize: false },
    } as const
    for (const [role, permissions] of Object.entries(expected)) {
      expect({
        create: canCreateInvestigation(role as keyof typeof expected),
        cancel: canCancelInvestigation(role as keyof typeof expected),
        review: canReviewInvestigation(role as keyof typeof expected),
        finalize: canFinalizeInvestigation(role as keyof typeof expected),
      }).toEqual(permissions)
    }
  })
})
