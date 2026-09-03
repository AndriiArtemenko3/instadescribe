'use client'

import { useEffect, useRef, useState, type FormEvent } from 'react'
import Link from 'next/link'
import { CheckCircle2, LockKeyhole, UploadCloud } from 'lucide-react'
import { BrowserIntegrationError, completeBrowserUpload, uploadBrowserFile } from '@/lib/browserIntegration'
import {
  canCreateInvestigation,
  createInvestigation,
  InvestigationApiError,
  loadBrowserSession,
  type BrowserRole,
  type ConnectivityPolicy,
  type CreateInvestigationInput,
  type CreateInvestigationResult,
  type InvestigationKind,
} from '@/lib/investigations'

const MAX_VIDEO_BYTES = 1024 * 1024 * 1024
const MIN_VIDEO_SECONDS = 30
const MAX_VIDEO_SECONDS = 180
const VIDEO_TYPES: Record<string, CreateVideoType> = {
  mp4: 'video/mp4',
  mov: 'video/quicktime',
  webm: 'video/webm',
}
type CreateVideoType = 'video/mp4' | 'video/quicktime' | 'video/webm'
type AccessState = 'loading' | 'signedOut' | 'unavailable' | BrowserRole
type SubmissionAttempt = {
  fingerprint: string
  sourceFile: File
  createKey: string
  completeKey: string | null
  created: CreateInvestigationResult | null
  uploadConfirmed: boolean
}

function extension(fileName: string): string {
  return fileName.split('.').pop()?.toLowerCase() ?? ''
}

function readVideoDuration(file: File): Promise<number> {
  return new Promise((resolve, reject) => {
    const objectUrl = URL.createObjectURL(file)
    const video = document.createElement('video')
    let timeout = 0
    const finish = (error?: Error) => {
      window.clearTimeout(timeout)
      video.onloadedmetadata = null
      video.onerror = null
      video.removeAttribute('src')
      URL.revokeObjectURL(objectUrl)
      if (error) reject(error)
    }
    timeout = window.setTimeout(() => finish(new Error('metadata timeout')), 15_000)
    video.preload = 'metadata'
    video.onloadedmetadata = () => {
      const duration = video.duration
      if (!Number.isFinite(duration) || duration <= 0) {
        finish(new Error('invalid duration'))
        return
      }
      finish()
      resolve(duration)
    }
    video.onerror = () => finish(new Error('unreadable metadata'))
    video.src = objectUrl
  })
}

