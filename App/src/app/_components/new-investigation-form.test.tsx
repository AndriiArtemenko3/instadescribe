// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { NewInvestigationForm } from './new-investigation-form'

const INVESTIGATION_ID = '11111111-1111-4111-8111-111111111111'
const PROJECT_ID = '22222222-2222-4222-8222-222222222222'
const JOB_ID = '33333333-3333-4333-8333-333333333333'

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function session(role: 'owner' | 'editor' | 'reviewer' | 'viewer') {
  return json({
    user: {
      email: `${role}@example.test`,
      displayName: role,
      organizationId: '44444444-4444-4444-8444-444444444444',
      role,
    },
  })
}

const createdInvestigation = {
  investigationId: INVESTIGATION_ID,
  projectId: PROJECT_ID,
  jobId: JOB_ID,
  name: 'Synthetic source',
  kind: 'geolocateProvenance',
  connectivityPolicy: 'local',
  status: 'awaitingUpload',
  abstained: false,
  calibratedConfidence: null,
  createdAt: '2026-08-30T10:00:00Z',
  updatedAt: '2026-08-30T10:00:00Z',
  traceId: null,
  modelProvenance: {
    modelId: null,
    modelDigest: null,
    promptDigest: null,
    executedLocally: false,
  },
  runtimeProvenance: {
    runtime: null,
    runtimeVersion: null,
    platform: null,
  },
  finalHypothesis: null,
  abstentionReason: null,
  completedAt: null,
}

function installVideoEnvironment(name: { current: string }) {
  vi.stubGlobal('File', window.File)
  const sourceFile = {
    current: new window.File([new Uint8Array([1, 2, 3])], 'source.mp4', {
      type: 'video/mp4',
      lastModified: 1_700_000_000_000,
    }),
  }
  const NativeFormData = window.FormData
  vi.stubGlobal('FormData', class extends NativeFormData {
    constructor(form?: HTMLFormElement) {
      super()
      if (!form) return
      this.set('name', name.current)
      this.set('video', sourceFile.current)
      this.set('kind', 'geolocateProvenance')
      this.set('connectivityPolicy', 'local')
      this.set('legalBasis', 'analystAuthorized')
      this.set('redistributionPolicy', 'metadataOnly')
      this.set('publisherUrl', '')
      this.set('publishedAt', '')
      this.set('license', '')
      this.set('retentionDays', '30')
    }
  })
  const nativeUrl = URL
  vi.stubGlobal('URL', class extends nativeUrl {
    static createObjectURL() { return 'blob:synthetic-source' }
    static revokeObjectURL() {}
  })
  vi.spyOn(document, 'createElement').mockImplementation(((tagName: string, options?: ElementCreationOptions) => {
    const element = Document.prototype.createElement.call(document, tagName, options)
    if (tagName === 'video') {
      Object.defineProperty(element, 'duration', { configurable: true, value: 60 })
      window.setTimeout(() => element.dispatchEvent(new Event('loadedmetadata')), 0)
    }
    return element
  }) as typeof document.createElement)
  vi.spyOn(document, 'cookie', 'get').mockReturnValue(`__Host-instadescribe_csrf=${'a'.repeat(43)}`)
  return sourceFile
}

