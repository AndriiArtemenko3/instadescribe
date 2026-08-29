// G7.1 A/B7: staged resumable upload — exactly one create, exactly one
// successful S3 POST (token-free), completion retried for the SAME job;
// an exhausted completion followed by a user retry still creates nothing
// new; stage-truthful copy; double-submit protection.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { clearPortfolioToken, setPortfolioToken } from './portfolioToken'
import {
  CLOUD_MAX_FILE_BYTES,
  CloudUploadSession,
  resolveContentType,
  submitErrorMessage,
  validateCloudUpload,
  type CloudSubmitResult,
} from './cloudUpload'
import { CloudApiError } from './cloudApi'
import { DEFAULT_SETTINGS } from '@/features/upload/hooks/useUploadFlow'

const TOKEN = 'upload-test-token'
const projectId = (n: number) => `00000000-0000-4001-8000-${String(n).padStart(12, '0')}`
const jobId = (n: number) => `00000000-0000-4002-8000-${String(n).padStart(12, '0')}`
const PENDING_PROJECT = '10000000-0000-4001-8000-000000000001'
const PENDING_JOB = '10000000-0000-4002-8000-000000000001'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function makeFile(name = 'clip.mp4', type = 'video/mp4', size = 1024): File {
  const file = new File([new Uint8Array(16)], name, { type })
  Object.defineProperty(file, 'size', { value: size })
  return file
}

function baseInput(overrides: Record<string, unknown> = {}) {
  return {
    file: makeFile(),
    projectName: 'G7 clip',
    settings: { ...DEFAULT_SETTINGS },
    customPrompt: 'Describe gently.',
    durationSecs: 120,
    ...overrides,
  }
}

interface Counters {
  create: number
  s3: number
  complete: number
}

/** Fetch stub with per-endpoint scripts; returns live call counters. */
function stubEndpoints(opts: {
  s3Status?: () => number
  completeStatus?: () => number
  expiresIn?: (createNumber: number) => number
}): Counters {
  const counters: Counters = { create: 0, s3: 0, complete: 0 }
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      const u = String(url)
      const headers = new Headers(init?.headers)
      if (u.endsWith('/api/v1/jobs')) {
        counters.create += 1
        expect(headers.get('X-Portfolio-Token')).toBe(TOKEN)
        const ttl = opts.expiresIn ? opts.expiresIn(counters.create) : 60_000
        return jsonResponse(
          {
            projectId: projectId(counters.create),
            projectVersion: 1,
            jobId: jobId(counters.create),
            upload: {
              url: 'http://localhost:4566/instascribe-media',
              fields: { key: 'uploads/x', policy: 'p' },
              expiresAt: new Date(Date.now() + ttl).toISOString(),
            },
          },
          201,
        )
      }
      if (u.includes('4566')) {
        counters.s3 += 1
        expect(headers.get('X-Portfolio-Token')).toBeNull() // never to S3
        return new Response(null, { status: opts.s3Status?.() ?? 204 })
      }
      counters.complete += 1
      return new Response(JSON.stringify({}), { status: opts.completeStatus?.() ?? 202 })
    }),
  )
  return counters
}

