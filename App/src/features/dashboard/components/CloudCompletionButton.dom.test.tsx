// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  listCloudJobs: vi.fn(),
  completeCloudUpload: vi.fn(),
  createCloudJob: vi.fn(),
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
    listCloudJobs: api.listCloudJobs,
    completeCloudUpload: api.completeCloudUpload,
    createCloudJob: api.createCloudJob,
  }
})

import ProjectsPage from '../pages/ProjectsPage'
import { reconcileCloudProjects } from '@/lib/cloudProjects'
import { clearPortfolioToken, setPortfolioToken } from '@/lib/portfolioToken'
import { useAppStore } from '@/store/appStore'

const JOB_ID = '11111111-1111-4111-8111-111111111111'
const PROJECT_ID = '22222222-2222-4222-8222-222222222222'

const UPLOADED_PENDING = {
  id: JOB_ID,
  projectId: PROJECT_ID,
  projectVersion: 1,
  project_name: 'Recovered upload',
  starred: false,
  status: 'queued' as const,
  canonicalState: 'AWAITING_UPLOAD' as const,
  sourceUploaded: true,
  progress: 0,
  stage: null,
  duration_secs: 42,
  model: 'gpt-4.1',
  chunk_size: 60,
  pipeline_revision: 'dev',
  created_at: '2026-08-07T10:00:00+00:00',
  updated_at: null,
  error: null,
  error_code: null,
}

beforeEach(() => {
  sessionStorage.clear()
  localStorage.clear()
  clearPortfolioToken()
  setPortfolioToken('mounted-recovery-token')
  useAppStore.setState({ projects: [], isAuthenticated: true })
  api.listCloudJobs.mockReset().mockResolvedValue({ [JOB_ID]: UPLOADED_PENDING })
  api.completeCloudUpload.mockReset().mockResolvedValue(undefined)
  api.createCloudJob.mockReset()
})

afterEach(() => {
  cleanup()
  clearPortfolioToken()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('mounted uploaded-source recovery', () => {
  it('reconstructs the pending action after route remount and retries only this job completion', async () => {
    await reconcileCloudProjects()
    expect(useAppStore.getState().projects[0]).toMatchObject({
      id: PROJECT_ID,
      jobId: JOB_ID,
      status: 'confirmation_pending',
    })

    const firstRoute = render(<MemoryRouter><ProjectsPage /></MemoryRouter>)
    expect(screen.getByText(/your video is uploaded/i)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Confirm upload' })).toBeTruthy()
    firstRoute.unmount()

    render(<MemoryRouter><ProjectsPage /></MemoryRouter>)
    const networkSpy = vi.fn()
    vi.stubGlobal('fetch', networkSpy)
    fireEvent.click(screen.getByRole('button', { name: 'Confirm upload' }))
    await waitFor(() => expect(api.completeCloudUpload).toHaveBeenCalledWith(JOB_ID))
    await waitFor(() => expect(useAppStore.getState().projects[0]).toMatchObject({
      status: 'processing',
      completionPending: false,
    }))
    expect(api.createCloudJob).not.toHaveBeenCalled()
    expect(networkSpy).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: 'Confirm upload' })).toBeNull()
  })

  it('keeps rehydrated client upload evidence visible before the server marker exists', async () => {
    useAppStore.setState({
      projects: [{
        id: PROJECT_ID,
        jobId: JOB_ID,
        name: 'Client-observed upload',
        status: 'confirmation_pending',
        completionPending: true,
        createdAt: '2026-08-07T10:00:00Z',
      }],
      isAuthenticated: true,
    })
    const persistedAfterS3 = sessionStorage.getItem('instascribe-app')
    expect(persistedAfterS3).toContain('completionPending')
    useAppStore.setState({ projects: [] })
    sessionStorage.setItem('instascribe-app', persistedAfterS3!)
    await useAppStore.persist.rehydrate()
    api.listCloudJobs.mockResolvedValueOnce({
      [JOB_ID]: { ...UPLOADED_PENDING, sourceUploaded: false },
    })

    await reconcileCloudProjects()
    expect(useAppStore.getState().projects[0]).toMatchObject({
      id: PROJECT_ID,
      jobId: JOB_ID,
      status: 'confirmation_pending',
      completionPending: true,
    })
    render(<MemoryRouter><ProjectsPage /></MemoryRouter>)
    expect(screen.getByText(/your video is uploaded/i)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Confirm upload' })).toBeTruthy()
  })

  it('ignores an older AWAITING snapshot after same-job completion is accepted', async () => {
    useAppStore.setState({
      projects: [{
        id: PROJECT_ID,
        jobId: JOB_ID,
        name: 'Completion race',
        status: 'confirmation_pending',
        completionPending: true,
        createdAt: '2026-08-07T10:00:00Z',
      }],
      isAuthenticated: true,
    })
    const staleList = deferred<Record<string, typeof UPLOADED_PENDING>>()
    api.listCloudJobs.mockReturnValueOnce(staleList.promise)
    const reconciliation = reconcileCloudProjects()
    await waitFor(() => expect(api.listCloudJobs).toHaveBeenCalledTimes(1))

    render(<MemoryRouter><ProjectsPage /></MemoryRouter>)
    fireEvent.click(screen.getByRole('button', { name: 'Confirm upload' }))
    await waitFor(() => expect(api.completeCloudUpload).toHaveBeenCalledWith(JOB_ID))
    await waitFor(() => expect(useAppStore.getState().projects[0]).toMatchObject({
      status: 'processing',
      completionPending: false,
    }))

    staleList.resolve({
      [JOB_ID]: { ...UPLOADED_PENDING, sourceUploaded: false },
    })
    await reconciliation
    expect(useAppStore.getState().projects[0]).toMatchObject({
      id: PROJECT_ID,
      jobId: JOB_ID,
      status: 'processing',
    })
    expect(screen.queryByText('Completion race')).toBeTruthy()
  })
})

function deferred<T>() {
  let resolve: (value: T) => void = () => {}
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}
