import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./cloudMode', () => ({
  isCloudMode: () => true,
  isCloudSession: () => true,
  cloudApiBase: () => 'http://localhost:8000',
}))

import {
  CloudDraftSaveDisposedError,
  CloudSceneSaveCoordinator,
} from './cloudDraftSave'
import { loadEdits } from './persistence'
import { CloudApiError } from './cloudApi'
import type { CloudPatchResponse, CloudSceneMutation } from './cloudApi'

const PROJECT = 'proj-1'
const JOB = 'job-1'
const SCENE = 1

interface PendingPatch {
  jobId: string
  sceneKey: string
  patch: CloudSceneMutation
  expectedVersion: number
  resolve: (value: CloudPatchResponse) => void
  reject: (reason: unknown) => void
}

function controlledPatches() {
  const pending: PendingPatch[] = []
  const wire: Array<{ patch: CloudSceneMutation; expectedVersion: number }> = []
  const request = vi.fn((
    jobId: string,
    sceneKey: string,
    patch: CloudSceneMutation,
    expectedVersion: number,
  ) =>
    new Promise<CloudPatchResponse>((resolve, reject) => {
      pending.push({ jobId, sceneKey, patch, expectedVersion, resolve, reject })
    }),
  )
  const succeed = (index: number) => {
    const call = pending[index]
    wire.push({ patch: call.patch, expectedVersion: call.expectedVersion })
    const version = call.expectedVersion + 1
    const reviewStatus = call.patch.reviewStatus ?? 'edited'
    const reviewedAt = reviewStatus === 'approved' || reviewStatus === 'rejected'
      ? '2026-08-07T12:00:00Z'
      : null
    const { reviewStatus: _command, ...editable } = call.patch
    void _command
    call.resolve({
      projectId: PROJECT,
      jobId: call.jobId,
      sceneId: call.sceneKey,
      version,
      reviewStatus,
      reviewedAt,
      updatedAt: '2026-08-07T12:00:00Z',
      override: {
        active: true,
        locked: false,
        version,
        reviewStatus,
        reviewedAt,
        updatedAt: '2026-08-07T12:00:00Z',
        ...editable,
      },
    })
  }
  const fail = (index: number) => pending[index].reject(new Error('sanitized failure'))
  return { request, pending, wire, succeed, fail }
}

async function flush(): Promise<void> {
  await Promise.resolve()
  await Promise.resolve()
}

beforeEach(() => {
  sessionStorage.clear()
  localStorage.clear()
})