function idempotencyKey(call: { init?: RequestInit }): string | undefined {
  return (call.init?.headers as Record<string, string> | undefined)?.['Idempotency-Key']
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('new investigation access and local-only boundary', () => {
  it.each(['owner', 'editor'] as const)('shows the local upload form to %s', async (role) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(session(role)))

    render(<NewInvestigationForm />)

    expect(await screen.findByRole('button', { name: 'Create and upload' })).toBeTruthy()
    expect(screen.getByText('Geolocation & provenance')).toBeTruthy()
    expect(screen.getByText('Local only')).toBeTruthy()
    expect(screen.getByText(/Other investigation modes are unavailable\./)).toBeTruthy()
    expect(screen.getByText(/no public-internet retrieval during analysis/)).toBeTruthy()
    expect(screen.getByText(/Authenticated BFF requests and the direct private-storage upload remain transport paths/)).toBeTruthy()
    expect(screen.queryByText(/approved image crops/i)).toBeNull()
    expect(screen.queryByText(/connected tools/i)).toBeNull()
  })

  it.each(['reviewer', 'viewer'] as const)('keeps %s read-only', async (role) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(session(role)))

    render(<NewInvestigationForm />)

    expect(await screen.findByRole('heading', { name: 'Read-only membership' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Create and upload' })).toBeNull()
  })

  it('orchestrates reservation, direct private upload, and completion with the fixed local mode', async () => {
    const sourceFile = installVideoEnvironment({ current: 'Synthetic source' })

    const calls: Array<{ url: string; init?: RequestInit }> = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      calls.push({ url, init })
      if (url === '/api/bff/session') return session('owner')
      if (url === '/api/bff/cloud/investigations') return json({
        investigation: createdInvestigation,
        upload: {
          method: 'POST',
          url: 'https://uploads.example.test/source',
          fields: { key: 'private/source.mp4' },
          expiresAt: '2026-08-30T10:10:00Z',
        },
      })
      if (url === 'https://uploads.example.test/source') return new Response(null, { status: 204 })
      if (url === `/api/bff/cloud/jobs/${JOB_ID}/uploads/complete`) return json({ code: 'fixture_stops_before_navigation' }, 503)
      return json({ code: 'not_found' }, 404)
    }))

    render(<NewInvestigationForm />)
    await screen.findByRole('button', { name: 'Create and upload' })

    fireEvent.change(screen.getByLabelText('Investigation name'), { target: { value: 'Synthetic source' } })
    fireEvent.change(screen.getByLabelText('Source video'), { target: { files: [sourceFile.current] } })
    fireEvent.submit(screen.getByRole('button', { name: 'Create and upload' }).closest('form')!)

    await waitFor(() => expect(calls.some((call) => call.url === `/api/bff/cloud/jobs/${JOB_ID}/uploads/complete`)).toBe(true))

    const reservation = calls.find((call) => call.url === '/api/bff/cloud/investigations')
    expect(reservation?.init?.method).toBe('POST')
    expect(idempotencyKey(reservation!)).toMatch(/^[0-9a-f-]{36}$/)
    expect(JSON.parse(String(reservation?.init?.body))).toMatchObject({
      kind: 'geolocateProvenance',
      connectivityPolicy: 'local',
      video: { durationSeconds: 60, fileName: 'source.mp4' },
    })
    const upload = calls.find((call) => call.url === 'https://uploads.example.test/source')
    expect(upload?.init?.method).toBe('POST')
    expect(upload?.init?.credentials).toBe('omit')
  })

  it('resumes the same reservation and retries completion with one caller-owned key after an ambiguous failure', async () => {
    const sourceFile = installVideoEnvironment({ current: 'Synthetic source' })
    const createKey = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
    const completeKey = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
    vi.spyOn(crypto, 'randomUUID')
      .mockReturnValueOnce(createKey)
      .mockReturnValueOnce(completeKey)

    const calls: Array<{ url: string; init?: RequestInit }> = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      calls.push({ url, init })
      if (url === '/api/bff/session') return session('owner')
      if (url === '/api/bff/cloud/investigations') return json({
        investigation: createdInvestigation,
        upload: {
          method: 'POST',
          url: 'https://uploads.example.test/source',
          fields: { key: 'private/source.mp4' },
          expiresAt: '2026-08-30T10:10:00Z',
        },
      })
      if (url === 'https://uploads.example.test/source') return new Response(null, { status: 204 })
      if (url === `/api/bff/cloud/jobs/${JOB_ID}/uploads/complete`) {
        return json({ code: 'temporarily_unavailable' }, 503)
      }
      return json({ code: 'not_found' }, 404)
    }))

    render(<NewInvestigationForm />)
    await screen.findByRole('button', { name: 'Create and upload' })
    fireEvent.change(screen.getByLabelText('Source video'), { target: { files: [sourceFile.current] } })
    const form = screen.getByRole('button', { name: 'Create and upload' }).closest('form')!

    fireEvent.submit(form)
    await waitFor(() => expect(calls.filter((call) => call.url.endsWith('/uploads/complete'))).toHaveLength(1))
    await waitFor(() => expect((screen.getByRole('button', { name: 'Create and upload' }) as HTMLButtonElement).disabled).toBe(false))
    fireEvent.submit(form)
    await waitFor(() => expect(calls.filter((call) => call.url.endsWith('/uploads/complete'))).toHaveLength(2))

    const reservations = calls.filter((call) => call.url === '/api/bff/cloud/investigations')
    const uploads = calls.filter((call) => call.url === 'https://uploads.example.test/source')
    const completions = calls.filter((call) => call.url.endsWith('/uploads/complete'))
    expect(reservations).toHaveLength(1)
    expect(uploads).toHaveLength(1)
    expect(completions).toHaveLength(2)
    expect(idempotencyKey(reservations[0]!)).toBe(createKey)
    expect(idempotencyKey(completions[0]!)).toBe(completeKey)
    expect(idempotencyKey(completions[1]!)).toBe(completeKey)
    expect(completions.every((call) => call.url === `/api/bff/cloud/jobs/${JOB_ID}/uploads/complete`)).toBe(true)
  })

  it('keeps the reservation across a failed storage response and retries only unfinished stages', async () => {
    const sourceFile = installVideoEnvironment({ current: 'Synthetic source' })
    const createKey = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
    const completeKey = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
    vi.spyOn(crypto, 'randomUUID')
      .mockReturnValueOnce(createKey)
      .mockReturnValueOnce(completeKey)

    let uploadCalls = 0
    const calls: Array<{ url: string; init?: RequestInit }> = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      calls.push({ url, init })
      if (url === '/api/bff/session') return session('owner')
      if (url === '/api/bff/cloud/investigations') return json({
        investigation: createdInvestigation,
        upload: {
          method: 'POST',
          url: 'https://uploads.example.test/source',
          fields: { key: 'private/source.mp4' },
          expiresAt: '2026-08-30T10:10:00Z',
        },
      })
      if (url === 'https://uploads.example.test/source') {
        uploadCalls += 1
        return new Response(null, { status: uploadCalls === 1 ? 503 : 204 })
      }
      if (url === `/api/bff/cloud/jobs/${JOB_ID}/uploads/complete`) {
        return json({ code: 'temporarily_unavailable' }, 503)
      }
      return json({ code: 'not_found' }, 404)
    }))

    render(<NewInvestigationForm />)
    await screen.findByRole('button', { name: 'Create and upload' })
    fireEvent.change(screen.getByLabelText('Source video'), { target: { files: [sourceFile.current] } })
    const form = screen.getByRole('button', { name: 'Create and upload' }).closest('form')!

    fireEvent.submit(form)
    await waitFor(() => expect(calls.filter((call) => call.url === 'https://uploads.example.test/source')).toHaveLength(1))
    await waitFor(() => expect((screen.getByRole('button', { name: 'Create and upload' }) as HTMLButtonElement).disabled).toBe(false))
    fireEvent.submit(form)
    await waitFor(() => expect(calls.filter((call) => call.url.endsWith('/uploads/complete'))).toHaveLength(1))
    await waitFor(() => expect((screen.getByRole('button', { name: 'Create and upload' }) as HTMLButtonElement).disabled).toBe(false))
    fireEvent.submit(form)
    await waitFor(() => expect(calls.filter((call) => call.url.endsWith('/uploads/complete'))).toHaveLength(2))

    const reservations = calls.filter((call) => call.url === '/api/bff/cloud/investigations')
    const uploads = calls.filter((call) => call.url === 'https://uploads.example.test/source')
    const completions = calls.filter((call) => call.url.endsWith('/uploads/complete'))
    expect(reservations).toHaveLength(1)
    expect(uploads).toHaveLength(2)
    expect(completions).toHaveLength(2)
    expect(idempotencyKey(reservations[0]!)).toBe(createKey)
    expect(idempotencyKey(completions[0]!)).toBe(completeKey)
    expect(idempotencyKey(completions[1]!)).toBe(completeKey)
  })

  it('starts a fresh reservation when the selected File object changes despite identical metadata', async () => {
    const sourceFile = installVideoEnvironment({ current: 'Synthetic source' })
    const generatedKeys = [
      'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
      'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
      'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
    ]
    vi.spyOn(crypto, 'randomUUID')
      .mockReturnValueOnce(generatedKeys[0]!)
      .mockReturnValueOnce(generatedKeys[1]!)
      .mockReturnValueOnce(generatedKeys[2]!)
      .mockReturnValueOnce(generatedKeys[3]!)

    const calls: Array<{ url: string; init?: RequestInit }> = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      calls.push({ url, init })
      if (url === '/api/bff/session') return session('owner')
      if (url === '/api/bff/cloud/investigations') return json({
        investigation: createdInvestigation,
        upload: {
          method: 'POST',
          url: 'https://uploads.example.test/source',
          fields: { key: 'private/source.mp4' },
          expiresAt: '2026-08-30T10:10:00Z',
        },
      })
      if (url === 'https://uploads.example.test/source') return new Response(null, { status: 204 })
      if (url === `/api/bff/cloud/jobs/${JOB_ID}/uploads/complete`) {
        return json({ code: 'temporarily_unavailable' }, 503)
      }
      return json({ code: 'not_found' }, 404)
    }))

    render(<NewInvestigationForm />)
    await screen.findByRole('button', { name: 'Create and upload' })
    const videoInput = screen.getByLabelText('Source video')
    fireEvent.change(videoInput, { target: { files: [sourceFile.current] } })
    const form = screen.getByRole('button', { name: 'Create and upload' }).closest('form')!
    fireEvent.submit(form)
    await waitFor(() => expect(calls.filter((call) => call.url.endsWith('/uploads/complete'))).toHaveLength(1))

    sourceFile.current = new window.File([new Uint8Array([9, 8, 7])], 'source.mp4', {
      type: 'video/mp4',
      lastModified: 1_700_000_000_000,
    })
    fireEvent.change(videoInput, { target: { files: [sourceFile.current] } })
    await waitFor(() => expect((screen.getByRole('button', { name: 'Create and upload' }) as HTMLButtonElement).disabled).toBe(false))
    fireEvent.submit(form)
    await waitFor(() => expect(calls.filter((call) => call.url === '/api/bff/cloud/investigations')).toHaveLength(2))

    const reservations = calls.filter((call) => call.url === '/api/bff/cloud/investigations')
    expect(calls.filter((call) => call.url === 'https://uploads.example.test/source')).toHaveLength(2)
    expect(idempotencyKey(reservations[0]!)).toBe(generatedKeys[0])
    expect(idempotencyKey(reservations[1]!)).toBe(generatedKeys[2])
  })

  it('reuses an idempotency key for an ambiguous create retry and rotates it after material input changes', async () => {
    const submittedName = { current: 'Synthetic source' }
    const sourceFile = installVideoEnvironment(submittedName)
    const generatedKeys = [
      'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
    ]
    vi.spyOn(crypto, 'randomUUID')
      .mockReturnValueOnce(generatedKeys[0])
      .mockReturnValueOnce(generatedKeys[1])

    const calls: Array<{ url: string; init?: RequestInit }> = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      calls.push({ url, init })
      if (url === '/api/bff/session') return session('owner')
      if (url === '/api/bff/cloud/investigations') return json({ code: 'temporarily_unavailable' }, 503)
      return json({ code: 'not_found' }, 404)
    }))

    render(<NewInvestigationForm />)
    await screen.findByRole('button', { name: 'Create and upload' })
    fireEvent.change(screen.getByLabelText('Source video'), { target: { files: [sourceFile.current] } })
    const form = screen.getByRole('button', { name: 'Create and upload' }).closest('form')!

    fireEvent.submit(form)
    await waitFor(() => expect(calls.filter((call) => call.url === '/api/bff/cloud/investigations')).toHaveLength(1))
    await waitFor(() => expect((screen.getByRole('button', { name: 'Create and upload' }) as HTMLButtonElement).disabled).toBe(false))
    fireEvent.submit(form)
    await waitFor(() => expect(calls.filter((call) => call.url === '/api/bff/cloud/investigations')).toHaveLength(2))

    submittedName.current = 'Changed synthetic source'
    await waitFor(() => expect((screen.getByRole('button', { name: 'Create and upload' }) as HTMLButtonElement).disabled).toBe(false))
    fireEvent.submit(form)
    await waitFor(() => expect(calls.filter((call) => call.url === '/api/bff/cloud/investigations')).toHaveLength(3))

    const reservations = calls.filter((call) => call.url === '/api/bff/cloud/investigations')
    expect(idempotencyKey(reservations[0]!)).toBe(generatedKeys[0])
    expect(idempotencyKey(reservations[1]!)).toBe(generatedKeys[0])
    expect(idempotencyKey(reservations[2]!)).toBe(generatedKeys[1])
  })
})
