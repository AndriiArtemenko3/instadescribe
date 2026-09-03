// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { InvestigationsPanel } from './investigations-panel'

const INVESTIGATION_ID = '11111111-1111-4111-8111-111111111111'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function browserApi(role: 'owner' | 'editor' | 'reviewer' | 'viewer', empty = false) {
  return vi.fn(async (url: string) => {
    if (url === '/api/bff/session') return json({
      user: {
        email: `${role}@example.test`,
        displayName: role,
        organizationId: '22222222-2222-4222-8222-222222222222',
        role,
      },
    })
    if (url === '/api/bff/cloud/investigations') return json({
      data: empty ? [] : [{
        investigationId: INVESTIGATION_ID,
        projectId: '33333333-3333-4333-8333-333333333333',
        jobId: '44444444-4444-4444-8444-444444444444',
        name: 'Synthetic station fixture',
        kind: 'geolocateProvenance',
        connectivityPolicy: 'local',
        status: 'needsReview',
        abstained: true,
        calibratedConfidence: null,
        createdAt: '2026-08-30T10:00:00Z',
        updatedAt: '2026-08-30T10:05:00Z',
      }],
    })
    return json({ code: 'not_found' }, 404)
  })
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('investigation list role boundary', () => {
  it.each([
    ['owner', true],
    ['editor', true],
    ['reviewer', false],
    ['viewer', false],
  ] as const)('shows role-appropriate create controls for %s', async (role, mayCreate) => {
    vi.stubGlobal('fetch', browserApi(role))

    render(<InvestigationsPanel />)

    expect(await screen.findByRole('heading', { name: 'Synthetic station fixture' })).toBeTruthy()
    expect(screen.getByText('Abstained')).toBeTruthy()
    expect(screen.getByText('Calibration pending')).toBeTruthy()
    if (mayCreate) {
      expect(screen.getByRole('link', { name: 'New investigation' })).toBeTruthy()
      expect(screen.queryByText('Read-only access')).toBeNull()
    } else {
      expect(screen.queryByRole('link', { name: 'New investigation' })).toBeNull()
      expect(screen.getByText('Read-only access')).toBeTruthy()
    }
  })

  it('renders an authorised empty state without inventing investigation data', async () => {
    vi.stubGlobal('fetch', browserApi('owner', true))

    render(<InvestigationsPanel />)

    expect(await screen.findByRole('heading', { name: 'No investigations yet' })).toBeTruthy()
    expect(screen.getByRole('link', { name: /Start the first investigation/ })).toBeTruthy()
    expect(screen.getByText(/Only authorised public or licensed footage/)).toBeTruthy()
  })
})
