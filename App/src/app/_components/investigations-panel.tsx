'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { Activity, ArrowRight, FileSearch, LockKeyhole, MapPin, Plus } from 'lucide-react'
import {
  canCreateInvestigation,
  InvestigationApiError,
  listInvestigations,
  loadBrowserSession,
  type BrowserRole,
  type InvestigationSummary,
} from '@/lib/investigations'

type PageState =
  | { kind: 'loading' }
  | { kind: 'ready'; investigations: InvestigationSummary[]; role: BrowserRole }
  | { kind: 'signedOut' }
  | { kind: 'unavailable'; message: string }

const statusLabels: Record<InvestigationSummary['status'], string> = {
  awaitingUpload: 'Awaiting upload',
  queued: 'Queued',
  preprocessing: 'Preprocessing',
  investigating: 'Investigating',
  needsReview: 'Needs review',
  completed: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
}

const kindLabels: Record<InvestigationSummary['kind'], string> = {
  geolocateProvenance: 'Geolocation & provenance',
  damageChange: 'Unavailable workflow',
}

const policyLabels: Record<InvestigationSummary['connectivityPolicy'], string> = {
  local: 'Local only',
  textOnly: 'Unavailable mode',
  approvedCrops: 'Unavailable mode',
  connected: 'Unavailable mode',
}

export function InvestigationsPanel() {
  const [state, setState] = useState<PageState>({ kind: 'loading' })

  useEffect(() => {
    const controller = new AbortController()
    Promise.all([
      listInvestigations(controller.signal),
      loadBrowserSession(controller.signal),
    ]).then(([investigations, user]) => {
      setState({ kind: 'ready', investigations, role: user.role })
    }).catch((error: unknown) => {
      if (error instanceof DOMException && error.name === 'AbortError') return
      if (error instanceof InvestigationApiError && error.status === 401) {
        setState({ kind: 'signedOut' })
        return
      }
      setState({
        kind: 'unavailable',
        message: error instanceof InvestigationApiError && error.code === 'invalid_response'
          ? 'The investigation API returned data that did not match the trusted browser contract.'
          : 'The investigation service is temporarily unavailable.',
      })
    })
    return () => controller.abort()
  }, [])

  if (state.kind === 'loading') {
    return <div className="mt-7 h-56 animate-pulse rounded-xl border border-neutral-200 bg-white" aria-label="Loading investigations" />
  }

  if (state.kind === 'signedOut') {
    return (
      <section className="mt-7 rounded-xl border border-neutral-200 bg-white p-10 text-center shadow-card">
        <LockKeyhole className="mx-auto h-6 w-6 text-neutral-400" aria-hidden="true" />
        <h2 className="mt-4 text-lg font-semibold text-neutral-900">Sign in to access investigations</h2>
        <Link href="/login?returnTo=%2Finvestigations" className="mt-5 inline-flex rounded-lg bg-brand-400 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500">
          Sign in
        </Link>
      </section>
    )
  }

  if (state.kind === 'unavailable') {
    return (
      <section className="mt-7 rounded-xl border border-warning-200 bg-warning-50 p-6" role="alert">
        <h2 className="font-semibold text-neutral-900">Investigation data unavailable</h2>
        <p className="mt-2 text-sm text-neutral-600">{state.message}</p>
      </section>
    )
  }

  const mayCreate = canCreateInvestigation(state.role)
  return (
    <>
      <div className="flex flex-col gap-5 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-widest text-brand-500">Analyst workspace</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight text-neutral-900">Investigations</h1>
          <p className="mt-2 max-w-2xl text-sm text-neutral-500">
            Evidence-backed hypotheses, visible uncertainty, and an auditable tool trace.
          </p>
        </div>
        {mayCreate ? (
          <Link href="/investigations/new" className="inline-flex items-center justify-center gap-2 rounded-lg bg-brand-400 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500">
            <Plus className="h-4 w-4" aria-hidden="true" />
            New investigation
          </Link>
        ) : (
          <span className="rounded-lg border border-neutral-200 bg-white px-4 py-2 text-xs text-neutral-500">
            Read-only access
          </span>
        )}
      </div>

      {state.investigations.length === 0 ? (
        <section className="mt-7 rounded-xl border border-dashed border-neutral-300 bg-white p-12 text-center">
          <FileSearch className="mx-auto h-7 w-7 text-neutral-400" aria-hidden="true" />
          <h2 className="mt-4 font-semibold text-neutral-900">No investigations yet</h2>
          <p className="mt-2 text-sm text-neutral-500">
            Only authorised public or licensed footage should be submitted.
          </p>
          {mayCreate && (
            <Link href="/investigations/new" className="mt-5 inline-flex text-sm font-medium text-brand-500 hover:text-brand-600">
              Start the first investigation <ArrowRight className="ml-1 h-4 w-4" aria-hidden="true" />
            </Link>
          )}
        </section>
      ) : (
        <ul className="mt-7 grid gap-4 lg:grid-cols-2">
          {state.investigations.map((investigation) => (
            <li key={investigation.investigationId}>
              <Link
                href={`/investigations/${encodeURIComponent(investigation.investigationId)}`}
                className="group block rounded-xl border border-neutral-200 bg-white p-5 shadow-card transition hover:border-neutral-300"
              >
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <p className="text-xs font-medium uppercase tracking-wide text-neutral-400">{kindLabels[investigation.kind]}</p>
                    <h2 className="mt-2 text-lg font-semibold text-neutral-900 group-hover:text-brand-600">{investigation.name}</h2>
                  </div>
                  <span className="rounded-full bg-neutral-100 px-2.5 py-1 text-xs font-medium text-neutral-600">
                    {statusLabels[investigation.status]}
                  </span>
                </div>
                <div className="mt-5 grid grid-cols-2 gap-3 border-t border-neutral-100 pt-4 text-xs text-neutral-500 sm:grid-cols-3">
                  <span className="flex items-center gap-1.5"><LockKeyhole className="h-3.5 w-3.5" />{policyLabels[investigation.connectivityPolicy]}</span>
                  <span className="flex items-center gap-1.5"><MapPin className="h-3.5 w-3.5" />{investigation.abstained ? 'Abstained' : 'Hypothesis open'}</span>
                  <span className="flex items-center gap-1.5"><Activity className="h-3.5 w-3.5" />{investigation.calibratedConfidence === null ? 'Calibration pending' : `${Math.round(investigation.calibratedConfidence * 100)}% calibrated confidence`}</span>
                </div>
                <p className="mt-4 text-xs text-neutral-400">Updated {new Date(investigation.updatedAt).toLocaleString()}</p>
              </Link>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-8 border-t border-neutral-200 pt-5">
        <Link href="/legacy/audio-description" className="text-xs font-medium text-neutral-500 hover:text-neutral-800">
          Open legacy audio-description workspace →
        </Link>
      </div>
    </>
  )
}
