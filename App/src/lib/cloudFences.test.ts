// G7 B6/B7: in explicit cloud mode every deferred legacy operation throws
// BEFORE any network activity — zero legacy requests can be issued.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

beforeEach(() => {
  vi.stubEnv('VITE_CLOUD_MODE', '1')
})

afterEach(() => {
  vi.unstubAllEnvs()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('cloud-mode legacy fences', () => {
  it('every deferred api.ts operation throws without a network call', async () => {
    const spy = vi.fn()
    vi.stubGlobal('fetch', spy)
    const api = await import('./api')
    await expect(api.previewTts('p', 1, 't', 'onyx', 1)).rejects.toThrow(/Portfolio Strong/)
    await expect(api.patchScene('p', 1, { ad: 'x' })).rejects.toThrow(/Portfolio Strong/)
    await expect(api.patchEntity('p', 'c', 'n')).rejects.toThrow(/Portfolio Strong/)
    await expect(api.requestExport('p', 'onyx', 'mp4')).rejects.toThrow(/Portfolio Strong/)
    await expect(api.pollExport('p', 'e')).rejects.toThrow(/Portfolio Strong/)
    await expect(api.smartFillScene('p', 't', 3)).rejects.toThrow(/Portfolio Strong/)
    await expect(api.fetchOverrides('p')).rejects.toThrow(/Portfolio Strong/)
    expect(() => api.exportDownloadUrl('p', 'e')).toThrow(/Portfolio Strong/)
    expect(spy).not.toHaveBeenCalled()
  })

  it('keeps the registered tutorial on deterministic browser-only operations', async () => {
    const spy = vi.fn()
    vi.stubGlobal('fetch', spy)
    const api = await import('./api')
    const { useAppStore } = await import('@/store/appStore')
    const { getAvailableTutorial, tutorialProject } = await import('./tutorials')
    const tutorial = getAvailableTutorial('ent-short')
    expect(tutorial).toBeDefined()
    useAppStore.setState({ projects: [tutorialProject(tutorial!)] })

    await expect(api.patchScene('ent-short', 1, { ad: 'edited locally' })).resolves.toBeUndefined()
    await expect(api.patchEntity('ent-short', 'character-1', 'Renamed')).resolves.toBeUndefined()
    await expect(api.fetchOverrides('ent-short')).resolves.toMatchObject({
      scene_1: { ad: 'edited locally' },
    })
    await expect(api.smartFillScene('ent-short', 'one two three four five', 1)).resolves.toMatchObject({
      model: 'demo (local)',
    })
    await expect(api.requestExport('ent-short', 'onyx', 'mp4')).resolves.toEqual({
      exportId: 'demo',
      format: 'mp4',
    })
    await expect(api.pollExport('ent-short', 'demo')).resolves.toMatchObject({ status: 'ready' })
    expect(api.exportDownloadUrl('ent-short', 'demo')).toBe('/data/sintel-blender-cc/export.mp4')
    expect(spy).not.toHaveBeenCalled()

    spy.mockResolvedValue(new Response('', { status: 404 }))
    await expect(api.previewTts('ent-short', 1, 'text', 'onyx', 1)).resolves.toBeInstanceOf(Blob)
    expect(spy.mock.calls.map(([input]) => String(input))).toEqual([
      '/data/sintel-blender-cc/tts/scene_1_onyx.mp3',
      '/demo/silence.mp3',
    ])
    useAppStore.setState({ projects: [] })
  })

  it('every deferred uploadApi.ts operation throws without a network call', async () => {
    const spy = vi.fn()
    vi.stubGlobal('fetch', spy)
    const uploadApi = await import('./uploadApi')
    const file = new File([new Uint8Array(4)], 'a.mp4', { type: 'video/mp4' })
    await expect(
      uploadApi.submitJob(file, 'n', {} as never, 10),
    ).rejects.toThrow(/Portfolio Strong/)
    await expect(uploadApi.pollStatus('j')).rejects.toThrow(/Portfolio Strong/)
    await expect(uploadApi.deleteProjectOnServer('p')).rejects.toThrow(/Portfolio Strong/)
    await expect(uploadApi.patchProjectOnServer('p', {})).rejects.toThrow(/Portfolio Strong/)
    await expect(uploadApi.reconcileProjectsWithServer()).resolves.toBeUndefined()
    expect(spy).not.toHaveBeenCalled() // reconcile is a silent no-op in cloud
  })
})