describe('CloudSceneSaveCoordinator', () => {
  it('serializes A then B so the final wire/server state and reconstruction are B', async () => {
    const controlled = controlledPatches()
    const coordinator = new CloudSceneSaveCoordinator(controlled.request)
    const saveA = coordinator.save(PROJECT, JOB, SCENE, 'scene_alpha', { ad: 'A' }, 0)
    const saveB = coordinator.save(PROJECT, JOB, SCENE, 'scene_alpha', { ad: 'B' }, 0)

    await flush()
    expect(controlled.request).toHaveBeenCalledTimes(1)
    expect(controlled.pending[0].patch).toEqual({ ad: 'A' })
    controlled.succeed(0)
    const outcomeA = await saveA
    await flush()
    expect(outcomeA.latest).toBe(false)
    expect(controlled.request).toHaveBeenCalledTimes(2)
    expect(loadEdits(PROJECT, JOB).scenes[SCENE]?.text).toBe('B')

    controlled.succeed(1)
    const outcomeB = await saveB
    expect(outcomeB.latest).toBe(true)
    expect(controlled.wire).toEqual([
      { patch: { ad: 'A' }, expectedVersion: 0 },
      { patch: { ad: 'B' }, expectedVersion: 1 },
    ])
    expect(controlled.wire.at(-1)?.patch.ad).toBe('B')
    expect(loadEdits(PROJECT, JOB).scenes[SCENE]).toBeUndefined()
  })

  it('serializes active true then false and leaves false as final user intent', async () => {
    const controlled = controlledPatches()
    const coordinator = new CloudSceneSaveCoordinator(controlled.request)
    const first = coordinator.save(PROJECT, JOB, SCENE, 'scene_alpha', { active: true }, 0)
    const second = coordinator.save(PROJECT, JOB, SCENE, 'scene_alpha', { active: false }, 0)

    await flush()
    controlled.succeed(0)
    await first
    await flush()
    expect(loadEdits(PROJECT, JOB).scenes[SCENE]?.active).toBe(false)
    controlled.succeed(1)
    await second
    expect(controlled.wire.at(-1)?.patch.active).toBe(false)
    expect(loadEdits(PROJECT, JOB).scenes[SCENE]).toBeUndefined()
  })

  it('sends and applies B even when A fails', async () => {
    const controlled = controlledPatches()
    const coordinator = new CloudSceneSaveCoordinator(controlled.request)
    const saveA = coordinator.save(PROJECT, JOB, SCENE, 'scene_alpha', { ad: 'A' }, 0)
    const observedA = saveA.catch((error) => error)
    const saveB = coordinator.save(PROJECT, JOB, SCENE, 'scene_alpha', { ad: 'B' }, 0)

    await flush()
    controlled.fail(0)
    expect(await observedA).toBeInstanceOf(Error)
    await flush()
    expect(controlled.request).toHaveBeenCalledTimes(2)
    controlled.succeed(1)
    await saveB
    expect(controlled.wire).toEqual([{ patch: { ad: 'B' }, expectedVersion: 0 }])
    expect(loadEdits(PROJECT, JOB).scenes[SCENE]).toBeUndefined()
  })

  it('retains the latest B retry draft when B fails', async () => {
    const controlled = controlledPatches()
    const coordinator = new CloudSceneSaveCoordinator(controlled.request)
    const saveA = coordinator.save(PROJECT, JOB, SCENE, 'scene_alpha', { ad: 'A' }, 0)
    const saveB = coordinator.save(PROJECT, JOB, SCENE, 'scene_alpha', { ad: 'B' }, 0)
    const observedB = saveB.catch((error) => error)

    await flush()
    controlled.succeed(0)
    await saveA
    await flush()
    controlled.fail(1)
    expect(await observedB).toBeInstanceOf(Error)
    expect(loadEdits(PROJECT, JOB).scenes[SCENE]).toEqual({ text: 'B' })
  })

  it('runs unrelated scenes independently', async () => {
    const controlled = controlledPatches()
    const coordinator = new CloudSceneSaveCoordinator(controlled.request)
    const first = coordinator.save(PROJECT, JOB, 1, 'scene_one', { ad: 'one' }, 0)
    const second = coordinator.save(PROJECT, JOB, 2, 'scene_two', { ad: 'two' }, 0)

    await flush()
    expect(controlled.request).toHaveBeenCalledTimes(2)
    controlled.succeed(1)
    await second
    expect(loadEdits(PROJECT, JOB).scenes[1]?.text).toBe('one')
    controlled.succeed(0)
    await first
  })

  it('clears an unchanged latest success and removes the empty edits key', async () => {
    const controlled = controlledPatches()
    const coordinator = new CloudSceneSaveCoordinator(controlled.request)
    const save = coordinator.save(PROJECT, JOB, SCENE, 'scene_alpha', {
      ad: 'only value',
      active: true,
    }, 0)
    await flush()
    expect(sessionStorage.getItem(`instascribe:${PROJECT}:${JOB}:edits`)).not.toBeNull()
    controlled.succeed(0)
    expect((await save).latest).toBe(true)
    expect(sessionStorage.getItem(`instascribe:${PROJECT}:${JOB}:edits`)).toBeNull()
  })

  it('marks an older success stale while a newer intent is pending', async () => {
    const controlled = controlledPatches()
    const coordinator = new CloudSceneSaveCoordinator(controlled.request)
    const oldSave = coordinator.save(PROJECT, JOB, SCENE, 'scene_alpha', { ad: 'old' }, 0)
    const newSave = coordinator.save(PROJECT, JOB, SCENE, 'scene_alpha', { ad: 'new' }, 0)
    await flush()
    controlled.succeed(0)
    expect((await oldSave).latest).toBe(false)
    await flush()
    controlled.succeed(1)
    expect((await newSave).latest).toBe(true)
  })

  it('dispose prevents queued work and keeps its persisted latest draft', async () => {
    const controlled = controlledPatches()
    const coordinator = new CloudSceneSaveCoordinator(controlled.request)
    const first = coordinator.save(PROJECT, JOB, SCENE, 'scene_alpha', { ad: 'A' }, 0)
    const observedFirst = first.catch((error) => error)
    const second = coordinator.save(PROJECT, JOB, SCENE, 'scene_alpha', { ad: 'B' }, 0)
    const observedSecond = second.catch((error) => error)
    await flush()
    coordinator.dispose()
    controlled.succeed(0)
    expect(await observedFirst).toBeInstanceOf(CloudDraftSaveDisposedError)
    expect(await observedSecond).toBeInstanceOf(CloudDraftSaveDisposedError)
    expect(controlled.request).toHaveBeenCalledTimes(1)
    expect(loadEdits(PROJECT, JOB).scenes[SCENE]).toEqual({ text: 'B' })
  })

  it('dispose while a lone PATCH is pending preserves its remount draft after success', async () => {
    const controlled = controlledPatches()
    const coordinator = new CloudSceneSaveCoordinator(controlled.request)
    const save = coordinator.save(PROJECT, JOB, SCENE, 'scene_alpha', { ad: 'B' }, 0)
    const observed = save.catch((error) => error)
    await flush()
    coordinator.dispose()
    controlled.succeed(0)
    expect(await observed).toBeInstanceOf(CloudDraftSaveDisposedError)
    expect(loadEdits(PROJECT, JOB).scenes[SCENE]).toEqual({ text: 'B' })
  })

  it('keeps the draft when the response acknowledgement cannot be committed', async () => {
    const controlled = controlledPatches()
    const coordinator = new CloudSceneSaveCoordinator(controlled.request)
    const save = coordinator.save(
      PROJECT,
      JOB,
      SCENE,
      'scene_alpha',
      { ad: 'B' },
      0,
      () => { throw new Error('cache fence failed') },
    )
    const observed = save.catch((error) => error)
    await flush()
    controlled.succeed(0)
    expect(await observed).toBeInstanceOf(Error)
    expect(loadEdits(PROJECT, JOB).scenes[SCENE]).toEqual({ text: 'B' })
  })

  it('starts from the fetched version and evolves the exact token after each serialized success', async () => {
    const controlled = controlledPatches()
    const coordinator = new CloudSceneSaveCoordinator(controlled.request)
    const first = coordinator.save(PROJECT, JOB, SCENE, 'scene_alpha', { ad: 'A' }, 7)
    const second = coordinator.save(PROJECT, JOB, SCENE, 'scene_alpha', {
      ad: 'B',
      reviewStatus: 'approved',
    }, 7)

    await flush()
    expect(controlled.pending[0].expectedVersion).toBe(7)
    controlled.succeed(0)
    await first
    await flush()
    expect(controlled.pending[1].expectedVersion).toBe(8)
    controlled.succeed(1)
    const result = await second
    expect(result.response.version).toBe(9)
    expect(result.response.reviewStatus).toBe('approved')
  })

  it('fences queued writes after a stale conflict and retains the newest draft for refresh-and-retry', async () => {
    const controlled = controlledPatches()
    const coordinator = new CloudSceneSaveCoordinator(controlled.request)
    const first = coordinator.save(PROJECT, JOB, SCENE, 'scene_alpha', { ad: 'A' }, 4)
    const second = coordinator.save(PROJECT, JOB, SCENE, 'scene_alpha', { ad: 'B' }, 4)
    const firstObserved = first.catch((error) => error)
    const secondObserved = second.catch((error) => error)

    await flush()
    controlled.pending[0].reject(new CloudApiError('conflict', 409, 'stale_version'))
    expect(await firstObserved).toMatchObject({ code: 'stale_version' })
    expect(await secondObserved).toMatchObject({ code: 'stale_version' })
    expect(controlled.request).toHaveBeenCalledTimes(1)
    expect(loadEdits(PROJECT, JOB).scenes[SCENE]).toEqual({ text: 'B' })
  })

  it('recovers a stale lane only when a retry carries the freshly refetched version', async () => {
    const controlled = controlledPatches()
    const coordinator = new CloudSceneSaveCoordinator(controlled.request)
    const stale = coordinator.save(PROJECT, JOB, SCENE, 'scene_alpha', { ad: 'stale' }, 4)
    const obsoleteRetry = coordinator.save(PROJECT, JOB, SCENE, 'scene_alpha', { ad: 'obsolete' }, 4)
    const staleObserved = stale.catch((error) => error)
    const obsoleteObserved = obsoleteRetry.catch((error) => error)

    await flush()
    controlled.pending[0].reject(new CloudApiError('conflict', 409, 'stale_version'))
    // Enqueue a caller retry carrying the newly refetched version before the
    // already-queued obsolete intent gets its turn. The obsolete entry stays
    // fenced; only the fresh token may reopen the lane.
    const freshRetry = coordinator.save(PROJECT, JOB, SCENE, 'scene_alpha', { ad: 'fresh' }, 6)
    expect(await staleObserved).toMatchObject({ code: 'stale_version' })
    expect(await obsoleteObserved).toMatchObject({ code: 'stale_version' })
    await flush()
    expect(controlled.request).toHaveBeenCalledTimes(2)
    expect(controlled.pending[1].expectedVersion).toBe(6)
    controlled.succeed(1)
    await expect(freshRetry).resolves.toMatchObject({ response: { version: 7 } })
    expect(loadEdits(PROJECT, JOB).scenes[SCENE]).toBeUndefined()
  })
})
