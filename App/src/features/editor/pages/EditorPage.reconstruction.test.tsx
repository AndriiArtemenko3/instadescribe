// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  fetchCloudOverrides: vi.fn(),
  patchCloudScene: vi.fn(),
  cloudEditorData: {
    manifest: { projectId: 'project-1', jobId: 'job-1' },
    rawScenes: [{
      id: 1,
      sceneNumber: 1,
      sceneKey: 'scene_alpha',
      startSecs: 0,
      endSecs: 4,
      durationSecs: 4,
      text: 'pipeline default',
      template: 'template',
      characterIds: [],
      locked: false,
      needsReview: false,
      active: true,
    }],
    audioEvents: [],
    adGaps: [],
    entities: [],
    scenesLoading: false,
    videoUrl: null,
  },
}))

vi.mock('@/lib/cloudMode', () => ({
  isCloudMode: () => true,
  isCloudSession: () => true,
  cloudApiBase: () => 'http://localhost:8000',
}))

vi.mock('@/lib/cloudApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/cloudApi')>()
  return {
    ...actual,
    fetchCloudOverrides: mocks.fetchCloudOverrides,
    patchCloudScene: mocks.patchCloudScene,
  }
})

vi.mock('../hooks/useCloudEditorData', () => ({
  useCloudEditorData: () => mocks.cloudEditorData,
}))

vi.mock('../components/SceneListPanel', () => ({
  SceneListPanel: ({
    scenes,
    onActiveToggle,
  }: {
    scenes: Array<{ id: number; text: string; active: boolean }>
    onActiveToggle: (sceneId: number) => void
  }) => (
    <div>
      <div data-testid="mounted-scene-state">
        {scenes.map((scene) => `${scene.text}|${scene.active ? 'active' : 'inactive'}`).join(',')}
      </div>
      {scenes.map((scene) => (
        <button
          key={scene.id}
          type="button"
          aria-label={`Toggle scene ${scene.id}`}
          onClick={() => onActiveToggle(scene.id)}
        >Toggle</button>
      ))}
    </div>
  ),
}))
vi.mock('../components/VideoPanel', () => ({ VideoPanel: () => <div /> }))
vi.mock('../components/ScriptPanel', () => ({
  ScriptPanel: ({
    scene,
    onAdChange,
    onApply,
    cloudReviewEnabled,
    cloudReviewStatus,
    cloudReviewLoading,
    cloudReviewUnavailable,
    onCloudReview,
    justApplied,
  }: {
    scene: { id: number; text: string } | null
    onAdChange: (sceneId: number, text: string) => void
    onApply: (sceneId: number) => void
    cloudReviewEnabled?: boolean
    cloudReviewStatus?: string
    cloudReviewLoading?: boolean
    cloudReviewUnavailable?: boolean
    onCloudReview?: (sceneId: number, status: 'approved' | 'rejected') => void
    justApplied: boolean
  }) => scene ? (
    <div>
      <input
        aria-label="Description text"
        value={scene.text}
        onChange={(event) => onAdChange(scene.id, event.target.value)}
      />
      <button type="button" onClick={() => onApply(scene.id)}>Apply</button>
      {cloudReviewEnabled && onCloudReview && (
        <>
          <p data-testid="review-state">{
            cloudReviewUnavailable ? 'unavailable' : cloudReviewLoading ? 'loading' : cloudReviewStatus
          }</p>
          {cloudReviewStatus && !cloudReviewUnavailable && !cloudReviewLoading && (
            <>
              <button type="button" onClick={() => onCloudReview(scene.id, 'approved')}>Approve</button>
              <button type="button" onClick={() => onCloudReview(scene.id, 'rejected')}>Reject</button>
            </>
          )}
        </>
      )}
      {justApplied && <p>Changes applied</p>}
    </div>
  ) : null,
}))
vi.mock('../components/CharactersPanel', () => ({ CharactersPanel: () => <div /> }))
vi.mock('../components/QualityPanel', () => ({ QualityPanel: () => <div /> }))
vi.mock('@/features/study/EditorTour', () => ({ EditorTour: () => null }))
vi.mock('@/features/study/HelpPanel', () => ({ HelpPanel: () => null }))

