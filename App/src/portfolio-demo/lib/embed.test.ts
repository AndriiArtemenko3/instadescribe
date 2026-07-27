// @vitest-environment node
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// The exit event's origin discipline: posted only to the explicit allowed
// parent origins (plus the dev-server origin under DEV), never to '*', and
// carrying nothing but its type.

type WindowStub = {
  location: { origin: string }
  parent: { postMessage: ReturnType<typeof vi.fn> } | WindowStub
}

const DEV_ORIGIN = 'http://localhost:5175'

function stubWindow(embedded: boolean): WindowStub {
  const stub: WindowStub = {
    location: { origin: DEV_ORIGIN },
    parent: { postMessage: vi.fn() },
  }
  if (!embedded) stub.parent = stub // top-level: window.parent === window
  vi.stubGlobal('window', stub)
  return stub
}

beforeEach(() => vi.resetModules())
afterEach(() => vi.unstubAllGlobals())

describe('embed exit message contract', () => {
  it('never uses the wildcard origin and carries no user data', async () => {
    const stub = stubWindow(true)
    const { postExitMessage, EXIT_MESSAGE_TYPE } = await import('./embed')
    postExitMessage()
    const calls = (stub.parent as { postMessage: ReturnType<typeof vi.fn> }).postMessage.mock.calls
    expect(calls.length).toBeGreaterThan(0)
    for (const [payload, origin] of calls) {
      expect(origin).not.toBe('*')
      expect(payload).toEqual({ type: EXIT_MESSAGE_TYPE })
    }
  })

  it('targets exactly the production portfolio origins (+ the dev origin under DEV)', async () => {
    const stub = stubWindow(true)
    const { postExitMessage, PARENT_ORIGINS } = await import('./embed')
    postExitMessage()
    const origins = (
      stub.parent as { postMessage: ReturnType<typeof vi.fn> }
    ).postMessage.mock.calls.map(([, o]) => o)
    expect(origins).toEqual([...PARENT_ORIGINS])
    expect(PARENT_ORIGINS).toContain('https://andriiartemenko.com')
    expect(PARENT_ORIGINS).toContain('https://www.andriiartemenko.com')
    // Vitest runs with DEV=true, so the stubbed dev origin is included; the
    // production build strips this branch (import.meta.env.DEV is false).
    expect(PARENT_ORIGINS).toContain(DEV_ORIGIN)
    expect(PARENT_ORIGINS).not.toContain('*')
  })

  it('does nothing when not embedded (window.parent === window)', async () => {
    stubWindow(false)
    const { postExitMessage } = await import('./embed')
    expect(() => postExitMessage()).not.toThrow()
  })
})
