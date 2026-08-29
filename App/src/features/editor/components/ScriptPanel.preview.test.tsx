// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Scene } from '@/types'
import { ScriptPanel } from './ScriptPanel'

const api = vi.hoisted(() => ({
  previewTts: vi.fn(),
  smartFillScene: vi.fn(),
  patchScene: vi.fn(),
}))

vi.mock('@/lib/api', () => ({
  ...api,
}))

const noop = () => {}
const scene: Scene = {
  id: 7,
  sceneNumber: 7,
  sceneKey: 'scene_7',
  startSecs: 0,
  endSecs: 4,
  durationSecs: 4,
  text: 'A door opens.',
  template: '',
  characterIds: [],
  locked: false,
  needsReview: false,
  active: true,
  voiceId: 'nova',
  voiceSpeed: 1.25,
}

class TestAudio {
  onended: (() => void) | null = null
  onerror: (() => void) | null = null
  pause = vi.fn()
  play = vi.fn().mockResolvedValue(undefined)
}

function panel(
  onCloudPreview?: Parameters<typeof ScriptPanel>[0]['onCloudPreview'],
  currentScene: Scene = scene,
  readOnly = false,
) {
  return <ScriptPanel
    projectId="project-1"
    scene={currentScene}
    characters={[]}
    availableGapSecs={4}
    collision={null}
    activeTab="script"
    onTabChange={noop}
    onAdChange={noop}
    onActiveToggle={noop}
    onApply={noop}
    onVoiceChange={noop}
    onSpeedChange={noop}
    onLockedChange={noop}
    onRenameRequest={noop}
    cloudDeferred
    onCloudPreview={onCloudPreview}
    readOnly={readOnly}
  />
}

beforeEach(() => {
  api.previewTts.mockReset()
  vi.stubGlobal('Audio', TestAudio)
  Object.defineProperty(URL, 'createObjectURL', {
    configurable: true,
    value: vi.fn(() => 'blob:preview'),
  })
  Object.defineProperty(URL, 'revokeObjectURL', {
    configurable: true,
    value: vi.fn(),
  })
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('ScriptPanel authenticated cloud preview seam', () => {
  it('uses the exact canonical scene id and never calls the legacy preview route', async () => {
    const onCloudPreview = vi.fn().mockResolvedValue(
      new Blob([new Uint8Array([0x49, 0x44, 0x33])], { type: 'audio/mpeg' }),
    )
    render(panel(onCloudPreview))

    const preview = screen.getByRole('button', { name: 'Preview' })
    expect((preview as HTMLButtonElement).disabled).toBe(false)
    fireEvent.click(preview)

    await waitFor(() => expect(onCloudPreview).toHaveBeenCalledTimes(1))
    const [sceneId, text, voice, speed, signal] = onCloudPreview.mock.calls[0]
    expect({ sceneId, text, voice, speed }).toEqual({
      sceneId: 'scene_7',
      text: 'A door opens.',
      voice: 'nova',
      speed: 1.25,
    })
    expect(signal).toBeInstanceOf(AbortSignal)
    expect(api.previewTts).not.toHaveBeenCalled()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Pause' })).toBeTruthy())
    expect(screen.getByText(/Smart Fill & character rename/)).toBeTruthy()
  })

  it('keeps preview deferred without the authenticated callback and aborts in-flight work on scene change', async () => {
    const signals: AbortSignal[] = []
    const onCloudPreview = vi.fn((
      _sceneId: string,
      _text: string,
      _voice: string,
      _speed: number,
      signal: AbortSignal,
    ) => {
      signals.push(signal)
      return new Promise<Blob>((_resolve, reject) => {
        signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
      })
    })
    const rendered = render(panel(onCloudPreview))
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }))
    await waitFor(() => expect(onCloudPreview).toHaveBeenCalledTimes(1))

    rendered.rerender(panel(onCloudPreview, {
      ...scene,
      id: 8,
      sceneNumber: 8,
      sceneKey: 'scene_8',
      text: 'A window closes.',
    }))
    await waitFor(() => expect(signals[0].aborted).toBe(true))

    rendered.rerender(panel())
    const deferred = screen.getByRole('button', { name: 'Preview' })
    expect((deferred as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(deferred)
    expect(api.previewTts).not.toHaveBeenCalled()
    expect(screen.getByText(/Smart Fill, Preview & character rename/)).toBeTruthy()
  })

  it('aborts in-flight audio when an open review becomes read-only', async () => {
    let requestSignal: AbortSignal | undefined
    const onCloudPreview = vi.fn((
      _sceneId: string,
      _text: string,
      _voice: string,
      _speed: number,
      signal: AbortSignal,
    ) => {
      requestSignal = signal
      return new Promise<Blob>((_resolve, reject) => {
        signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
      })
    })
    const rendered = render(panel(onCloudPreview))
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }))
    await waitFor(() => expect(onCloudPreview).toHaveBeenCalledTimes(1))

    rendered.rerender(panel(undefined, scene, true))
    await waitFor(() => expect(requestSignal?.aborted).toBe(true))
    expect((screen.getByRole('button', { name: 'Preview' }) as HTMLButtonElement).disabled).toBe(true)
  })
})