import EditorPage from './EditorPage'
import { CloudApiError } from '@/lib/cloudApi'
import { persistSceneActive, persistSceneText } from '@/lib/persistence'
import { useAppStore } from '@/store/appStore'

function serverOverride(ad: string, active: boolean, version = 1) {
  return {
    ad,
    active,
    locked: false,
    version,
    reviewStatus: 'edited' as const,
    reviewedAt: null,
    updatedAt: '2026-08-07T12:00:00Z',
  }
}

function reviewedOverride(
  ad: string,
  active: boolean,
  version: number,
  reviewStatus: 'approved' | 'rejected',
) {
  return {
    ...serverOverride(ad, active, version),
    reviewStatus,
    reviewedAt: '2026-08-07T12:00:00Z',
  }
}

function renderMountedEditor() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/editor/project-1']}>
        <Routes>
          <Route path="/editor/:projectId" element={<EditorPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  sessionStorage.clear()
  localStorage.clear()
  mocks.fetchCloudOverrides.mockReset()
  mocks.patchCloudScene.mockReset()
  mocks.fetchCloudOverrides.mockResolvedValue({
    scene_alpha: serverOverride('server A', true),
  })
  mocks.patchCloudScene.mockResolvedValue({
    projectId: 'project-1',
    jobId: 'job-1',
    sceneId: 'scene_alpha',
    version: 2,
    reviewStatus: 'edited',
    reviewedAt: null,
    updatedAt: '2026-08-07T12:00:00Z',
    override: {
      ...serverOverride('server A', true, 2),
      reviewStatus: 'edited',
    },
  })
  useAppStore.setState({
    projects: [{
      id: 'project-1',
      jobId: 'job-1',
      name: 'Editor reconstruction',
      status: 'ready',
      createdAt: '2026-08-07T10:00:00Z',
      durationSecs: 4,
    }],
    isAuthenticated: true,
  })
  persistSceneText('project-1', 1, 'local B', 'job-1')
  persistSceneActive('project-1', 1, false, 'job-1')
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('mounted cloud editor reconstruction', () => {
  it('renders local draft B over server override A after initial mount and remount', async () => {
    const first = renderMountedEditor()
    await waitFor(() => expect(mocks.fetchCloudOverrides).toHaveBeenCalledWith('job-1'))
    expect((await screen.findByTestId('mounted-scene-state')).textContent).toContain('local B|inactive')
    expect(screen.getByTestId('mounted-scene-state').textContent).not.toContain('server A')
    first.unmount()

    renderMountedEditor()
    await waitFor(() => expect(mocks.fetchCloudOverrides).toHaveBeenCalledTimes(2))
    expect((await screen.findByTestId('mounted-scene-state')).textContent).toContain('local B|inactive')
    expect(screen.getByTestId('mounted-scene-state').textContent).not.toContain('server A')
  })

  it('does not show Changes applied for A while newer B is queued', async () => {
    sessionStorage.clear()
    persistSceneText('project-1', 1, 'A', 'job-1')
    const first = deferredPatch()
    const second = deferredPatch()
    mocks.patchCloudScene
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise)
    renderMountedEditor()
    const input = await screen.findByRole('textbox', { name: 'Description text' })
    await waitFor(() => expect((input as HTMLInputElement).value).toBe('A'))

    fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
    fireEvent.change(input, { target: { value: 'B' } })
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
    await waitFor(() => expect(mocks.patchCloudScene).toHaveBeenCalledTimes(1))

    await act(async () => first.resolve(patchResponse('A', 2)))
    await waitFor(() => expect(mocks.patchCloudScene).toHaveBeenCalledTimes(2))
    expect(screen.queryByText('Changes applied')).toBeNull()
    expect((screen.getByRole('textbox', { name: 'Description text' }) as HTMLInputElement).value).toBe('B')

    await act(async () => second.resolve(patchResponse('B', 3)))
    expect(await screen.findByText('Changes applied')).toBeTruthy()
    expect(mocks.patchCloudScene.mock.calls.map(([, , patch]) => patch.ad)).toEqual(['A', 'B'])
    expect(mocks.patchCloudScene.mock.calls.map(([, , , version]) => version)).toEqual([1, 2])
    expect(sessionStorage.getItem('instascribe:project-1:job-1:edits')).toBeNull()

    const mounted = screen.getByTestId('mounted-scene-state')
    expect(mounted.textContent).toContain('B|active')
    cleanup()
    mocks.fetchCloudOverrides.mockResolvedValueOnce({
      scene_alpha: serverOverride('B', true, 3),
    })
    renderMountedEditor()
    await waitFor(() => expect(mocks.fetchCloudOverrides).toHaveBeenCalledTimes(2))
    expect((await screen.findByRole('textbox', { name: 'Description text' }) as HTMLInputElement).value).toBe('B')
    expect(screen.getByTestId('mounted-scene-state').textContent).toContain('B|active')
  })

  it('serializes active true then false, clears the final draft, and remounts inactive', async () => {
    sessionStorage.clear()
    persistSceneActive('project-1', 1, false, 'job-1')
    mocks.fetchCloudOverrides.mockReset().mockResolvedValue({
      scene_alpha: serverOverride('server A', false),
    })
    const first = deferredPatch()
    const second = deferredPatch()
    mocks.patchCloudScene
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise)
    renderMountedEditor()
    const toggle = await screen.findByRole('button', { name: 'Toggle scene 1' })
    await waitFor(() => expect(screen.getByTestId('mounted-scene-state').textContent).toContain('inactive'))

    fireEvent.click(toggle)
    await waitFor(() => {
      expect(mocks.patchCloudScene).toHaveBeenCalledTimes(1)
      expect(screen.getByTestId('mounted-scene-state').textContent).toContain('active')
    })
    fireEvent.click(screen.getByRole('button', { name: 'Toggle scene 1' }))
    await waitFor(() => expect(screen.getByTestId('mounted-scene-state').textContent).toContain('inactive'))
    expect(mocks.patchCloudScene).toHaveBeenCalledTimes(1)

    await act(async () => first.resolve(activePatchResponse(true, 2)))
    await waitFor(() => expect(mocks.patchCloudScene).toHaveBeenCalledTimes(2))
    expect(sessionStorage.getItem('instascribe:project-1:job-1:edits')).toContain('false')
    await act(async () => second.resolve(activePatchResponse(false, 3)))
    expect(mocks.patchCloudScene.mock.calls.map(([, , patch]) => patch.active)).toEqual([true, false])
    await waitFor(() => {
      expect(sessionStorage.getItem('instascribe:project-1:job-1:edits')).toBeNull()
      expect(screen.getByTestId('mounted-scene-state').textContent).toContain('inactive')
    })

    cleanup()
    mocks.fetchCloudOverrides.mockResolvedValueOnce({
      scene_alpha: serverOverride('server A', false, 3),
    })
    renderMountedEditor()
    await waitFor(() => expect(mocks.fetchCloudOverrides).toHaveBeenCalledTimes(2))
    await waitFor(() => {
      expect(screen.getByTestId('mounted-scene-state').textContent).toContain('server A|inactive')
    })
  })

  it('fences a stale mount GET after B succeeds, clears only the draft, and remounts from server B', async () => {
    const staleGet = deferredValue<Record<string, { ad: string; active: boolean }>>()
    mocks.fetchCloudOverrides.mockReset().mockReturnValueOnce(staleGet.promise)
    mocks.patchCloudScene.mockResolvedValueOnce(patchResponse('local B', 2))

    const first = renderMountedEditor()
    const input = await screen.findByRole('textbox', { name: 'Description text' })
    await waitFor(() => expect((input as HTMLInputElement).value).toBe('local B'))
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }))

    expect(await screen.findByText('Changes applied')).toBeTruthy()
    expect(sessionStorage.getItem('instascribe:project-1:job-1:edits')).toBeNull()
    expect((screen.getByRole('textbox', { name: 'Description text' }) as HTMLInputElement).value).toBe('local B')

    await act(async () => staleGet.resolve({
      scene_alpha: serverOverride('server A', true),
    }))
    await waitFor(() => expect(mocks.fetchCloudOverrides).toHaveBeenCalledTimes(1))
    expect((screen.getByRole('textbox', { name: 'Description text' }) as HTMLInputElement).value).toBe('local B')
    expect(screen.queryByDisplayValue('server A')).toBeNull()
    first.unmount()

    mocks.fetchCloudOverrides.mockResolvedValueOnce({
      scene_alpha: serverOverride('local B', true, 2),
    })
    renderMountedEditor()
    await waitFor(() => expect(mocks.fetchCloudOverrides).toHaveBeenCalledTimes(2))
    expect((await screen.findByRole('textbox', { name: 'Description text' }) as HTMLInputElement).value).toBe('local B')
    expect(sessionStorage.getItem('instascribe:project-1:job-1:edits')).toBeNull()
  })

  it('preserves B across unmount while PATCH B and the next mount stale GET A are pending', async () => {
    const patch = deferredPatch()
    mocks.patchCloudScene.mockReturnValueOnce(patch.promise)
    const first = renderMountedEditor()
    const firstInput = await screen.findByRole('textbox', { name: 'Description text' })
    await waitFor(() => expect((firstInput as HTMLInputElement).value).toBe('local B'))
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
    await waitFor(() => expect(mocks.patchCloudScene).toHaveBeenCalledTimes(1))
    first.unmount()

    const staleGet = deferredValue<Record<string, { ad: string; active: boolean }>>()
    mocks.fetchCloudOverrides.mockReset().mockReturnValueOnce(staleGet.promise)
    renderMountedEditor()
    const remountedInput = await screen.findByRole('textbox', { name: 'Description text' })
    await waitFor(() => expect((remountedInput as HTMLInputElement).value).toBe('local B'))

    await act(async () => patch.resolve(patchResponse('local B', 2)))
    expect(sessionStorage.getItem('instascribe:project-1:job-1:edits')).toContain('local B')
    await act(async () => staleGet.resolve({
      scene_alpha: serverOverride('server A', true),
    }))
    await waitFor(() => expect(mocks.fetchCloudOverrides).toHaveBeenCalledTimes(1))
    expect((screen.getByRole('textbox', { name: 'Description text' }) as HTMLInputElement).value).toBe('local B')
    expect(screen.queryByDisplayValue('server A')).toBeNull()
  })

  it('shows generated only for an absent row and persists an explicit approval with version zero', async () => {
    sessionStorage.clear()
    mocks.fetchCloudOverrides.mockReset().mockResolvedValue({})
    mocks.patchCloudScene.mockResolvedValueOnce(reviewPatchResponse(
      'pipeline default',
      1,
      'approved',
    ))
    renderMountedEditor()

    await waitFor(() => expect(screen.getByTestId('review-state').textContent).toBe('generated'))
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))
    await waitFor(() => expect(mocks.patchCloudScene).toHaveBeenCalledTimes(1))
    const [, sceneId, patch, expectedVersion] = mocks.patchCloudScene.mock.calls[0]
    expect(sceneId).toBe('scene_alpha')
    expect(expectedVersion).toBe(0)
    expect(patch).toMatchObject({
      ad: 'pipeline default',
      active: true,
      locked: false,
      voice: 'onyx',
      speed: 1,
      reviewStatus: 'approved',
    })
    await waitFor(() => expect(screen.getByTestId('review-state').textContent).toBe('approved'))
  })

  it('does not claim Generated while the authoritative overrides map is still loading', async () => {
    sessionStorage.clear()
    const pending = deferredValue<Record<string, ReturnType<typeof serverOverride>>>()
    mocks.fetchCloudOverrides.mockReset().mockReturnValueOnce(pending.promise)
    renderMountedEditor()

    expect((await screen.findByTestId('review-state')).textContent).toBe('loading')
    expect(screen.queryByText('generated', { exact: false })).toBeNull()

    await act(async () => pending.resolve({}))
    await waitFor(() => expect(screen.getByTestId('review-state').textContent).toBe('generated'))
  })

  it('shows review state as unavailable rather than Generated when the initial fetch fails', async () => {
    sessionStorage.clear()
    mocks.fetchCloudOverrides.mockReset().mockRejectedValueOnce(new Error('private storage detail'))
    renderMountedEditor()

    await waitFor(() => expect(screen.getByTestId('review-state').textContent).toBe('unavailable'))
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull()
    expect(screen.queryByText('generated', { exact: false })).toBeNull()
  })

  it('refreshes after stale_version while retaining the visible local draft', async () => {
    mocks.fetchCloudOverrides.mockReset()
      .mockResolvedValueOnce({ scene_alpha: serverOverride('server A', true, 4) })
      .mockResolvedValueOnce({ scene_alpha: reviewedOverride('server C', true, 5, 'approved') })
    mocks.patchCloudScene.mockRejectedValueOnce(
      new CloudApiError('conflict', 409, 'stale_version'),
    )
    renderMountedEditor()
    const input = await screen.findByRole('textbox', { name: 'Description text' })
    await waitFor(() => expect((input as HTMLInputElement).value).toBe('local B'))

    fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
    await waitFor(() => expect(mocks.patchCloudScene).toHaveBeenCalledTimes(1))
    expect(mocks.patchCloudScene.mock.calls[0][3]).toBe(4)
    expect((await screen.findByRole('alert')).textContent).toContain('changed elsewhere')
    await waitFor(() => expect(mocks.fetchCloudOverrides).toHaveBeenCalledTimes(2))
    expect((screen.getByRole('textbox', { name: 'Description text' }) as HTMLInputElement).value).toBe('local B')
    expect(sessionStorage.getItem('instascribe:project-1:job-1:edits')).toContain('local B')
    expect(screen.getByTestId('review-state').textContent).toBe('approved')
  })

  it('does not claim a refresh succeeded when the explicit post-conflict fetch fails', async () => {
    mocks.fetchCloudOverrides.mockReset()
      .mockResolvedValueOnce({ scene_alpha: serverOverride('server A', true, 4) })
      .mockRejectedValueOnce(new Error('private storage detail'))
    mocks.patchCloudScene.mockRejectedValueOnce(
      new CloudApiError('conflict', 409, 'stale_version'),
    )
    renderMountedEditor()
    const input = await screen.findByRole('textbox', { name: 'Description text' })
    await waitFor(() => expect((input as HTMLInputElement).value).toBe('local B'))

    fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
    await waitFor(() => expect(mocks.fetchCloudOverrides).toHaveBeenCalledTimes(2))
    expect((await screen.findByRole('alert')).textContent).toMatch(/could not be loaded.*refresh/i)
    expect(screen.getByTestId('review-state').textContent).toBe('unavailable')
    expect((screen.getByRole('textbox', { name: 'Description text' }) as HTMLInputElement).value).toBe('local B')
    expect(sessionStorage.getItem('instascribe:project-1:job-1:edits')).toContain('local B')
  })
})

