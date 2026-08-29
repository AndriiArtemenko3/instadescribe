import { afterEach, describe, expect, it, vi } from 'vitest'

const reconcile = vi.hoisted(() => vi.fn())

vi.mock('./cloudProjects', () => ({ reconcileCloudProjects: reconcile }))

import { CloudApiError } from './cloudApi'
import { reportProjectMutationError } from './projectMutationFeedback'

afterEach(() => {
  reconcile.mockReset()
  vi.restoreAllMocks()
})

describe('shared Home/Projects mutation feedback', () => {
  it('reconciles before reporting a stale-version retry', async () => {
    const order: string[] = []
    reconcile.mockImplementation(async () => {
      order.push('reconciled')
      return true
    })
    const alert = vi.fn(() => { order.push('alerted') })
    Object.defineProperty(window, 'alert', { configurable: true, value: alert })

    await reportProjectMutationError(
      'rename',
      new CloudApiError('conflict', 409, 'stale_version'),
    )

    expect(reconcile).toHaveBeenCalledWith({ forceFresh: true })
    expect(order).toEqual(['reconciled', 'alerted'])
    expect(alert).toHaveBeenCalledWith(expect.stringMatching(/changed elsewhere.*latest version.*retry/i))
  })

  it('never displays raw cloud response details', async () => {
    const alert = vi.fn()
    Object.defineProperty(window, 'alert', { configurable: true, value: alert })
    const error = new CloudApiError('service', 503, 'persistence_unavailable')
    Object.assign(error, { responseBody: 'postgresql://user:secret@internal' })

    await reportProjectMutationError('update star', error)

    expect(reconcile).not.toHaveBeenCalled()
    expect(alert).toHaveBeenCalledWith(expect.stringMatching(/cloud service did not accept/i))
    expect(String(alert.mock.calls[0][0])).not.toContain('postgresql://')
  })

  it('reports a safe refresh instruction when stale reconciliation fails', async () => {
    reconcile.mockResolvedValue(false)
    const alert = vi.fn()
    Object.defineProperty(window, 'alert', { configurable: true, value: alert })

    await expect(reportProjectMutationError(
      'rename',
      new CloudApiError('conflict', 409, 'stale_version'),
    )).resolves.toBeUndefined()

    expect(alert).toHaveBeenCalledWith(expect.stringMatching(/could not be loaded.*refresh/i))
    expect(String(alert.mock.calls[0][0])).not.toContain('postgresql://')
  })

  it('sanitizes unexpected mutation failures as well', async () => {
    const alert = vi.fn()
    Object.defineProperty(window, 'alert', { configurable: true, value: alert })

    await reportProjectMutationError('rename', new Error('Bearer private-token'))

    expect(alert).toHaveBeenCalledWith(expect.stringMatching(/unexpected error/i))
    expect(String(alert.mock.calls[0][0])).not.toContain('private-token')
  })
})
