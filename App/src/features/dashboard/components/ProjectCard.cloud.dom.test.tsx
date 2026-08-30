// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/cloudMode', () => ({
  isCloudMode: () => true,
  isCloudSession: () => true,
  cloudApiBase: () => 'http://localhost:8000',
}))

import { ProjectCard } from './ProjectCard'
import type { Project } from '@/types'

const PROJECT: Project = {
  id: '22222222-2222-4222-8222-222222222222',
  projectVersion: 3,
  jobId: '11111111-1111-4111-8111-111111111111',
  name: 'Cloud project',
  status: 'ready',
  createdAt: '2026-08-10T03:00:00Z',
  starred: false,
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('cloud project card actions', () => {
  it('enables star and rename while keeping delete truthfully deferred', async () => {
    const onRename = vi.fn()
    const onToggleStar = vi.fn()
    const onDelete = vi.fn()
    vi.spyOn(window, 'prompt').mockReturnValue('Renamed cloud project')
    render(
      <MemoryRouter>
        <ProjectCard
          project={PROJECT}
          onRename={onRename}
          onToggleStar={onToggleStar}
          onDelete={onDelete}
        />
      </MemoryRouter>,
    )

    const actions = screen.getByRole('button', { name: /project actions/i })
    fireEvent.keyDown(actions, { key: 'ArrowDown' })
    const star = await screen.findByRole('menuitem', { name: 'Star' })
    fireEvent.click(star)
    expect(onToggleStar).toHaveBeenCalledWith(PROJECT.id)

    fireEvent.keyDown(actions, { key: 'ArrowDown' })
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Rename' }))
    expect(onRename).toHaveBeenCalledWith(PROJECT.id, 'Renamed cloud project')

    fireEvent.keyDown(actions, { key: 'ArrowDown' })
    expect((await screen.findByRole('menuitem', { name: /delete.*not available yet/i }))
      .getAttribute('aria-disabled')).toBe('true')
    expect(onDelete).not.toHaveBeenCalled()
  })
})