beforeEach(() => setPortfolioToken(TOKEN))
afterEach(() => {
  clearPortfolioToken()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('validateCloudUpload', () => {
  it('accepts the v0.1 contract shape and rejects each violated bound', () => {
    expect(validateCloudUpload(baseInput())).toBeNull()
    expect(
      validateCloudUpload(baseInput({ file: makeFile('c.mp4', 'video/mp4', CLOUD_MAX_FILE_BYTES + 1) })),
    ).toMatch(/250 MiB/)
    expect(validateCloudUpload(baseInput({ durationSecs: 0 }))).toBeTruthy()
    expect(validateCloudUpload(baseInput({ durationSecs: 301 }))).toBeTruthy()
    expect(
      validateCloudUpload(baseInput({ settings: { ...DEFAULT_SETTINGS, model: 'gpt-5.4' } })),
    ).toBeTruthy()
    expect(
      validateCloudUpload(baseInput({ settings: { ...DEFAULT_SETTINGS, fps: 8 } })),
    ).toBeTruthy()
  })

  it('enforces the exact extension/MIME pairs', () => {
    expect(resolveContentType(makeFile('a.mp4', 'video/mp4'))).toBe('video/mp4')
    expect(resolveContentType(makeFile('a.mp4', ''))).toBe('video/mp4') // normalized
    expect(resolveContentType(makeFile('a.webm', 'video/mp4'))).toBeNull() // contradiction
    expect(resolveContentType(makeFile('a.avi', 'video/x-msvideo'))).toBeNull()
  })
})

describe('CloudUploadSession', () => {
  it('publishes IDs while the S3 POST is pending without publishing a visible project', async () => {
    let releaseS3: () => void = () => {}
    const s3Gate = new Promise<void>((resolve) => { releaseS3 = resolve })
    let createdResolve: () => void = () => {}
    const createdGate = new Promise<void>((resolve) => { createdResolve = resolve })
    const created: CloudSubmitResult[] = []
    const visible: CloudSubmitResult[] = []
    vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL) => {
      if (String(url).endsWith('/api/v1/jobs')) {
        return jsonResponse({
          projectId: PENDING_PROJECT,
          projectVersion: 1,
          jobId: PENDING_JOB,
          upload: { url: 'http://localhost:4566/b', fields: { key: 'uploads/pending' }, expiresAt: new Date(Date.now() + 60_000).toISOString() },
        }, 201)
      }
      if (String(url).includes('4566')) {
        await s3Gate
        return new Response(null, { status: 204 })
      }
      return jsonResponse({}, 202)
    }))
    const session = new CloudUploadSession()
    const run = session.run(baseInput(), {
      onCreated: (ids) => { created.push(ids); createdResolve() },
      onUploaded: (ids) => visible.push(ids),
    })
    await createdGate
    expect(session.stage).toBe('created')
    expect(created).toEqual([{ projectId: PENDING_PROJECT, projectVersion: 1, jobId: PENDING_JOB }])
    expect(visible).toEqual([])
    releaseS3()
    await run
    expect(visible).toEqual(created)
  })

  it('S3 failure and abandoned input/unmount never publish a ghost card', async () => {
    const visible: unknown[] = []
    stubEndpoints({ s3Status: () => 500 })
    const failed = new CloudUploadSession()
    await expect(failed.run(baseInput(), { onUploaded: (ids) => visible.push(ids) }))
      .rejects.toBeInstanceOf(CloudApiError)
    expect(visible).toEqual([])

    let releaseS3: () => void = () => {}
    const s3Gate = new Promise<void>((resolve) => { releaseS3 = resolve })
    let createdResolve: () => void = () => {}
    const createdGate = new Promise<void>((resolve) => { createdResolve = resolve })
    stubEndpoints({})
    const originalFetch = fetch
    vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      if (String(url).includes('4566')) {
        await s3Gate
        return new Response(null, { status: 204 })
      }
      return originalFetch(url, init)
    }))
    const abandoned = new CloudUploadSession()
    const run = abandoned.run(baseInput(), {
      onCreated: () => createdResolve(),
      onUploaded: (ids) => visible.push(ids),
    })
    await createdGate
    abandoned.abandon() // exact boundary used by input replacement/cancel/unmount
    releaseS3()
    await expect(run).rejects.toMatchObject({ category: 'conflict' })
    expect(visible).toEqual([])
  })

  it('runs create -> S3 -> complete once each with the exact payload', async () => {
    const counters = stubEndpoints({})
    const session = new CloudUploadSession()
    const result = await session.run(baseInput())
    expect(result).toEqual({ projectId: projectId(1), projectVersion: 1, jobId: jobId(1) })
    expect(counters).toEqual({ create: 1, s3: 1, complete: 1 })
    expect(session.stage).toBe('accepted')

    const fetchSpy = fetch as unknown as ReturnType<typeof vi.fn>
    const payload = JSON.parse(String((fetchSpy.mock.calls[0][1] as RequestInit).body))
    expect(payload.settings.customPrompt).toBe('Describe gently.')
    expect(payload).not.toHaveProperty('mode')
    expect(payload).not.toHaveProperty('provider')
    expect(payload).not.toHaveProperty('pipelineRevision')
    expect(payload.settings).not.toHaveProperty('mode')
  })

  it('retryable completion failures then success: ONE create, ONE upload, repeated completes', async () => {
    let completeAttempt = 0
    const counters = stubEndpoints({
      completeStatus: () => (++completeAttempt < 3 ? 503 : 202),
    })
    const session = new CloudUploadSession({ backoffMs: 1 })
    await session.run(baseInput())
    expect(counters.create).toBe(1)
    expect(counters.s3).toBe(1)
    expect(counters.complete).toBe(3)
  })

  it('an exhausted completion followed by a USER retry causes no second create or upload', async () => {
    const counters = stubEndpoints({ completeStatus: () => 503 })
    const session = new CloudUploadSession({ backoffMs: 1 })
    await expect(session.run(baseInput())).rejects.toBeInstanceOf(CloudApiError)
    expect(session.stage).toBe('completing') // durable IDs already exist
    expect(session.ids).toEqual({ projectId: projectId(1), projectVersion: 1, jobId: jobId(1) })
    const before = { ...counters }

    // The user presses retry: the SAME session resumes at completion only.
    await expect(session.run(baseInput())).rejects.toBeInstanceOf(CloudApiError)
    expect(counters.create).toBe(before.create) // still 1
    expect(counters.s3).toBe(before.s3) // still 1
    expect(counters.complete).toBeGreaterThan(before.complete)
  })

  it('an S3 failure retries the SAME POST while the contract is valid — no new create', async () => {
    let s3Attempt = 0
    const counters = stubEndpoints({ s3Status: () => (++s3Attempt < 2 ? 500 : 204) })
    const session = new CloudUploadSession()
    await expect(session.run(baseInput())).rejects.toBeInstanceOf(CloudApiError)
    expect(session.stage).toBe('created')
    await session.run(baseInput()) // user retry: same job, second POST attempt
    expect(counters.create).toBe(1)
    expect(counters.s3).toBe(2)
    expect(counters.complete).toBe(1)
  })

  it('a contract that lapsed BEFORE any byte triggers exactly ONE recreate', async () => {
    // First create returns an already-expired contract; the second is valid.
    const counters = stubEndpoints({ expiresIn: (n) => (n === 1 ? -1000 : 60_000) })
    const session = new CloudUploadSession()
    const created: string[] = []
    const visible: string[] = []
    const result = await session.run(baseInput(), {
      onCreated: ({ projectId }) => created.push(projectId),
      onUploaded: ({ projectId }) => visible.push(projectId),
    })
    expect(result).toEqual({ projectId: projectId(2), projectVersion: 1, jobId: jobId(2) }) // the FRESH job
    expect(counters).toEqual({ create: 2, s3: 1, complete: 1 })
    expect(session.stage).toBe('accepted')
    expect(session.ids).toEqual({ projectId: projectId(2), projectVersion: 1, jobId: jobId(2) })
    expect(created).toEqual([projectId(1), projectId(2)])
    expect(visible).toEqual([projectId(2)]) // abandoned first reservation stays invisible
  })

  it('two consecutive lapsed contracts fail loudly — no unbounded job creation', async () => {
    const counters = stubEndpoints({ expiresIn: () => -1000 })
    const session = new CloudUploadSession()
    const err = await session.run(baseInput()).catch((e) => e as CloudApiError)
    expect(err).toBeInstanceOf(CloudApiError)
    expect(err.category).not.toBe('validation') // truthful category, not a file complaint
    expect(counters.create).toBe(2) // bounded: exactly one recreate attempt
    expect(counters.s3).toBe(0)
  })

  it('double-submit protection: a concurrent run is rejected without I/O', async () => {
    let release: () => void = () => {}
    const gate = new Promise<void>((r) => (release = r))
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: RequestInfo | URL) => {
        if (String(url).endsWith('/api/v1/jobs')) {
          await gate
          return jsonResponse({
            projectId: projectId(1),
            projectVersion: 1,
            jobId: jobId(1),
            upload: { url: 'http://localhost:4566/b', fields: { key: 'uploads/one' }, expiresAt: new Date(Date.now() + 60_000).toISOString() },
          }, 201)
        }
        if (String(url).includes('4566')) return new Response(null, { status: 204 })
        return new Response(JSON.stringify({}), { status: 202 })
      }),
    )
    const session = new CloudUploadSession()
    const first = session.run(baseInput())
    await expect(session.run(baseInput())).rejects.toMatchObject({ category: 'validation' })
    release()
    await first
  })
})

describe('submitErrorMessage stage truth', () => {
  it('never claims "uploaded" unless the S3 POST completed', () => {
    const err = new CloudApiError('service', 503)
    expect(submitErrorMessage(err, 'created')).toMatch(/transfer to storage failed/i)
    expect(submitErrorMessage(err, 'created')).not.toMatch(/uploaded/i)
    expect(submitErrorMessage(err, 'completing')).toMatch(/uploaded — the final confirmation is pending/i)
    expect(submitErrorMessage(new CloudApiError('conflict', 409), 'completing')).toMatch(
      /can no longer be submitted/i,
    )
    // A dead job must never be described as retryable-for-the-same-job.
    expect(submitErrorMessage(new CloudApiError('not_found', 404), 'completing')).toMatch(
      /no longer exists.*start a new upload/i,
    )
  })
})