function deferredPatch() {
  let resolve: (value: ReturnType<typeof patchResponse>) => void = () => {}
  const promise = new Promise<ReturnType<typeof patchResponse>>((done) => { resolve = done })
  return { promise, resolve }
}

function deferredValue<T>() {
  let resolve: (value: T) => void = () => {}
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

function patchResponse(ad: string, version: number) {
  const updatedAt = '2026-08-07T12:00:00Z'
  return {
    projectId: 'project-1',
    jobId: 'job-1',
    sceneId: 'scene_alpha',
    version,
    reviewStatus: 'edited' as const,
    reviewedAt: null,
    updatedAt,
    override: {
      ...serverOverride(ad, true, version),
      reviewStatus: 'edited' as const,
      updatedAt,
    },
  }
}

function activePatchResponse(active: boolean, version: number) {
  const updatedAt = '2026-08-07T12:00:00Z'
  return {
    projectId: 'project-1',
    jobId: 'job-1',
    sceneId: 'scene_alpha',
    version,
    reviewStatus: 'edited' as const,
    reviewedAt: null,
    updatedAt,
    override: {
      ...serverOverride('server A', active, version),
      reviewStatus: 'edited' as const,
      updatedAt,
    },
  }
}

function reviewPatchResponse(
  ad: string,
  version: number,
  reviewStatus: 'approved' | 'rejected',
) {
  const updatedAt = '2026-08-07T12:00:00Z'
  return {
    projectId: 'project-1',
    jobId: 'job-1',
    sceneId: 'scene_alpha',
    version,
    reviewStatus,
    reviewedAt: updatedAt,
    updatedAt,
    override: reviewedOverride(ad, true, version, reviewStatus),
  }
}
