// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Project, Scene } from '@/types'

// These route destinations are deliberately small: this suite owns the
// login/guard/session boundary, not dashboard widgets. LoginPage, AuthGuard,
// GuestGuard, the production browser router, real EditorPage/query lifecycle,
// Zustand store, and portfolio-token module all remain real.
vi.mock('@/components/layout/DashboardLayout', async () => {
  const { Outlet } = await import('react-router-dom')
  return {
    default: function TestDashboardLayout() {
      return <main data-testid="dashboard-shell"><Outlet /></main>
    },
  }
})

vi.mock('@/features/dashboard/pages/HomePage', () => ({
  default: () => <h1>Dashboard home</h1>,
}))

vi.mock('@/features/dashboard/pages/ProjectsPage', async () => {
  const { useAppStore } = await import('@/store/appStore')
  return {
    default: function TestProjectsPage() {
      const projects = useAppStore((state) => state.projects)
      return <>{projects.map((project) => <p key={project.id}>{project.name}</p>)}</>
    },
  }
})

vi.mock('@/features/editor/components/SceneListPanel', () => ({
  SceneListPanel: ({ scenes }: { scenes: Scene[] }) => (
    <div data-testid="real-editor-scenes">{scenes.map((scene) => scene.text).join(',')}</div>
  ),
}))
vi.mock('@/features/editor/components/VideoPanel', () => ({ VideoPanel: () => <div /> }))
vi.mock('@/features/editor/components/ScriptPanel', () => ({ ScriptPanel: () => <div /> }))
vi.mock('@/features/editor/components/CharactersPanel', () => ({ CharactersPanel: () => <div /> }))
vi.mock('@/features/editor/components/QualityPanel', () => ({ QualityPanel: () => <div /> }))
vi.mock('@/features/study/EditorTour', () => ({ EditorTour: () => null }))
vi.mock('@/features/study/HelpPanel', () => ({ HelpPanel: () => null }))

vi.mock('@/features/auth/pages/RegisterPage', () => ({ default: () => <p>Register</p> }))
vi.mock('@/features/auth/pages/ForgotPasswordPage', () => ({ default: () => <p>Reset password</p> }))
vi.mock('@/features/study/StudyIntro', () => ({ default: () => <p>Study</p> }))

type AppStoreModule = typeof import('@/store/appStore')
type PortfolioTokenModule = typeof import('@/lib/portfolioToken')
type RouterModule = typeof import('./index')
type QueryClientModule = typeof import('@/lib/queryClient')
type QueryKeysModule = typeof import('@/lib/queryKeys')

let useAppStore: AppStoreModule['useAppStore']
let clearPortfolioToken: PortfolioTokenModule['clearPortfolioToken']
let getPortfolioToken: PortfolioTokenModule['getPortfolioToken']
let router: RouterModule['router']
let queryClient: QueryClientModule['queryClient']
let queryKeys: QueryKeysModule['queryKeys']

const VALID_TOKEN = 'mounted-route-valid-token'
const PRIVATE_SCENE_TEXT = 'Private cached scene description'
const CACHED_PROJECT: Project = {
  id: '22222222-2222-4222-8222-222222222222',
  jobId: '11111111-1111-4111-8111-111111111111',
  name: 'Private cached project',
  status: 'ready',
  createdAt: '2026-08-07T12:00:00.000Z',
}

