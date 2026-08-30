import { beforeEach, describe, expect, it, vi } from 'vitest'

let cloudSession = true
vi.mock('./cloudMode', () => ({
  isCloudMode: () => cloudSession,
  isCloudSession: () => cloudSession,
  cloudApiBase: () => 'http://localhost:8000',
}))

import {
  clearSceneDraftFieldsIfUnchanged,
  clearCurrentModeDrafts,
  getDraftStorageStats,
  persistSceneActive,
  persistSceneText,
} from './persistence'

beforeEach(() => {
  cloudSession = true
  sessionStorage.clear()
  localStorage.clear()
})

describe('mode-scoped draft Settings helpers', () => {
  it('cloud counts and clears session drafts only, preserving token/session metadata and local drafts', () => {
    persistSceneText('cloud-project', 1, 'cloud draft', 'cloud-job')
    sessionStorage.setItem('instascribe:portfolioToken', 'keep-token')
    sessionStorage.setItem('instascribe-app', 'keep-session')
    localStorage.setItem('instascribe:legacy-project:edits', '{"keep":true}')
    expect(getDraftStorageStats()).toMatchObject({ keys: 1, scope: 'session' })
    clearCurrentModeDrafts()
    expect(getDraftStorageStats().keys).toBe(0)
    expect(sessionStorage.getItem('instascribe:portfolioToken')).toBe('keep-token')
    expect(sessionStorage.getItem('instascribe-app')).toBe('keep-session')
    expect(localStorage.getItem('instascribe:legacy-project:edits')).toBeTruthy()
  })

  it('legacy counts and clears local drafts only, preserving cloud session storage', () => {
    cloudSession = false
    persistSceneText('legacy-project', 1, 'legacy draft')
    sessionStorage.setItem('instascribe:cloud-project:cloud-job:edits', '{"keep":true}')
    localStorage.setItem('unrelated:key', 'keep-local')
    expect(getDraftStorageStats()).toMatchObject({ keys: 1, scope: 'local' })
    clearCurrentModeDrafts()
    expect(getDraftStorageStats().keys).toBe(0)
    expect(localStorage.getItem('unrelated:key')).toBe('keep-local')
    expect(sessionStorage.getItem('instascribe:cloud-project:cloud-job:edits')).toBeTruthy()
  })

  it('removes the edits key after its last field clears and ignores stale empty keys', () => {
    persistSceneText('project', 1, 'saved text', 'job')
    persistSceneActive('project', 1, false, 'job')
    const draftKey = 'instascribe:project:job:edits'
    expect(getDraftStorageStats().keys).toBe(1)

    clearSceneDraftFieldsIfUnchanged('project', 1, { text: 'saved text', active: false }, 'job')
    expect(sessionStorage.getItem(draftKey)).toBeNull()
    expect(getDraftStorageStats().keys).toBe(0)

    sessionStorage.setItem(draftKey, '{"scenes":{"1":{}}}')
    expect(getDraftStorageStats()).toMatchObject({ keys: 0, bytes: 0 })
  })
})
