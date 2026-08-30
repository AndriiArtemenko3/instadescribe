import { afterEach, describe, expect, it, vi } from 'vitest'
import { ObjectUrlOwner } from './objectUrlOwner'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('ObjectUrlOwner', () => {
  it('replacement revokes the prior URL exactly once and selects only the new URL', () => {
    const create = vi.fn()
      .mockReturnValueOnce('blob:first')
      .mockReturnValueOnce('blob:second')
    const revoke = vi.fn()
    vi.stubGlobal('URL', { createObjectURL: create, revokeObjectURL: revoke })
    const owner = new ObjectUrlOwner()
    const first = owner.replace(new Blob())
    const second = owner.replace(new Blob())
    owner.revoke(first) // stale metadata completion cannot double-revoke
    expect(revoke).toHaveBeenCalledTimes(1)
    expect(revoke).toHaveBeenCalledWith('blob:first')
    expect(owner.current).toBe(second)
    expect(owner.owns(first)).toBe(false)
  })

  it('cancel/unmount clear revokes the final URL exactly once and leaves no selection', () => {
    const revoke = vi.fn()
    vi.stubGlobal('URL', { createObjectURL: vi.fn(() => 'blob:final'), revokeObjectURL: revoke })
    const owner = new ObjectUrlOwner()
    owner.replace(new Blob())
    owner.clear() // cancel
    owner.clear() // later unmount
    expect(revoke).toHaveBeenCalledTimes(1)
    expect(revoke).toHaveBeenCalledWith('blob:final')
    expect(owner.current).toBeNull()
  })
})
