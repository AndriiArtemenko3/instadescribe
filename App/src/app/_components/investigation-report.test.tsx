// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { InvestigationReport } from './investigation-report'

const INVESTIGATION_ID = '11111111-1111-4111-8111-111111111111'

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } })
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('investigation report source lineage', () => {
  it('renders only recorded source values and labels probabilities as a baseline posterior', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({
      investigation: {
        investigationId: INVESTIGATION_ID,
        projectId: '22222222-2222-4222-8222-222222222222',
        jobId: '33333333-3333-4333-8333-333333333333',
        name: 'Rights-cleared source report',
        kind: 'geolocateProvenance',
        connectivityPolicy: 'local',
        status: 'completed',
        abstained: false,
        calibratedConfidence: null,
        createdAt: '2026-08-30T09:00:00Z',
        updatedAt: '2026-08-30T09:10:00Z',
        traceId: null,
        modelProvenance: { executedLocally: true },
        runtimeProvenance: {},
        finalHypothesis: { id: 'candidate-a', label: 'Candidate A' },
        abstentionReason: null,
        completedAt: '2026-08-30T09:10:00Z',
      },
      source: {
        sourceRecordId: '44444444-4444-4444-8444-444444444444',
        publisherUrl: 'https://publisher.example.test/video',
        publishedAt: '2026-08-30T08:00:00Z',
        collectedAt: '2026-08-30T08:30:00Z',
        legalBasis: 'licensed',
        license: 'CC BY 4.0',
        mediaSha256: 'a'.repeat(64),
        redistributionPolicy: 'metadataOnly',
        retentionDays: 30,
        purgeAfter: '2026-09-29T08:30:00Z',
      },
      decision: {
        decisionId: '66666666-6666-4666-8666-666666666666',
        status: 'final',
        evidenceDecisions: [
          {
            evidenceId: '77777777-7777-4777-8777-777777777777',
            decision: 'accepted',
          },
          {
            evidenceId: '99999999-9999-4999-8999-999999999999',
            decision: 'accepted',
          },
          {
            evidenceId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
            decision: 'rejected',
          },
        ],
        finalHypothesis: { id: 'candidate-a', label: 'Candidate A' },
        abstained: false,
        abstentionReason: null,
        notes: null,
        decidedByPrincipalId: '88888888-8888-4888-8888-888888888888',
        createdAt: '2026-08-30T09:10:00Z',
      },
      evidence: [
        {
          evidenceId: '77777777-7777-4777-8777-777777777777',
          kind: 'visual',
          observation: { summary: 'A model-proposed visual clue.' },
          frameTimeMs: 2_000,
          bbox: null,
          polarity: 'supports',
          reliability: 0.8,
          verificationState: 'proposed',
          correlationGroup: 'frame-2',
          createdAt: '2026-08-30T09:05:00Z',
        },
        {
          evidenceId: '99999999-9999-4999-8999-999999999999',
          kind: 'metadata',
          observation: { summary: 'A deterministic verifier output.' },
          frameTimeMs: null,
          bbox: null,
          polarity: 'supports',
          reliability: 1,
          verificationState: 'verified',
          correlationGroup: 'source-record',
          createdAt: '2026-08-30T09:06:00Z',
        },
        {
          evidenceId: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
          kind: 'visual',
          observation: { summary: 'A verifier rejected this observation.' },
          frameTimeMs: 3_000,
          bbox: null,
          polarity: 'neutral',
          reliability: 0,
          verificationState: 'rejected',
          correlationGroup: 'frame-3',
          createdAt: '2026-08-30T09:07:00Z',
        },
      ],
      latestBelief: {
        beliefSnapshotId: '55555555-5555-4555-8555-555555555555',
        sequence: 1,
        candidates: [{ id: 'candidate-a', label: 'Candidate A', probability: 1 }],
        entropy: 0,
        abstained: false,
        createdAt: '2026-08-30T09:09:00Z',
      },
    })))

    render(<InvestigationReport investigationId={INVESTIGATION_ID} />)

    expect(await screen.findByRole('heading', { name: 'Rights-cleared source report' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Source lineage' })).toBeTruthy()
    expect(screen.getByRole('link', { name: 'https://publisher.example.test/video' }).getAttribute('href'))
      .toBe('https://publisher.example.test/video')
    expect(screen.getByText('licensed · CC BY 4.0')).toBeTruthy()
    expect(screen.getByText('a'.repeat(64))).toBeTruthy()
    expect(screen.getByText('Lifecycle eligibility tier')).toBeTruthy()
    expect(screen.getByText('Physical removal may occur later.')).toBeTruthy()
    expect(screen.getByText('Latest baseline posterior (uncalibrated)')).toBeTruthy()
    expect(screen.getAllByText('Candidate A')).toHaveLength(2)
    expect(screen.getByText('Proposed observation')).toBeTruthy()
    expect(screen.getByText('Verified by tool')).toBeTruthy()
    expect(screen.getByText('Rejected by verifier')).toBeTruthy()
    expect(screen.getByText(/Analyst acceptance is a report disposition/)).toBeTruthy()
  })

  it('renders mandatory abstention and a deterministic fixture without a model claim', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({
      investigation: {
        investigationId: INVESTIGATION_ID,
        projectId: '22222222-2222-4222-8222-222222222222',
        jobId: '33333333-3333-4333-8333-333333333333',
        name: 'Ambiguous synthetic fixture',
        kind: 'geolocateProvenance',
        connectivityPolicy: 'local',
        status: 'completed',
        abstained: true,
        calibratedConfidence: null,
        createdAt: '2026-08-30T09:00:00Z',
        updatedAt: '2026-08-30T09:10:00Z',
        traceId: null,
        modelProvenance: { modelId: 'deterministic-fixture', executedLocally: false },
        runtimeProvenance: {},
        finalHypothesis: null,
        abstentionReason: 'Synthetic evidence did not separate the candidates.',
        completedAt: '2026-08-30T09:10:00Z',
      },
      source: {
        sourceRecordId: '44444444-4444-4444-8444-444444444444',
        publisherUrl: null,
        publishedAt: null,
        collectedAt: '2026-08-30T08:30:00Z',
        legalBasis: 'analystAuthorized',
        license: null,
        mediaSha256: null,
        redistributionPolicy: 'prohibited',
        retentionDays: 7,
        purgeAfter: '2026-09-06T08:30:00Z',
      },
      decision: {
        decisionId: '66666666-6666-4666-8666-666666666666',
        status: 'final',
        evidenceDecisions: [],
        finalHypothesis: null,
        abstained: true,
        abstentionReason: 'Synthetic evidence did not separate the candidates.',
        notes: null,
        decidedByPrincipalId: '88888888-8888-4888-8888-888888888888',
        createdAt: '2026-08-30T09:10:00Z',
      },
      evidence: [],
      latestBelief: {
        beliefSnapshotId: '55555555-5555-4555-8555-555555555555',
        sequence: 1,
        candidates: [
          { id: 'candidate-a', label: 'Candidate A', probability: 0.5 },
          { id: 'candidate-b', label: 'Candidate B', probability: 0.5 },
        ],
        entropy: 0.693,
        abstained: true,
        createdAt: '2026-08-30T09:09:00Z',
      },
    })))

    render(<InvestigationReport investigationId={INVESTIGATION_ID} />)

    expect(await screen.findByRole('heading', { name: 'Ambiguous synthetic fixture' })).toBeTruthy()
    expect(screen.getByText('Abstained')).toBeTruthy()
    expect(screen.getByText('Synthetic evidence did not separate the candidates.')).toBeTruthy()
    expect(screen.getByText('Deterministic fixture · no model inference')).toBeTruthy()
    expect(screen.getByText('0.693')).toBeTruthy()
    expect(screen.queryByText('Non-local reported')).toBeNull()
  })
})
