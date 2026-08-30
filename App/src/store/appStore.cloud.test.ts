import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const dependencies = vi.hoisted(() => ({
  patchCloudProject: vi.fn(),
  fenceCloudProjectReconciliation: vi.fn(),
}))

vi.mock('@/lib/cloudMode', () => ({
  isCloudMode: () => true,
  isCloudSession: () => true,
  cloudApiBase: () => 'http://localhost:8000',
}))

vi.mock('@/lib/cloudApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/cloudApi')>()
  return { ...actual, patchCloudProject: dependencies.patchCloudProject }
})

vi.mock('@/lib/cloudProjectReconciliationFence', () => ({
  fenceCloudProjectReconciliation: dependencies.fenceCloudProjectReconciliation,
}))

import { CloudApiError } from '@/lib/cloudApi'
import { useAppStore } from './appStore'

const PROJECT_ID = '22222222-2222-4222-8222-222222222222'

function deferred<T>() {
  let resolve: (value: T) => void = () => {}
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

beforeEach(() => {
  sessionStorage.clear()
  localStorage.clear()
  dependencies.patchCloudProject.mockReset()
  dependencies.fenceCloudProjectReconciliation.mockReset()
  useAppStore.setState({
    projects: [{
      id: PROJECT_ID,
      projectVersion: 3,
      jobId: '11111111-1111-4111-8111-111111111111',
      name: 'Original',
      starred: false,
      status: 'ready',
      createdAt: '2026-08-10T03:00:00Z',
    }],
    isAuthenticated: true,
  })
})

afterEach(() => {
  useAppStore.setState({ projects: [], isAuthenticated: false })
  vi.restoreAllMocks()
})

describe('cloud project metadata mutations', () => {
  it('persists each returned version and uses it for the next mutation', async () => {
    dependencies.patchCloudProject
      .mockResolvedValueOnce({
        projectId: PROJECT_ID,
        name: 'Renamed',
        starred: false,
        version: 4,
        updatedAt: '2026-08-10T03:01:00Z',
      })
      .mockResolvedValueOnce({
        projectId: PROJECT_ID,
        name: 'Renamed',
        starred: true,
        version: 5,
        updatedAt: '2026-08-10T03:02:00Z',
      })

    await useAppStore.getState().renameProject(PROJECT_ID, '  Renamed  ')
    expect(dependencies.patchCloudProject).toHaveBeenNthCalledWith(1, PROJECT_ID, {
      name: 'Renamed',
      expectedVersion: 3,
    })
    expect(useAppStore.getState().projects[0]).toMatchObject({
      name: 'Renamed',
      projectVersion: 4,
    })

    await useAppStore.getState().toggleStar(PROJECT_ID)
    expect(dependencies.patchCloudProject).toHaveBeenNthCalledWith(2, PROJECT_ID, {
      starred: true,
      expectedVersion: 4,
    })
    expect(useAppStore.getState().projects[0]).toMatchObject({
      starred: true,
      projectVersion: 5,
    })
    expect(dependencies.fenceCloudProjectReconciliation).toHaveBeenCalledTimes(2)
  })

  it('does not apply an optimistic-concurrency loser locally', async () => {
    dependencies.patchCloudProject.mockRejectedValueOnce(
      new CloudApiError('conflict', 409, 'stale_version'),
    )
    await expect(useAppStore.getState().renameProject(PROJECT_ID, 'Lost write'))
      .rejects.toMatchObject({ code: 'stale_version' })
    expect(useAppStore.getState().projects[0]).toMatchObject({
      name: 'Original',
      projectVersion: 3,
    })
    expect(dependencies.fenceCloudProjectReconciliation).not.toHaveBeenCalled()
  })

  it('ignores an entire delayed rename response N+1 after the store reaches N+2', async () => {
    const response = deferred<{
      projectId: string
      name: string
      starred: boolean
      version: number
      updatedAt: string
    }>()
    dependencies.patchCloudProject.mockReturnValueOnce(response.promise)
    const pending = useAppStore.getState().renameProject(PROJECT_ID, 'Delayed rename')
    await Promise.resolve()

    useAppStore.setState((state) => ({
      projects: state.projects.map((project) => project.id === PROJECT_ID ? {
        ...project,
        name: 'Newer authoritative name',
        starred: true,
        projectVersion: 5,
      } : project),
    }))
    response.resolve({
      projectId: PROJECT_ID,
      name: 'Delayed rename',
      starred: false,
      version: 4,
      updatedAt: '2026-08-10T03:01:00Z',
    })
    await pending

    expect(useAppStore.getState().projects[0]).toMatchObject({
      name: 'Newer authoritative name',
      starred: true,
      projectVersion: 5,
    })
    expect(dependencies.fenceCloudProjectReconciliation).not.toHaveBeenCalled()
  })

  it('ignores an entire delayed star response N+1 after the store reaches N+2', async () => {
    const response = deferred<{
      projectId: string
      name: string
      starred: boolean
      version: number
      updatedAt: string
    }>()
    dependencies.patchCloudProject.mockReturnValueOnce(response.promise)
    const pending = useAppStore.getState().toggleStar(PROJECT_ID)
    await Promise.resolve()

    useAppStore.setState((state) => ({
      projects: state.projects.map((project) => project.id === PROJECT_ID ? {
        ...project,
        name: 'Newer authoritative name',
        starred: false,
        projectVersion: 5,
      } : project),
    }))
    response.resolve({
      projectId: PROJECT_ID,
      name: 'Original',
      starred: true,
      version: 4,
      updatedAt: '2026-08-10T03:01:00Z',
    })
    await pending

    expect(useAppStore.getState().projects[0]).toMatchObject({
      name: 'Newer authoritative name',
      starred: false,
      projectVersion: 5,
    })
    expect(dependencies.fenceCloudProjectReconciliation).not.toHaveBeenCalled()
  })

  it('never regresses projectVersion through a stale generic status patch', () => {
    useAppStore.getState().updateProject(PROJECT_ID, {
      projectVersion: 2,
      status: 'processing',
    })
    expect(useAppStore.getState().projects[0]).toMatchObject({
      projectVersion: 3,
      status: 'processing',
    })
  })

  it('keeps cloud delete explicitly deferred instead of calling a legacy route', async () => {
    await expect(useAppStore.getState().deleteProject(PROJECT_ID))
      .rejects.toMatchObject({ category: 'validation' })
    expect(useAppStore.getState().projects).toHaveLength(1)
  })
})
