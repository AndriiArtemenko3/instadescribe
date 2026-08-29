// G7.1 B/C: real session boundaries and cache identity.
//
// - Cloud logout clears the TanStack query cache, the session-scoped scene
//   drafts, the portfolio token and the project metadata.
// - Scene drafts in cloud mode live in sessionStorage; a successful Apply
//   clears the redundant draft; logout clears them all.
// - Every cloud data key carries projectId AND jobId, so a project
//   reconciled to a new processing job can never see stale cached data.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./cloudMode', () => ({
  isCloudMode: () => true,
  isCloudSession: () => true,
  cloudApiBase: () => 'http://localhost:8000',
}))

import { hasPortfolioToken, setPortfolioToken } from './portfolioToken'
import {
  clearAllCloudDrafts,
  clearSceneDraft,
  clearSceneDraftFields,
  loadEdits,
  persistSceneActive,
  persistSceneText,
} from './persistence'
import { queryClient } from './queryClient'
import { queryKeys } from './queryKeys'
import { initialProjects, useAppStore } from '@/store/appStore'

beforeEach(() => {
  sessionStorage.clear()
  localStorage.clear()
  queryClient.clear()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('cloud scene drafts (session-scoped)', () => {
  it('writes drafts to sessionStorage only, never localStorage', () => {
    persistSceneText('proj-1', 3, 'edited text')
    persistSceneActive('proj-1', 3, false)
    expect(sessionStorage.getItem('instascribe:proj-1:edits')).toBeTruthy()
    expect(localStorage.getItem('instascribe:proj-1:edits')).toBeNull()
    expect(loadEdits('proj-1').scenes[3]).toEqual({ text: 'edited text', active: false })
  })

  it('clearSceneDraft removes ONLY the applied scene', () => {
    persistSceneText('proj-1', 3, 'applied')
    persistSceneText('proj-1', 4, 'still pending')
    clearSceneDraft('proj-1', 3)
    const edits = loadEdits('proj-1')
    expect(edits.scenes[3]).toBeUndefined()
    expect(edits.scenes[4]).toEqual({ text: 'still pending' })
  })

  it('clearAllCloudDrafts removes every draft key and nothing else', () => {
    persistSceneText('proj-1', 1, 'a')
    persistSceneText('proj-2', 2, 'b')
    sessionStorage.setItem('unrelated:key', 'keep me')
    clearAllCloudDrafts()
    expect(sessionStorage.getItem('instascribe:proj-1:edits')).toBeNull()
    expect(sessionStorage.getItem('instascribe:proj-2:edits')).toBeNull()
    expect(sessionStorage.getItem('unrelated:key')).toBe('keep me')
  })
})

describe('cloud draft JOB scoping (G7.1 C)', () => {
  it('drafts for the same project under different jobs never collide', () => {
    persistSceneText('proj-1', 1, 'from job A', 'job-A')
    expect(loadEdits('proj-1', 'job-B').scenes[1]).toBeUndefined()
    expect(loadEdits('proj-1', 'job-A').scenes[1]).toEqual({ text: 'from job A' })
    expect(sessionStorage.getItem('instascribe:proj-1:job-A:edits')).toBeTruthy()
  })

  it('clearAllCloudDrafts also removes job-scoped keys', () => {
    persistSceneText('proj-1', 1, 'x', 'job-A')
    clearAllCloudDrafts()
    expect(sessionStorage.getItem('instascribe:proj-1:job-A:edits')).toBeNull()
  })

  it('a PARTIAL save clears only the fields it sent', () => {
    persistSceneText('proj-1', 2, 'unsent text draft', 'job-A')
    persistSceneActive('proj-1', 2, false, 'job-A')
    clearSceneDraftFields('proj-1', 2, ['active'], 'job-A')
    expect(loadEdits('proj-1', 'job-A').scenes[2]).toEqual({ text: 'unsent text draft' })
    clearSceneDraftFields('proj-1', 2, ['text'], 'job-A')
    expect(loadEdits('proj-1', 'job-A').scenes[2]).toBeUndefined()
  })
})

describe('cloud logout (G7.1 B)', () => {
  it('a fresh cloud tab starts empty and an abandoned reservation cannot resurrect', () => {
    expect(initialProjects()).toEqual([])
    useAppStore.setState({ projects: [{ id: 'abandoned' } as never], isAuthenticated: true })
    useAppStore.getState().logout()
    expect(useAppStore.getState().projects).toEqual([])
    expect(sessionStorage.getItem('instascribe-app') ?? '').not.toContain('abandoned')
    // Login only restores the shell; the authoritative list must repopulate projects.
    useAppStore.getState().login('demo@example.test', 'demo-only-not-a-real-password')
    expect(useAppStore.getState().projects).toEqual([])
  })

  it('clears token, query cache, drafts, and project metadata', async () => {
    setPortfolioToken('logout-test-token')
    persistSceneText('proj-1', 1, 'draft')
    queryClient.setQueryData(queryKeys.manifest('proj-1', 'job-1'), { source: { url: 'signed' } })
    queryClient.setQueryData(queryKeys.cloudScenes('proj-1', 'job-1'), [{ id: 1 }])
    useAppStore.setState({
      isAuthenticated: true,
      currentUser: { email: 'x@y.z', name: 'X', tokenBalance: 1 },
      projects: [{ id: 'proj-1' } as never],
    })

    useAppStore.getState().logout()

    expect(hasPortfolioToken()).toBe(false)
    expect(queryClient.getQueryData(queryKeys.manifest('proj-1', 'job-1'))).toBeUndefined()
    expect(queryClient.getQueryData(queryKeys.cloudScenes('proj-1', 'job-1'))).toBeUndefined()
    expect(sessionStorage.getItem('instascribe:proj-1:edits')).toBeNull()
    const state = useAppStore.getState()
    expect(state.isAuthenticated).toBe(false)
    expect(state.currentUser).toBeNull()
    expect(state.projects).toEqual([])
  })
})

describe('cloud cache identity (G7.1 C)', () => {
  it('every cloud data key carries BOTH projectId and jobId', () => {
    for (const build of [
      queryKeys.cloudScenes,
      queryKeys.cloudEntities,
      queryKeys.cloudAdGaps,
      queryKeys.cloudAudioEvents,
      queryKeys.cloudOverrides,
      queryKeys.manifest,
    ]) {
      const key = build('proj-1', 'job-A') as readonly string[]
      expect(key).toContain('proj-1')
      expect(key).toContain('job-A')
    }
  })

  it('job switch regression: the same project with a NEW job misses the old cache', () => {
    queryClient.setQueryData(queryKeys.cloudScenes('proj-1', 'job-A'), [{ id: 1, text: 'old' }])
    expect(queryClient.getQueryData(queryKeys.cloudScenes('proj-1', 'job-B'))).toBeUndefined()
    queryClient.setQueryData(queryKeys.cloudOverrides('proj-1', 'job-A'), { scene_1: 'o' })
    expect(queryClient.getQueryData(queryKeys.cloudOverrides('proj-1', 'job-B'))).toBeUndefined()
  })
})