export function NewInvestigationForm() {
  const [access, setAccess] = useState<AccessState>('loading')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const submissionAttempt = useRef<SubmissionAttempt | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    loadBrowserSession(controller.signal).then((user) => {
      setAccess(user.role)
    }).catch((error: unknown) => {
      if (error instanceof DOMException && error.name === 'AbortError') return
      setAccess(error instanceof InvestigationApiError && error.status === 401 ? 'signedOut' : 'unavailable')
    })
    return () => controller.abort()
  }, [])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy || !['owner', 'editor'].includes(access)) return
    const form = event.currentTarget
    const data = new FormData(form)
    const videoControl = form.elements.namedItem('video')
    const video = videoControl instanceof HTMLInputElement ? videoControl.files?.[0] ?? null : null
    const name = String(data.get('name') ?? '').trim()
    const kind = String(data.get('kind')) as InvestigationKind
    const connectivityPolicy = String(data.get('connectivityPolicy')) as ConnectivityPolicy
    const legalBasis = String(data.get('legalBasis')) as 'publicDomain' | 'licensed' | 'consent' | 'analystAuthorized'
    const redistributionPolicy = String(data.get('redistributionPolicy')) as 'prohibited' | 'metadataOnly' | 'permitted'
    const publisherUrl = String(data.get('publisherUrl') ?? '').trim()
    const publishedAtInput = String(data.get('publishedAt') ?? '').trim()
    const license = String(data.get('license') ?? '').trim()
    const retentionDays = Number(data.get('retentionDays'))

    if (!name || !(video instanceof File) || video.size < 1) {
      setMessage('Enter a name and choose a source video.')
      return
    }
    if (kind !== 'geolocateProvenance' || connectivityPolicy !== 'local') {
      setMessage('This milestone accepts local geolocation/provenance investigations only.')
      return
    }
    const contentType = VIDEO_TYPES[extension(video.name)]
    if (!contentType || video.size > MAX_VIDEO_BYTES) {
      setMessage('Video must be MP4, MOV or WebM and no larger than 1 GiB.')
      return
    }
    if (publisherUrl) {
      try {
        const url = new URL(publisherUrl)
        if (
          url.protocol !== 'https:' || url.username || url.password || url.hash ||
          publisherUrl.split('').some((character) => /\s/.test(character))
        ) throw new Error('Safe HTTPS URL required')
      } catch {
        setMessage('Publisher URL must be a valid HTTPS URL.')
        return
      }
    }
    if (legalBasis === 'licensed' && !license) {
      setMessage('Record the licence when licensed material is selected.')
      return
    }
    if (!Number.isInteger(retentionDays) || retentionDays < 1 || retentionDays > 30) {
      setMessage('Lifecycle eligibility must be between 1 and 30 days.')
      return
    }
    let publishedAt: string | undefined
    if (publishedAtInput) {
      const parsed = new Date(publishedAtInput)
      if (!Number.isFinite(parsed.getTime())) {
        setMessage('Published at must be a valid date and time.')
        return
      }
      publishedAt = parsed.toISOString()
    }

    setBusy(true)
    setMessage('Reading source duration locally…')
    let durationSeconds: number
    try {
      durationSeconds = await readVideoDuration(video)
    } catch {
      setMessage('The browser could not read this video’s duration. Choose a valid MP4, MOV or WebM file.')
      setBusy(false)
      return
    }
    if (durationSeconds < MIN_VIDEO_SECONDS || durationSeconds > MAX_VIDEO_SECONDS) {
      setMessage('The month-one investigation workflow accepts videos from 30 seconds to 3 minutes.')
      setBusy(false)
      return
    }

    setMessage('Reserving a private upload…')
    const input: CreateInvestigationInput = {
      name,
      kind,
      connectivityPolicy,
      video: {
        fileName: video.name,
        contentType,
        sizeBytes: video.size,
        durationSeconds: Number(durationSeconds.toFixed(3)),
      },
      source: {
        ...(publisherUrl ? { publisherUrl } : {}),
        ...(publishedAt ? { publishedAt } : {}),
        legalBasis,
        ...(license ? { license } : {}),
        redistributionPolicy,
        retentionDays,
      },
    }
    const fingerprint = JSON.stringify(input)
    const attempt = submissionAttempt.current?.fingerprint === fingerprint && submissionAttempt.current.sourceFile === video
      ? submissionAttempt.current
      : {
          fingerprint,
          sourceFile: video,
          createKey: crypto.randomUUID(),
          completeKey: null,
          created: null,
          uploadConfirmed: false,
        }
    submissionAttempt.current = attempt
    try {
      if (attempt.created === null) {
        attempt.created = await createInvestigation(input, attempt.createKey)
        attempt.completeKey = crypto.randomUUID()
      }
      const created = attempt.created
      if (!attempt.uploadConfirmed) {
        setMessage('Uploading directly to private object storage…')
        await uploadBrowserFile(created.upload, video)
        attempt.uploadConfirmed = true
      }
      setMessage('Verifying the source and queueing local preprocessing…')
      await completeBrowserUpload(created.investigation.jobId, attempt.completeKey!)
      submissionAttempt.current = null
      window.location.assign(`/investigations/${encodeURIComponent(created.investigation.investigationId)}`)
    } catch (error) {
      const code = error instanceof InvestigationApiError || error instanceof BrowserIntegrationError
        ? error.code
        : 'upload_failed'
      const investigationId = attempt.created?.investigation.investigationId ?? null
      setMessage(code === 'investigation_mode_unavailable'
        ? 'This investigation mode is not available in the current milestone.'
        : investigationId
        ? `The investigation was reserved but could not be queued (${code}). Investigation ID: ${investigationId}.`
        : `The investigation could not be created (${code}).`)
      setBusy(false)
    }
  }

  if (access === 'loading') {
    return <div className="mt-7 h-96 animate-pulse rounded-xl border border-neutral-200 bg-white" aria-label="Checking investigation access" />
  }

  if (access === 'signedOut') {
    return (
      <section className="mt-7 rounded-xl border border-neutral-200 bg-white p-8 text-center">
        <LockKeyhole className="mx-auto h-6 w-6 text-neutral-400" />
        <p className="mt-4 text-sm text-neutral-600">Sign in before creating an investigation.</p>
        <Link href="/login?returnTo=%2Finvestigations%2Fnew" className="mt-5 inline-flex rounded-lg bg-brand-400 px-4 py-2 text-sm font-medium text-white">Sign in</Link>
      </section>
    )
  }

  if (access === 'unavailable') {
    return <p className="mt-7 rounded-xl border border-warning-200 bg-warning-50 p-6 text-sm text-neutral-700" role="alert">Session verification is temporarily unavailable.</p>
  }

  if (!canCreateInvestigation(access)) {
    return (
      <section className="mt-7 rounded-xl border border-neutral-200 bg-white p-8">
        <h2 className="font-semibold text-neutral-900">Read-only membership</h2>
        <p className="mt-2 text-sm text-neutral-500">Only Owners and Editors can submit source media.</p>
        <Link href="/investigations" className="mt-5 inline-flex text-sm font-medium text-brand-500">Back to investigations</Link>
      </section>
    )
  }

  return (
    <form onSubmit={submit} className="mt-7 grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
      <div className="space-y-5 rounded-xl border border-neutral-200 bg-white p-6 shadow-card">
        <div>
          <h2 className="font-semibold text-neutral-900">Investigation and source</h2>
          <p className="mt-1 text-xs text-neutral-500">The original video stays outside Next and uploads directly to private storage.</p>
        </div>
        <label className="block text-sm font-medium text-neutral-700">
          Investigation name
          <input name="name" required maxLength={200} disabled={busy} className="mt-2 w-full rounded-lg border border-neutral-300 px-3 py-2 text-neutral-900" />
        </label>
        <label className="block text-sm font-medium text-neutral-700">
          Source video
          <input name="video" type="file" required accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm" disabled={busy} className="mt-2 block w-full rounded-lg border border-dashed border-neutral-300 p-4 text-sm font-normal" />
        </label>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="rounded-lg border border-neutral-200 bg-neutral-50 px-3 py-2">
            <p className="text-xs text-neutral-400">Workflow</p>
            <p className="mt-1 text-sm font-medium text-neutral-700">Geolocation &amp; provenance</p>
            <input name="kind" type="hidden" value="geolocateProvenance" />
          </div>
          <div className="rounded-lg border border-neutral-200 bg-neutral-50 px-3 py-2">
            <p className="text-xs text-neutral-400">Connectivity policy</p>
            <p className="mt-1 text-sm font-medium text-neutral-700">Local only</p>
            <input name="connectivityPolicy" type="hidden" value="local" />
          </div>
        </div>
        <p className="rounded-lg border border-neutral-200 bg-neutral-50 px-3 py-2 text-xs leading-5 text-neutral-500">
          This implementation milestone accepts local geolocation/provenance runs only. Other investigation modes are unavailable.
        </p>
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="text-sm font-medium text-neutral-700">
            Publisher URL <span className="font-normal text-neutral-400">(optional HTTPS)</span>
            <input name="publisherUrl" type="url" inputMode="url" maxLength={2048} disabled={busy} className="mt-2 w-full rounded-lg border border-neutral-300 px-3 py-2 font-normal" />
          </label>
          <label className="text-sm font-medium text-neutral-700">
            Published at <span className="font-normal text-neutral-400">(optional)</span>
            <input name="publishedAt" type="datetime-local" disabled={busy} className="mt-2 w-full rounded-lg border border-neutral-300 px-3 py-2 font-normal" />
          </label>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="text-sm font-medium text-neutral-700">
            Legal basis
            <select name="legalBasis" defaultValue="analystAuthorized" disabled={busy} className="mt-2 w-full rounded-lg border border-neutral-300 px-3 py-2 font-normal">
              <option value="analystAuthorized">Analyst authorised</option>
              <option value="publicDomain">Public domain</option>
              <option value="licensed">Licensed</option>
              <option value="consent">Consent</option>
            </select>
          </label>
          <label className="text-sm font-medium text-neutral-700">
            Licence <span className="font-normal text-neutral-400">(if applicable)</span>
            <input name="license" maxLength={200} disabled={busy} className="mt-2 w-full rounded-lg border border-neutral-300 px-3 py-2 font-normal" />
          </label>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="text-sm font-medium text-neutral-700">
            Redistribution
            <select name="redistributionPolicy" defaultValue="metadataOnly" disabled={busy} className="mt-2 w-full rounded-lg border border-neutral-300 px-3 py-2 font-normal">
              <option value="prohibited">Prohibited</option>
              <option value="metadataOnly">Metadata only</option>
              <option value="permitted">Permitted</option>
            </select>
          </label>
          <label className="text-sm font-medium text-neutral-700">
            Source lifecycle eligibility (days)
            <input name="retentionDays" type="number" min={1} max={30} defaultValue={30} disabled={busy} className="mt-2 w-full rounded-lg border border-neutral-300 px-3 py-2 font-normal" />
            <span className="mt-1 block text-xs font-normal leading-5 text-neutral-400">The tier controls when S3 may act; physical removal of every object version can occur later.</span>
          </label>
        </div>
        {message && <p role="status" className="rounded-lg bg-neutral-50 px-3 py-2 text-sm text-neutral-600">{message}</p>}
        <button type="submit" disabled={busy} className="inline-flex items-center gap-2 rounded-lg bg-brand-400 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500 disabled:cursor-not-allowed disabled:opacity-50">
          <UploadCloud className="h-4 w-4" />
          {busy ? 'Creating investigation…' : 'Create and upload'}
        </button>
      </div>

      <aside className="h-fit rounded-xl border border-neutral-200 bg-neutral-950 p-5 text-neutral-100">
        <p className="text-xs font-semibold uppercase tracking-widest text-brand-200">Submission boundary</p>
        <ul className="mt-5 space-y-4 text-sm text-neutral-300">
          <li className="flex gap-3"><CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-brand-200" />Use public, licensed, consented or explicitly authorised media.</li>
          <li className="flex gap-3"><CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-brand-200" />No face recognition, live tracking or operational targeting.</li>
          <li className="flex gap-3"><CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-brand-200" />Local mode means no public-internet retrieval during analysis. Authenticated BFF requests and the direct private-storage upload remain transport paths.</li>
        </ul>
      </aside>
    </form>
  )
}
