// G7 B2/B7: reconciliation is idempotent, keyed by jobId with identity via
// entry.projectId, never interchanges the two IDs, uses server created_at,
// and distinct IDs survive Zustand persistence (with no secrets persisted).

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./cloudMode', () => ({
  isCloudMode: () => true,
  isCloudSession: () => true,
  cloudApiBase: () => 'http://localhost:8000',
}))

import { useAppStore } from '@/store/appStore'
import { clearPortfolioToken, setPortfolioToken } from './portfolioToken'
import { fenceCloudProjectReconciliation, reconcileCloudProjects } from './cloudProjects'

const JOB_1 = '11111111-1111-4111-8111-111111111111'
const PROJECT_1 = '22222222-2222-4222-8222-222222222222'
const JOB_ABANDONED = '33333333-3333-4333-8333-333333333333'
const PROJECT_ABANDONED = '44444444-4444-4444-8444-444444444444'
const JOB_ACCEPTED = '55555555-5555-4555-8555-555555555555'
const PROJECT_ACCEPTED = '66666666-6666-4666-8666-666666666666'
const JOB_NEW = '77777777-7777-4777-8777-777777777777'
const PROJECT_NEW = '88888888-8888-4888-8888-888888888888'

const ENTRY = {
  id: JOB_1,
  projectId: PROJECT_1,
  projectVersion: 1,
  project_name: 'Cloud clip',
  starred: false,
  status: 'ready',
  canonicalState: 'READY_FOR_REVIEW',
  sourceUploaded: true,
  progress: 100,
  stage: 'complete',
  duration_secs: 120,
  model: 'gpt-4.1',
  chunk_size: 60,
  pipeline_revision: 'dev',
  created_at: '2026-08-07T10:00:00+00:00',
  updated_at: null,
  error: null,
  error_code: null,
}

function stubJobsList(body: unknown = { [JOB_1]: ENTRY }, status = 200) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    })),
  )
}

beforeEach(() => {
  setPortfolioToken('reconcile-token')
  useAppStore.setState({
    projects: [],
    isAuthenticated: true,
    currentUser: { email: 'demo@example.test', name: 'Demo User' },
  })
})

afterEach(() => {
  clearPortfolioToken()
  useAppStore.setState({ projects: [], isAuthenticated: false, currentUser: null })
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  localStorage.clear()
  sessionStorage.clear()
})