function stubJobsResponse(status: number, body: unknown = {}) {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
  }))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function stubTutorialResponses() {
  const fixtureBodies: Record<string, unknown> = {
    '/data/sintel-blender-cc/scenes.json': [{
      scene_id: 'scene_1',
      start: 0,
      end: 4,
      frame_indices: [0],
      character_ids: [],
      caption_template: 'A short tutorial scene.',
      caption: 'Public tutorial fixture loaded',
      render_mode: 'overlay',
      locked: false,
      needs_review: false,
    }],
    '/data/sintel-blender-cc/audio_events.json': [],
    '/data/sintel-blender-cc/ad_placement_gaps.json': [],
    '/data/sintel-blender-cc/entities.json': [],
  }
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = input instanceof Request ? input.url : String(input)
    const pathname = new URL(url, 'http://localhost').pathname
    const body = fixtureBodies[pathname]
    return body === undefined
      ? new Response('not found', { status: 404 })
      : new Response(JSON.stringify(body), {
          status: 200,
          headers: { 'Content-Type': 'application/json; charset=utf-8' },
        })
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function fillLogin(token: string) {
  fireEvent.change(screen.getByLabelText('Email'), {
    target: { value: 'demo@example.test' },
  })
  fireEvent.change(screen.getByLabelText('Password'), {
    target: { value: 'demo-only-not-a-real-password' },
  })
  fireEvent.change(screen.getByLabelText('Portfolio access token'), {
    target: { value: token },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
}

async function mountAt(path: string) {
  await act(async () => {
    await router.navigate(path)
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

function seedPrivateEditorQueries() {
  const ref = {
    url: 'https://storage.example.invalid/private-signed-reference',
    contentType: 'application/json',
    sizeBytes: 2,
    checksumSha256: 'a'.repeat(64),
  }
  queryClient.setQueryData(queryKeys.manifest(CACHED_PROJECT.id, CACHED_PROJECT.jobId!), {
    projectId: CACHED_PROJECT.id,
    jobId: CACHED_PROJECT.jobId,
    pipelineRevision: 'test',
    expiresAt: new Date(Date.now() + 300_000).toISOString(),
    artifacts: {
      video: { ...ref, contentType: 'video/mp4' },
      scenes: ref,
      entities: ref,
      audioEvents: ref,
      placementGaps: ref,
      transcript: ref,
      posterJpg: null,
      posterAvif: null,
    },
  })
  queryClient.setQueryData(queryKeys.cloudScenes(CACHED_PROJECT.id, CACHED_PROJECT.jobId!), [{
    id: 1,
    sceneNumber: 1,
    sceneKey: 'scene_private',
    startSecs: 0,
    endSecs: 4,
    durationSecs: 4,
    text: PRIVATE_SCENE_TEXT,
    template: 'private template',
    characterIds: [],
    locked: false,
    needsReview: false,
    active: true,
  } satisfies Scene])
  queryClient.setQueryData(queryKeys.cloudAudioEvents(CACHED_PROJECT.id, CACHED_PROJECT.jobId!), [])
  queryClient.setQueryData(queryKeys.cloudAdGaps(CACHED_PROJECT.id, CACHED_PROJECT.jobId!), [])
  queryClient.setQueryData(queryKeys.cloudEntities(CACHED_PROJECT.id, CACHED_PROJECT.jobId!), [])
  queryClient.setQueryData(queryKeys.cloudOverrides(CACHED_PROJECT.id, CACHED_PROJECT.jobId!), {})
}

beforeAll(async () => {
  // Import production modules only after establishing cloud mode so Zustand
  // selects sessionStorage during its initial persistence hydration.
  vi.stubEnv('VITE_CLOUD_MODE', '1')
  const storeModule = await import('@/store/appStore')
  const tokenModule = await import('@/lib/portfolioToken')
  const routerModule = await import('./index')
  const queryClientModule = await import('@/lib/queryClient')
  const queryKeysModule = await import('@/lib/queryKeys')
  useAppStore = storeModule.useAppStore
  clearPortfolioToken = tokenModule.clearPortfolioToken
  getPortfolioToken = tokenModule.getPortfolioToken
  router = routerModule.router
  queryClient = queryClientModule.queryClient
  queryKeys = queryKeysModule.queryKeys
})

beforeEach(async () => {
  localStorage.clear()
  sessionStorage.clear()
  clearPortfolioToken()
  queryClient.clear()
  useAppStore.setState({
    currentUser: null,
    isAuthenticated: false,
    sidebarCollapsed: false,
    isDemoMode: true,
    projects: [],
  })
  await act(async () => {
    await router.navigate('/login')
  })
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

afterAll(() => {
  vi.unstubAllEnvs()
})

describe('mounted cloud router and session boundaries', () => {
  it('opens the baked tutorial publicly in cloud mode without a token or API request', async () => {
    const fetchMock = stubTutorialResponses()
    await mountAt('/tutorials')

    fireEvent.click(screen.getByRole('button', { name: /Edit a short film/ }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/tutorials/ent-short/editor'))
    await waitFor(() => expect(screen.getByTestId('real-editor-scenes').textContent).toContain(
      'Public tutorial fixture loaded',
    ))
    expect(screen.queryByRole('heading', { name: 'Welcome back' })).toBeNull()
    expect(document.querySelector('a[href="/tutorials"]')).not.toBeNull()
    expect(useAppStore.getState().isAuthenticated).toBe(false)
    expect(getPortfolioToken()).toBeNull()
    expect(fetchMock).toHaveBeenCalledTimes(4)
    expect(fetchMock.mock.calls.every(([input]) => {
      const url = input instanceof Request ? input.url : String(input)
      return new URL(url, 'http://localhost').pathname.startsWith('/data/sintel-blender-cc/')
    })).toBe(true)
  })

  it('reconstructs the public tutorial route directly without a prior picker visit', async () => {
    stubTutorialResponses()
    useAppStore.setState({
      projects: [{
        ...CACHED_PROJECT,
        id: 'ent-short',
        name: 'Stale browser metadata',
        dataPath: 'https://untrusted.example.invalid/data',
      }],
    })

    await mountAt('/tutorials/ent-short/editor')

    await waitFor(() => expect(screen.getByTestId('real-editor-scenes').textContent).toContain(
      'Public tutorial fixture loaded',
    ))
    expect(router.state.location.pathname).toBe('/tutorials/ent-short/editor')
    expect(useAppStore.getState().projects.find((project) => project.id === 'ent-short')).toMatchObject({
      name: 'Edit a short film',
      dataPath: '/data/sintel-blender-cc',
      videoFile: '/videos/sintel-blender-cc.mp4',
    })
    expect(useAppStore.getState().projects.find((project) => project.id === 'ent-short')?.jobId).toBeUndefined()
  })

  it.each(['/tutorials/edu-short/editor', '/tutorials/not-registered/editor'])(
    'redirects unavailable public tutorial route %s to the picker',
    async (path) => {
      await mountAt(path)

      await waitFor(() => expect(router.state.location.pathname).toBe('/tutorials'))
      expect(screen.getByRole('heading', { name: 'Try a tutorial' })).toBeTruthy()
    },
  )

  it('transitions from the real cloud login page to the protected dashboard for a valid token', async () => {
    const fetchMock = stubJobsResponse(200, {})
    await mountAt('/login')

    fillLogin(VALID_TOKEN)

    expect(await screen.findByRole('heading', { name: 'Dashboard home' })).toBeTruthy()
    expect(router.state.location.pathname).toBe('/dashboard')
    expect(useAppStore.getState().isAuthenticated).toBe(true)
    expect(getPortfolioToken()).toBe(VALID_TOKEN)
    expect(sessionStorage.getItem('instascribe:portfolioToken')).toBe(VALID_TOKEN)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('keeps a wrong-token attempt on login without creating an authenticated session', async () => {
    stubJobsResponse(401, { detail: 'not admitted' })
    await mountAt('/login')

    fillLogin('wrong-token')

    expect((await screen.findByRole('alert')).textContent).toContain('Access was not granted')
    expect(router.state.location.pathname).toBe('/login')
    expect(useAppStore.getState().isAuthenticated).toBe(false)
    expect(getPortfolioToken()).toBeNull()
    expect(sessionStorage.getItem('instascribe:portfolioToken')).toBeNull()
  })

  it('redirects restored authentication with a missing portfolio token back to login', async () => {
    useAppStore.setState({
      currentUser: { email: 'demo@example.test', name: 'Demo User' },
      isAuthenticated: true,
      projects: [{ ...CACHED_PROJECT, dataPath: '/data/sintel-blender-cc' }],
    })
    clearPortfolioToken()

    await mountAt(`/editor/${CACHED_PROJECT.id}`)

    await waitFor(() => expect(router.state.location.pathname).toBe('/login'))
    expect(screen.getByRole('heading', { name: 'Welcome back' })).toBeTruthy()
    expect(screen.queryByText(`Editor project: ${CACHED_PROJECT.name}`)).toBeNull()
  })

  it('restores the accepted token and authenticated store from same-tab session storage after remount', async () => {
    stubJobsResponse(200, {})
    const firstMount = await mountAt('/login')
    fillLogin(VALID_TOKEN)
    expect(await screen.findByRole('heading', { name: 'Dashboard home' })).toBeTruthy()

    const persistedToken = sessionStorage.getItem('instascribe:portfolioToken')
    const persistedApp = sessionStorage.getItem('instascribe-app')
    expect(persistedToken).toBe(VALID_TOKEN)
    expect(persistedApp).not.toBeNull()
    firstMount.unmount()

    // Model a same-tab refresh: module memory starts empty, then the existing
    // tab's sessionStorage snapshot hydrates before the protected route mounts.
    clearPortfolioToken()
    useAppStore.setState({ currentUser: null, isAuthenticated: false, projects: [] })
    sessionStorage.setItem('instascribe:portfolioToken', persistedToken!)
    sessionStorage.setItem('instascribe-app', persistedApp!)
    await useAppStore.persist.rehydrate()

    await mountAt('/dashboard')

    expect(screen.getByRole('heading', { name: 'Dashboard home' })).toBeTruthy()
    expect(router.state.location.pathname).toBe('/dashboard')
    expect(useAppStore.getState().isAuthenticated).toBe(true)
    expect(getPortfolioToken()).toBe(VALID_TOKEN)
  })

  it('logout followed by wrong-token re-entry cannot render cached project or editor data', async () => {
    stubJobsResponse(200, {})
    await mountAt('/login')
    fillLogin(VALID_TOKEN)
    expect(await screen.findByRole('heading', { name: 'Dashboard home' })).toBeTruthy()

    act(() => {
      useAppStore.setState({ projects: [CACHED_PROJECT] })
      seedPrivateEditorQueries()
    })
    await act(async () => {
      await router.navigate(`/editor/${CACHED_PROJECT.id}`)
    })
    expect(await screen.findByText(PRIVATE_SCENE_TEXT)).toBeTruthy()

    act(() => {
      useAppStore.getState().logout()
    })
    await waitFor(() => expect(router.state.location.pathname).toBe('/login'))
    expect(screen.queryByText(PRIVATE_SCENE_TEXT)).toBeNull()
    expect(queryClient.getQueryData(
      queryKeys.manifest(CACHED_PROJECT.id, CACHED_PROJECT.jobId!),
    )).toBeUndefined()
    expect(useAppStore.getState().projects).toEqual([])
    expect(sessionStorage.getItem('instascribe:portfolioToken')).toBeNull()

    stubJobsResponse(401, { detail: 'not admitted' })
    fillLogin('still-wrong')
    expect((await screen.findByRole('alert')).textContent).toContain('Access was not granted')
    expect(router.state.location.pathname).toBe('/login')
    expect(screen.queryByText(CACHED_PROJECT.name)).toBeNull()
    expect(screen.queryByText(PRIVATE_SCENE_TEXT)).toBeNull()
    expect(useAppStore.getState().projects).toEqual([])
    expect(useAppStore.getState().isAuthenticated).toBe(false)
  })
})
