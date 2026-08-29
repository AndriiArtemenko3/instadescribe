// @vitest-environment jsdom

import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  createCloudJob: vi.fn(),
  completeCloudUpload: vi.fn(),
  getCloudJob: vi.fn(),
}))

vi.mock('@/lib/cloudMode', () => ({
  isCloudMode: () => true,
  isCloudSession: () => true,
  cloudApiBase: () => 'http://localhost:8000',
}))

vi.mock('@/lib/cloudApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/cloudApi')>()
  return {
    ...actual,
    createCloudJob: api.createCloudJob,
    completeCloudUpload: api.completeCloudUpload,
    getCloudJob: api.getCloudJob,
  }
})

import { useUploadFlow } from './useUploadFlow'
import { CloudApiError, type CloudCreateResponse } from '@/lib/cloudApi'
import { useAppStore } from '@/store/appStore'

const IDS = {
  projectId: '11111111-1111-4111-8111-111111111111',
  projectVersion: 1,
  jobId: '22222222-2222-4222-8222-222222222222',
}

function createResponse(
  ids = IDS,
  expiresAt = '2099-01-01T00:00:00Z',
): CloudCreateResponse {
  return {
    ...ids,
    upload: {
      url: 'https://storage.example.invalid/direct-upload',
      fields: { key: `uploads/${ids.jobId}/source.mp4`, policy: 'opaque-policy' },
      expiresAt,
    },
  }
}

function deferred<T>() {
  let resolve: (value: T) => void = () => {}
  let reject: (reason: unknown) => void = () => {}
  const promise = new Promise<T>((done, fail) => {
    resolve = done
    reject = fail
  })
  return { promise, resolve, reject }
}

function file(name = 'clip.mp4'): File {
  return new File(['video-bytes'], name, { type: 'video/mp4' })
}

function installObjectUrlAndMetadata() {
  const urls = ['blob:preview-1', 'blob:metadata-1', 'blob:preview-2', 'blob:metadata-2']
  const createObjectURL = vi.fn(() => urls.shift() ?? 'blob:extra')
  const revokeObjectURL = vi.fn()
  class TestURL extends URL {
    static createObjectURL = createObjectURL
    static revokeObjectURL = revokeObjectURL
  }
  vi.stubGlobal('URL', TestURL)

  const nativeCreateElement = document.createElement.bind(document)
  vi.spyOn(document, 'createElement').mockImplementation((tagName, options) => {
    const element = nativeCreateElement(tagName, options)
    if (tagName.toLowerCase() !== 'video') return element
    const video = element as HTMLVideoElement
    Object.defineProperty(video, 'duration', { configurable: true, value: 42 })
    Object.defineProperty(video, 'src', {
      configurable: true,
      set: () => queueMicrotask(() => video.onloadedmetadata?.(new Event('loadedmetadata'))),
    })
    return video
  })
  return { createObjectURL, revokeObjectURL }
}

async function chooseFile(result: { current: ReturnType<typeof useUploadFlow> }, selected = file()) {
  await act(async () => {
    await result.current.setFile(selected)
  })
}