describe('reconcileCloudProjects', () => {
  it('adds unknown projects keyed by projectId while retaining the map-key jobId', async () => {
    stubJobsList()
    expect(await reconcileCloudProjects()).toBe(true)
    const projects = useAppStore.getState().projects
    expect(projects).toHaveLength(1)
    expect(projects[0].id).toBe(PROJECT_1) // durable product identity
    expect(projects[0].jobId).toBe(JOB_1) // the MAP KEY, not entry.id
    expect(projects[0].projectVersion).toBe(ENTRY.projectVersion)
    expect(projects[0].createdAt).toBe(ENTRY.created_at) // server created_at
  })

  it('is idempotent across repeated runs (StrictMode-safe)', async () => {
    stubJobsList()
    await reconcileCloudProjects()
    await reconcileCloudProjects()
    await reconcileCloudProjects()
    expect(useAppStore.getState().projects).toHaveLength(1)
  })

  it('updates existing projects BY projectId and never interchanges IDs', async () => {
    useAppStore.getState().addProject({
      id: PROJECT_1,
      name: 'stale name',
      status: 'processing',
      createdAt: 'old',
    })
    stubJobsList()
    await reconcileCloudProjects()
    const projects = useAppStore.getState().projects
    expect(projects).toHaveLength(1)
    expect(projects[0].id).toBe(PROJECT_1)
    expect(projects[0].jobId).toBe(JOB_1)
    expect(projects[0].status).toBe('ready')
    expect(projects[0].name).toBe('Cloud clip')
    // No project was ever keyed by the job id.
    expect(projects.find((p) => p.id === JOB_1)).toBeUndefined()
  })

  it('a successful reconciliation prunes fixtures, stale cards, and AWAITING_UPLOAD reservations', async () => {
    useAppStore.setState({
      projects: [
        { id: 'fixture', name: 'Fixture', status: 'ready', createdAt: 'old' },
        { id: 'stale', name: 'Stale', status: 'processing', createdAt: 'old' },
      ],
    })
    stubJobsList({
      [JOB_ABANDONED]: {
        ...ENTRY,
        id: JOB_ABANDONED,
        projectId: PROJECT_ABANDONED,
        canonicalState: 'AWAITING_UPLOAD',
        status: 'queued',
        sourceUploaded: false,
      },
      [JOB_ACCEPTED]: { ...ENTRY, id: JOB_ACCEPTED, projectId: PROJECT_ACCEPTED },
    })
    await reconcileCloudProjects()
    expect(useAppStore.getState().projects.map((project) => project.id)).toEqual([PROJECT_ACCEPTED])
  })

  it('keeps a server-verified AWAITING_UPLOAD source visible and truthfully retryable', async () => {
    stubJobsList({
      [JOB_ABANDONED]: {
        ...ENTRY,
        id: JOB_ABANDONED,
        projectId: PROJECT_ABANDONED,
        canonicalState: 'AWAITING_UPLOAD',
        status: 'queued',
        sourceUploaded: true,
        progress: 0,
        stage: null,
      },
    })
    await reconcileCloudProjects()
    expect(useAppStore.getState().projects).toEqual([
      expect.objectContaining({
        id: PROJECT_ABANDONED,
        jobId: JOB_ABANDONED,
        status: 'confirmation_pending',
        completionPending: true,
      }),
    ])
  })

  it('preserves same-tab S3-success evidence across a snapshot before server verification', async () => {
    useAppStore.setState({
      projects: [{
        id: PROJECT_ABANDONED,
        jobId: JOB_ABANDONED,
        name: 'Uploaded locally',
        status: 'confirmation_pending',
        completionPending: true,
        createdAt: '2026-08-07T09:00:00Z',
      }],
    })
    stubJobsList({
      [JOB_ABANDONED]: {
        ...ENTRY,
        id: JOB_ABANDONED,
        projectId: PROJECT_ABANDONED,
        canonicalState: 'AWAITING_UPLOAD',
        status: 'queued',
        sourceUploaded: false,
        progress: 0,
        stage: null,
      },
    })
    await reconcileCloudProjects()
    expect(useAppStore.getState().projects[0]).toMatchObject({
      id: PROJECT_ABANDONED,
      jobId: JOB_ABANDONED,
      status: 'confirmation_pending',
      completionPending: true,
    })
  })

  it('after logout, a later session reconstructs only server-durable upload evidence', async () => {
    useAppStore.setState({
      projects: [{
        id: PROJECT_ABANDONED,
        jobId: JOB_ABANDONED,
        name: 'Same-tab only',
        status: 'confirmation_pending',
        completionPending: true,
        createdAt: '2026-08-07T09:00:00Z',
      }],
    })
    useAppStore.getState().logout()
    expect(useAppStore.getState().projects).toEqual([])

    setPortfolioToken('next-session-token')
    useAppStore.setState({ isAuthenticated: true })
    stubJobsList({
      [JOB_ABANDONED]: {
        ...ENTRY,
        id: JOB_ABANDONED,
        projectId: PROJECT_ABANDONED,
        canonicalState: 'AWAITING_UPLOAD',
        status: 'queued',
        sourceUploaded: false,
        progress: 0,
        stage: null,
      },
    })
    await reconcileCloudProjects()
    expect(useAppStore.getState().projects).toEqual([])

    stubJobsList({
      [JOB_ABANDONED]: {
        ...ENTRY,
        id: JOB_ABANDONED,
        projectId: PROJECT_ABANDONED,
        canonicalState: 'AWAITING_UPLOAD',
        status: 'queued',
        sourceUploaded: true,
        progress: 0,
        stage: null,
      },
    })
    await reconcileCloudProjects()
    expect(useAppStore.getState().projects[0]?.status).toBe('confirmation_pending')
  })

  it('a failed or malformed list preserves the last valid cloud view', async () => {
    const previous = { id: 'valid', name: 'Last valid', status: 'ready', createdAt: 'now' } as const
    useAppStore.setState({ projects: [previous] })
    stubJobsList({ detail: 'outage' }, 503)
    expect(await reconcileCloudProjects()).toBe(false)
    expect(useAppStore.getState().projects).toEqual([previous])

    stubJobsList([])
    expect(await reconcileCloudProjects()).toBe(false)
    expect(useAppStore.getState().projects).toEqual([previous])
  })

  it('never issues a request without token access', async () => {
    clearPortfolioToken()
    const spy = vi.fn()
    vi.stubGlobal('fetch', spy)
    await reconcileCloudProjects()
    expect(spy).not.toHaveBeenCalled()
  })

  it('ignores an old response that resolves after logout', async () => {
    let resolveList: (response: Response) => void = () => {}
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((resolve) => { resolveList = resolve })))

    const oldReconciliation = reconcileCloudProjects()
    await Promise.resolve()
    useAppStore.getState().logout()
    expect(useAppStore.getState().projects).toEqual([])
    resolveList(new Response(JSON.stringify({ [JOB_1]: ENTRY }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    await oldReconciliation
    expect(useAppStore.getState().projects).toEqual([])
    expect(useAppStore.getState().isAuthenticated).toBe(false)
    expect(sessionStorage.getItem('instascribe-app') ?? '').not.toContain(PROJECT_1)
  })

  it('starts an independent new-token request while the old session is unresolved', async () => {
    const resolvers: Array<(response: Response) => void> = []
    const fetchSpy = vi.fn((_url: string | URL | Request, init?: RequestInit) =>
      new Promise<Response>((resolve) => { resolvers.push(resolve) }).then((response) => {
        void init
        return response
      }),
    )
    vi.stubGlobal('fetch', fetchSpy)

    clearPortfolioToken()
    setPortfolioToken('old-token')
    const oldRequest = reconcileCloudProjects()
    await Promise.resolve()
    setPortfolioToken('new-token')
    const newRequest = reconcileCloudProjects()
    await Promise.resolve()

    expect(fetchSpy).toHaveBeenCalledTimes(2)
    const firstHeaders = new Headers((fetchSpy.mock.calls[0][1] as RequestInit).headers)
    const secondHeaders = new Headers((fetchSpy.mock.calls[1][1] as RequestInit).headers)
    expect(firstHeaders.get('X-Portfolio-Token')).toBe('old-token')
    expect(secondHeaders.get('X-Portfolio-Token')).toBe('new-token')

    const NEW_ENTRY = {
      ...ENTRY,
      id: JOB_NEW,
      projectId: PROJECT_NEW,
      project_name: 'New session clip',
    }
    resolvers[1](new Response(JSON.stringify({ [JOB_NEW]: NEW_ENTRY }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    await newRequest
    expect(useAppStore.getState().projects.map((project) => project.id)).toEqual([PROJECT_NEW])

    resolvers[0](new Response(JSON.stringify({ [JOB_1]: ENTRY }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    await oldRequest
    expect(useAppStore.getState().projects.map((project) => project.id)).toEqual([PROJECT_NEW])
  })

  it('coalesces same-session StrictMode mounts and applies once', async () => {
    let resolveList: (response: Response) => void = () => {}
    const fetchSpy = vi.fn(() => new Promise<Response>((resolve) => { resolveList = resolve }))
    vi.stubGlobal('fetch', fetchSpy)
    const first = reconcileCloudProjects()
    const second = reconcileCloudProjects()
    const third = reconcileCloudProjects()
    await Promise.resolve()
    expect(fetchSpy).toHaveBeenCalledTimes(1)
    resolveList(new Response(JSON.stringify({ [JOB_1]: ENTRY }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    await Promise.all([first, second, third])
    expect(useAppStore.getState().projects.map((project) => project.id)).toEqual([PROJECT_1])
  })

  it('cannot apply a jobs-list snapshot that predates a successful project mutation', async () => {
    let resolveList: (response: Response) => void = () => {}
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((resolve) => { resolveList = resolve })))
    const oldRequest = reconcileCloudProjects()
    await Promise.resolve()

    fenceCloudProjectReconciliation()
    useAppStore.setState({
      projects: [{
        id: PROJECT_1,
        projectVersion: 2,
        jobId: JOB_1,
        name: 'Accepted rename',
        starred: false,
        status: 'ready',
        createdAt: ENTRY.created_at,
      }],
    })
    resolveList(new Response(JSON.stringify({ [JOB_1]: ENTRY }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))

    await expect(oldRequest).resolves.toBe(false)
    expect(useAppStore.getState().projects[0]).toMatchObject({
      name: 'Accepted rename',
      projectVersion: 2,
    })
  })

  it('forceFresh starts a distinct post-conflict fetch without waiting for the older request', async () => {
    const resolvers: Array<(response: Response) => void> = []
    const fetchSpy = vi.fn(() => new Promise<Response>((resolve) => { resolvers.push(resolve) }))
    vi.stubGlobal('fetch', fetchSpy)
    const oldRequest = reconcileCloudProjects()
    await Promise.resolve()
    const recovery = reconcileCloudProjects({ forceFresh: true })
    await Promise.resolve()
    expect(fetchSpy).toHaveBeenCalledTimes(2)

    const updated = { ...ENTRY, projectVersion: 2, project_name: 'Concurrent rename' }
    resolvers[1](new Response(JSON.stringify({ [JOB_1]: updated }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    await expect(recovery).resolves.toBe(true)

    // The older snapshot resolves last and remains unable to overwrite N+1.
    resolvers[0](new Response(JSON.stringify({ [JOB_1]: ENTRY }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }))
    await expect(oldRequest).resolves.toBe(false)
    expect(useAppStore.getState().projects[0]).toMatchObject({
      name: 'Concurrent rename',
      projectVersion: 2,
    })
  })

  it('persisted state carries both distinct IDs and no secrets/URLs', async () => {
    stubJobsList()
    await reconcileCloudProjects()
    await new Promise((resolve) => setTimeout(resolve, 0)) // flush persist write
    const persisted = sessionStorage.getItem('instascribe-app') ?? ''
    expect(persisted).toContain(PROJECT_1)
    expect(persisted).toContain(JOB_1)
    expect(persisted).not.toContain('reconcile-token')
    expect(persisted).not.toContain('X-Amz')
    expect(persisted).not.toContain('4566')
    expect(localStorage.getItem('instascribe-app')).toBeNull()
  })
})
