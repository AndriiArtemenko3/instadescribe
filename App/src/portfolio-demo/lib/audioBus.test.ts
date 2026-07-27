import { describe, it, expect, beforeEach, vi } from 'vitest'
import { claimAudio, stopAllAudio, clearAudioClaim, currentAudioOwner } from './audioBus'

beforeEach(() => {
  stopAllAudio()
})

describe('audioBus — single audio owner', () => {
  it('stops the previous owner when a new source claims', () => {
    const stopA = vi.fn()
    const stopB = vi.fn()
    claimAudio('baked-line', stopA)
    claimAudio('source-video', stopB)
    expect(stopA).toHaveBeenCalledTimes(1)
    expect(stopB).not.toHaveBeenCalled()
    expect(currentAudioOwner()).toBe('source-video')
  })

  it('re-claiming by the same owner does not self-stop', () => {
    const stop = vi.fn()
    claimAudio('speech', stop)
    claimAudio('speech', stop)
    expect(stop).not.toHaveBeenCalled()
  })

  it('stopAllAudio stops the active source exactly once and clears the claim', () => {
    const stop = vi.fn()
    claimAudio('described', stop)
    stopAllAudio()
    stopAllAudio()
    expect(stop).toHaveBeenCalledTimes(1)
    expect(currentAudioOwner()).toBeNull()
  })

  it('clearAudioClaim drops the claim without stopping (natural end)', () => {
    const stop = vi.fn()
    claimAudio('baked-line', stop)
    clearAudioClaim('baked-line')
    expect(stop).not.toHaveBeenCalled()
    expect(currentAudioOwner()).toBeNull()
  })

  it('clearAudioClaim ignores non-owners', () => {
    const stop = vi.fn()
    claimAudio('baked-line', stop)
    clearAudioClaim('speech')
    expect(currentAudioOwner()).toBe('baked-line')
  })

  it('a stop callback that claims again does not corrupt the bus', () => {
    // Regression: stopping must clear state BEFORE invoking the callback.
    claimAudio('a', () => claimAudio('b', () => {}))
    claimAudio('c', () => {})
    expect(currentAudioOwner()).toBe('c')
  })
})