beforeEach(() => {
  sessionStorage.clear()
  localStorage.clear()
  api.createCloudJob.mockReset()
  api.completeCloudUpload.mockReset()
  api.getCloudJob.mockReset()
  useAppStore.setState({ projects: [], isAuthenticated: true })
  installObjectUrlAndMetadata()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('mounted useUploadFlow lifecycle and recovery', () => {
  it('revokes a replaced preview exactly once and cancel revokes the final preview exactly once', async () => {
    const { result, unmount } = renderHook(() => useUploadFlow())
    const revoke = (URL as typeof URL & { revokeObjectURL: ReturnType<typeof vi.fn> }).revokeObjectURL
    await chooseFile(result, file('first.mp4'))
    await chooseFile(result, file('second.mp4'))

    expect(revoke.mock.calls.filter(([url]) => url === 'blob:preview-1')).toHaveLength(1)
    expect(revoke.mock.calls.filter(([url]) => url === 'blob:metadata-1')).toHaveLength(1)
    expect(revoke.mock.calls.filter(([url]) => url === 'blob:metadata-2')).toHaveLength(1)

    act(() => result.current.cancel())
    expect(revoke.mock.calls.filter(([url]) => url === 'blob:preview-2')).toHaveLength(1)
    unmount()
    expect(revoke.mock.calls.filter(([url]) => url === 'blob:preview-2')).toHaveLength(1)
  })

  it('unmount revokes the final preview exactly once', async () => {
    const { result, unmount } = renderHook(() => useUploadFlow())
    const revoke = (URL as typeof URL & { revokeObjectURL: ReturnType<typeof vi.fn> }).revokeObjectURL
    await chooseFile(result)
    unmount()
    expect(revoke.mock.calls.filter(([url]) => url === 'blob:preview-1')).toHaveLength(1)
  })

  it('publishes IDs before S3, then rehydrates the durable pending card before completion responds', async () => {
    const s3 = deferred<Response>()
    const completion = deferred<void>()
    api.createCloudJob.mockResolvedValue(createResponse())
    api.completeCloudUpload.mockReturnValue(completion.promise)
    vi.stubGlobal('fetch', vi.fn(() => s3.promise))
    const { result } = renderHook(() => useUploadFlow())
    await chooseFile(result)

    let submission!: Promise<void>
    act(() => { submission = result.current.submit() })
    await waitFor(() => expect(result.current.state.jobId).toBe(IDS.jobId))
    expect(result.current.state.newProjectId).toBe(IDS.projectId)
    expect(useAppStore.getState().projects).toEqual([])

    act(() => s3.resolve(new Response(null, { status: 204 })))
    await waitFor(() => expect(useAppStore.getState().projects[0]).toMatchObject({
      id: IDS.projectId,
      projectVersion: IDS.projectVersion,
      jobId: IDS.jobId,
      status: 'confirmation_pending',
      completionPending: true,
    }))
    const persistedAfterS3 = sessionStorage.getItem('instascribe-app')
    expect(persistedAfterS3).toContain('confirmation_pending')
    expect(persistedAfterS3).toContain(IDS.jobId)
    useAppStore.setState({ projects: [] })
    sessionStorage.setItem('instascribe-app', persistedAfterS3!)
    await act(async () => { await useAppStore.persist.rehydrate() })
    expect(useAppStore.getState().projects[0]).toMatchObject({
      id: IDS.projectId,
      projectVersion: IDS.projectVersion,
      jobId: IDS.jobId,
      status: 'confirmation_pending',
      completionPending: true,
    })
    // Project metadata can advance while upload-complete is in flight.
    // Completion must not replay its create-time projectVersion.
    useAppStore.getState().updateProject(IDS.projectId, {
      projectVersion: 3,
      name: 'Concurrent rename',
    })
    act(() => completion.resolve())
    await act(async () => { await submission })
    expect(useAppStore.getState().projects[0]).toMatchObject({
      name: 'Concurrent rename',
      projectVersion: 3,
      status: 'processing',
      completionPending: false,
    })
  })

  it('rehydrates the same-job recovery card after completion transport exhausts before a response', async () => {
    vi.useFakeTimers()
    api.createCloudJob.mockResolvedValue(createResponse())
    api.completeCloudUpload
      .mockRejectedValueOnce(new CloudApiError('network'))
      .mockRejectedValueOnce(new CloudApiError('network'))
      .mockRejectedValueOnce(new CloudApiError('network'))
    const s3Spy = vi.fn(async () => new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', s3Spy)
    const { result } = renderHook(() => useUploadFlow())
    await chooseFile(result)

    let submission!: Promise<void>
    act(() => { submission = result.current.submit() })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000)
      await submission
    })
    expect(api.createCloudJob).toHaveBeenCalledTimes(1)
    expect(s3Spy).toHaveBeenCalledTimes(1)
    expect(api.completeCloudUpload).toHaveBeenCalledTimes(3)
    expect(useAppStore.getState().projects[0]).toMatchObject({
      id: IDS.projectId,
      jobId: IDS.jobId,
      status: 'confirmation_pending',
      completionPending: true,
    })
    expect(result.current.state.submitError).toMatch(/confirmation is pending/i)

    const persistedAfterFailure = sessionStorage.getItem('instascribe-app')
    useAppStore.setState({ projects: [] })
    sessionStorage.setItem('instascribe-app', persistedAfterFailure!)
    await act(async () => { await useAppStore.persist.rehydrate() })
    expect(useAppStore.getState().projects[0]).toMatchObject({
      id: IDS.projectId,
      jobId: IDS.jobId,
      status: 'confirmation_pending',
      completionPending: true,
    })
  })

  it('input change while create is pending supersedes the stale payload before S3', async () => {
    const create = deferred<CloudCreateResponse>()
    api.createCloudJob.mockReturnValue(create.promise)
    const s3Spy = vi.fn(async () => new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', s3Spy)
    const { result } = renderHook(() => useUploadFlow())
    await chooseFile(result)
    act(() => result.current.setProjectName('Payload A'))

    let submission!: Promise<void>
    act(() => { submission = result.current.submit() })
    await waitFor(() => expect(api.createCloudJob).toHaveBeenCalledTimes(1))
    act(() => result.current.setProjectName('Payload B'))
    act(() => create.resolve(createResponse()))
    await act(async () => { await submission })

    expect(api.createCloudJob.mock.calls[0][0]).toMatchObject({ name: 'Payload A' })
    expect(result.current.state.projectName).toBe('Payload B')
    expect(s3Spy).not.toHaveBeenCalled()
    expect(api.completeCloudUpload).not.toHaveBeenCalled()
    expect(result.current.state.jobId).toBeNull()
    expect(useAppStore.getState().projects).toEqual([])
  })

  it('late S3 completion after cancel cannot mutate hook or project state', async () => {
    const s3 = deferred<Response>()
    api.createCloudJob.mockResolvedValue(createResponse())
    vi.stubGlobal('fetch', vi.fn(() => s3.promise))
    const { result } = renderHook(() => useUploadFlow())
    await chooseFile(result)

    let submission!: Promise<void>
    act(() => { submission = result.current.submit() })
    await waitFor(() => expect(result.current.state.jobId).toBe(IDS.jobId))
    act(() => result.current.cancel())
    act(() => s3.resolve(new Response(null, { status: 204 })))
    await act(async () => { await submission })

    expect(result.current.state.jobId).toBeNull()
    expect(result.current.state.newProjectId).toBeNull()
    expect(useAppStore.getState().projects).toEqual([])
    expect(api.completeCloudUpload).not.toHaveBeenCalled()
  })

  it('late S3 completion after unmount cannot publish a project', async () => {
    const s3 = deferred<Response>()
    api.createCloudJob.mockResolvedValue(createResponse())
    vi.stubGlobal('fetch', vi.fn(() => s3.promise))
    const { result, unmount } = renderHook(() => useUploadFlow())
    await chooseFile(result)

    const submission = result.current.submit()
    await waitFor(() => expect(result.current.state.jobId).toBe(IDS.jobId))
    unmount()
    s3.resolve(new Response(null, { status: 204 }))
    await submission
    expect(useAppStore.getState().projects).toEqual([])
    expect(api.completeCloudUpload).not.toHaveBeenCalled()
  })

  it('capacity failure stays retryable on the same job with no second create or S3 POST', async () => {
    vi.useFakeTimers()
    api.createCloudJob.mockResolvedValue(createResponse())
    api.completeCloudUpload
      .mockRejectedValueOnce(new CloudApiError('capacity', 409, 'capacity_conflict'))
      .mockRejectedValueOnce(new CloudApiError('capacity', 409, 'capacity_conflict'))
      .mockRejectedValueOnce(new CloudApiError('capacity', 409, 'capacity_conflict'))
      .mockResolvedValueOnce(undefined)
    const s3Spy = vi.fn(async () => new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', s3Spy)
    const { result } = renderHook(() => useUploadFlow())
    await chooseFile(result)

    let first!: Promise<void>
    act(() => { first = result.current.submit() })
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000); await first })
    expect(useAppStore.getState().projects[0]).toMatchObject({
      id: IDS.projectId,
      status: 'confirmation_pending',
    })
    expect(result.current.state.submitError).toMatch(/same job/i)

    await act(async () => { await result.current.submit() })
    expect(api.createCloudJob).toHaveBeenCalledTimes(1)
    expect(s3Spy).toHaveBeenCalledTimes(1)
    expect(api.completeCloudUpload).toHaveBeenCalledTimes(4)
    expect(useAppStore.getState().projects[0]?.status).toBe('processing')
  })

  it('an expired first reservation stays hidden while its replacement becomes visible', async () => {
    const replacement = {
      projectId: '33333333-3333-4333-8333-333333333333',
      projectVersion: 1,
      jobId: '44444444-4444-4444-8444-444444444444',
    }
    const completion = deferred<void>()
    api.createCloudJob
      .mockResolvedValueOnce(createResponse(IDS, '2020-01-01T00:00:00Z'))
      .mockResolvedValueOnce(createResponse(replacement))
    api.completeCloudUpload.mockReturnValue(completion.promise)
    vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 204 })))
    const { result } = renderHook(() => useUploadFlow())
    await chooseFile(result)

    const submission = result.current.submit()
    await waitFor(() => expect(useAppStore.getState().projects).toHaveLength(1))
    expect(useAppStore.getState().projects[0]).toMatchObject({
      id: replacement.projectId,
      jobId: replacement.jobId,
      status: 'confirmation_pending',
    })
    expect(useAppStore.getState().projects.find((project) => project.id === IDS.projectId)).toBeUndefined()
    expect(api.createCloudJob).toHaveBeenCalledTimes(2)
    expect(globalThis.fetch).toHaveBeenCalledTimes(1)
    completion.resolve()
    await submission
  })
})
