// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AccountPanel } from './account-panel'

function session(role: 'owner' | 'editor') {
  return new Response(JSON.stringify({
    user: {
      email: `${role}@example.com`,
      displayName: role === 'owner' ? 'Workspace Owner' : 'Editor',
      organizationId: '11111111-1111-4111-8111-111111111111',
      role,
    },
  }), { status: 200, headers: { 'Content-Type': 'application/json' } })
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('account invitation controls', () => {
  it('renders the participant invitation form for an owner', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(session('owner')))

    render(<AccountPanel />)

    expect(await screen.findByText('Invite a participant')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Enable authenticator MFA' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Invite' })).toBeTruthy()
    expect(screen.getByRole('option', { name: 'Editor' })).toBeTruthy()
    expect(screen.getByRole('option', { name: 'Reviewer' })).toBeTruthy()
    expect(screen.getByRole('option', { name: 'Viewer' })).toBeTruthy()
  })

  it('does not expose the invitation form to a non-owner', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(session('editor')))

    render(<AccountPanel />)

    expect(await screen.findByText('editor@example.com')).toBeTruthy()
    expect(screen.queryByText('Invite a participant')).toBeNull()
    expect(screen.getByRole('button', { name: 'Enable authenticator MFA' })).toBeTruthy()
  })
})
