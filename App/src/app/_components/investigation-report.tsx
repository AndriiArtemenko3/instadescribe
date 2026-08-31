'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { AlertCircle, CheckCircle2, FileCheck2, MapPin, Printer, ShieldCheck, XCircle } from 'lucide-react'
import {
  InvestigationApiError,
  loadInvestigationReport,
  type EvidenceItem,
  type InvestigationReport as InvestigationReportData,
} from '@/lib/investigations'

type ReportState =
  | { kind: 'loading' }
  | { kind: 'ready'; report: InvestigationReportData }
  | { kind: 'signedOut' }
  | { kind: 'notFound' }
  | { kind: 'unavailable'; message: string }

const verificationLabels: Record<EvidenceItem['verificationState'], string> = {
  proposed: 'Proposed observation',
  verified: 'Verified by tool',
  rejected: 'Rejected by verifier',
}

const verificationClasses: Record<EvidenceItem['verificationState'], string> = {
  proposed: 'bg-warning-50 text-warning-800',
  verified: 'bg-success-50 text-success-800',
  rejected: 'bg-danger-50 text-danger-800',
}

function VerificationBadge({ state }: { state: EvidenceItem['verificationState'] }) {
  return (
    <span className={`mt-2 inline-flex rounded-full px-2 py-1 text-[0.65rem] font-semibold ${verificationClasses[state]}`}>
      {verificationLabels[state]}
    </span>
  )
}

export function InvestigationReport({ investigationId }: { investigationId: string }) {
  const [state, setState] = useState<ReportState>({ kind: 'loading' })

  useEffect(() => {
    const controller = new AbortController()
    loadInvestigationReport(investigationId, controller.signal).then((report) => {
      setState({ kind: 'ready', report })
    }).catch((error: unknown) => {
      if (error instanceof DOMException && error.name === 'AbortError') return
      if (error instanceof InvestigationApiError && error.status === 401) setState({ kind: 'signedOut' })
      else if (error instanceof InvestigationApiError && error.status === 404) setState({ kind: 'notFound' })
      else setState({
        kind: 'unavailable',
        message: error instanceof InvestigationApiError && error.code === 'invalid_response'
          ? 'The report did not match the trusted browser contract.'
          : 'The report service is temporarily unavailable.',
      })
    })
    return () => controller.abort()
  }, [investigationId])

  if (state.kind === 'loading') {
    return <div className="h-[34rem] animate-pulse rounded-xl border border-neutral-200 bg-white" aria-label="Loading investigation report" />
  }
  if (state.kind === 'signedOut') {
    return (
      <section className="rounded-xl border border-neutral-200 bg-white p-10 text-center">
        <h1 className="text-xl font-semibold text-neutral-900">Sign in to view this report</h1>
        <Link href={`/login?returnTo=${encodeURIComponent(`/investigations/${investigationId}/report`)}`} className="mt-5 inline-flex rounded-lg bg-brand-400 px-4 py-2 text-sm font-medium text-white">Sign in</Link>
      </section>
    )
  }
  if (state.kind === 'notFound') {
    return (
      <section className="rounded-xl border border-neutral-200 bg-white p-10 text-center">
        <h1 className="text-xl font-semibold text-neutral-900">Report not found</h1>
        <p className="mt-2 text-sm text-neutral-500">Missing and cross-organisation identifiers return the same result.</p>
        <Link href="/investigations" className="mt-5 inline-flex text-sm font-medium text-brand-500">Back to investigations</Link>
      </section>
    )
  }
  if (state.kind === 'unavailable') {
    return <p className="rounded-xl border border-warning-200 bg-warning-50 p-6 text-sm text-neutral-700" role="alert">{state.message}</p>
  }

  const { investigation, source, decision, evidence, latestBelief } = state.report
  const decisionByEvidence = new Map(decision?.evidenceDecisions.map((item) => [item.evidenceId, item.decision]) ?? [])
  const accepted = evidence.filter((item) => decisionByEvidence.get(item.evidenceId) === 'accepted')
  const rejected = evidence.filter((item) => decisionByEvidence.get(item.evidenceId) === 'rejected')
  const executionBoundary = investigation.modelProvenance.modelId === 'deterministic-fixture'
    ? 'Deterministic fixture · no model inference'
    : investigation.modelProvenance.executedLocally
    ? 'Local execution recorded'
    : ['awaitingUpload', 'queued', 'preprocessing', 'investigating'].includes(investigation.status)
    ? 'Local execution configured · pending'
    : 'No completed local execution recorded'

  return (
    <article className="space-y-5">
      <header className="rounded-xl border border-neutral-200 bg-white p-6 shadow-card">
        <div className="flex flex-col gap-5 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <p className="text-xs font-semibold uppercase tracking-widest text-brand-500">Evidence-backed report</p>
            <h1 className="mt-2 text-3xl font-semibold tracking-tight text-neutral-900">{investigation.name}</h1>
            <p className="mt-2 text-sm text-neutral-500">Investigation {investigation.investigationId}</p>
          </div>
          <div className="flex flex-wrap gap-2 print:hidden">
            <Link href={`/investigations/${investigationId}`} className="rounded-lg border border-neutral-300 bg-white px-4 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50">Open workspace</Link>
            <button type="button" onClick={() => window.print()} className="inline-flex items-center gap-2 rounded-lg border border-neutral-300 bg-white px-4 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50"><Printer className="h-4 w-4" />Print</button>
          </div>
        </div>
      </header>

      {!decision ? (
        <section className="rounded-xl border border-warning-200 bg-warning-50 p-6">
          <div className="flex items-start gap-3"><AlertCircle className="mt-0.5 h-5 w-5 shrink-0 text-warning-800" /><div><h2 className="font-semibold text-neutral-900">No final analyst decision</h2><p className="mt-2 text-sm text-neutral-600">This is not a finalized report. Return to the workspace after the investigation reaches review.</p></div></div>
        </section>
      ) : (
        <section className="rounded-xl border border-neutral-200 bg-white p-6 shadow-card">
          <div className="flex items-center gap-2"><FileCheck2 className="h-5 w-5 text-brand-500" /><h2 className="font-semibold text-neutral-900">Analyst conclusion</h2></div>
          {decision.abstained ? (
            <div className="mt-5 rounded-lg border border-warning-200 bg-warning-50 p-5">
              <p className="text-xs font-semibold uppercase tracking-wide text-warning-800">Abstained</p>
              <p className="mt-2 text-sm text-neutral-700">{decision.abstentionReason ?? 'The analyst did not record a reason.'}</p>
            </div>
          ) : decision.finalHypothesis ? (
            <div className="mt-5 grid gap-5 lg:grid-cols-[1fr_18rem]">
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-neutral-400">Final hypothesis</p>
                <h3 className="mt-2 text-2xl font-semibold text-neutral-900">{decision.finalHypothesis.label}</h3>
                {decision.finalHypothesis.summary && <p className="mt-3 text-sm leading-6 text-neutral-600">{decision.finalHypothesis.summary}</p>}
                <dl className="mt-5 grid gap-3 text-xs sm:grid-cols-2">
                  <div><dt className="text-neutral-400">Calibration</dt><dd className="mt-1 font-medium text-neutral-800">{investigation.calibratedConfidence === null ? 'Pending benchmark' : `${Math.round(investigation.calibratedConfidence * 100)}% calibrated confidence`}</dd></div>
                  <div><dt className="text-neutral-400">Decision time</dt><dd className="mt-1 font-medium text-neutral-800">{new Date(decision.createdAt).toLocaleString()}</dd></div>
                  <div><dt className="text-neutral-400">Country</dt><dd className="mt-1 font-medium text-neutral-800">{decision.finalHypothesis.countryCode ?? 'Not recorded'}</dd></div>
                  <div><dt className="text-neutral-400">Region / city</dt><dd className="mt-1 font-medium text-neutral-800">{[decision.finalHypothesis.region, decision.finalHypothesis.city].filter(Boolean).join(', ') || 'Not recorded'}</dd></div>
                </dl>
              </div>
              <div className="grid min-h-40 place-items-center rounded-lg border border-neutral-200 bg-neutral-50 p-5 text-center">
                <MapPin className="h-7 w-7 text-brand-500" />
                {typeof decision.finalHypothesis.latitude === 'number' && typeof decision.finalHypothesis.longitude === 'number' ? (
                  <p className="mt-3 font-mono text-sm text-neutral-700">{decision.finalHypothesis.latitude.toFixed(5)}, {decision.finalHypothesis.longitude.toFixed(5)}</p>
                ) : <p className="mt-3 text-xs text-neutral-500">No precise coordinates were included in the analyst decision.</p>}
              </div>
            </div>
          ) : null}
          {decision.notes && <div className="mt-5 border-t border-neutral-100 pt-4"><p className="text-xs font-medium text-neutral-400">Analyst notes</p><p className="mt-2 text-sm leading-6 text-neutral-700">{decision.notes}</p></div>}
        </section>
      )}

      <section className="rounded-xl border border-neutral-200 bg-white p-5 shadow-card">
        <div className="flex items-center gap-2"><ShieldCheck className="h-4 w-4 text-brand-500" /><h2 className="text-sm font-semibold text-neutral-900">Source lineage</h2></div>
        <dl className="mt-5 grid gap-5 text-xs sm:grid-cols-2 lg:grid-cols-4">
          <div><dt className="text-neutral-400">Publisher</dt><dd className="mt-1 break-words font-medium text-neutral-800">{source.publisherUrl ? <a href={source.publisherUrl} target="_blank" rel="noreferrer" className="text-brand-500 underline decoration-neutral-300 underline-offset-2">{source.publisherUrl}</a> : 'Not recorded'}</dd></div>
          <div><dt className="text-neutral-400">Legal basis / licence</dt><dd className="mt-1 font-medium text-neutral-800">{source.legalBasis}{source.license ? ` · ${source.license}` : ''}</dd></div>
          <div><dt className="text-neutral-400">Published / collected</dt><dd className="mt-1 font-medium text-neutral-800">{source.publishedAt ? new Date(source.publishedAt).toLocaleString() : 'Publication time not recorded'}<br />Collected {new Date(source.collectedAt).toLocaleString()}</dd></div>
          <div><dt className="text-neutral-400">Redistribution</dt><dd className="mt-1 font-medium text-neutral-800">{source.redistributionPolicy}</dd></div>
          <div className="sm:col-span-2"><dt className="text-neutral-400">Media SHA-256</dt><dd className="mt-1 break-all font-mono text-neutral-800">{source.mediaSha256 ?? 'Not recorded'}</dd></div>
          <div><dt className="text-neutral-400">Lifecycle eligibility tier</dt><dd className="mt-1 font-medium text-neutral-800">{source.retentionDays} days <span className="block font-normal text-neutral-400">Physical removal may occur later.</span></dd></div>
          <div><dt className="text-neutral-400">Pinned source-media purge target</dt><dd className="mt-1 font-medium text-neutral-800">{new Date(source.purgeAfter).toLocaleString()}</dd></div>
        </dl>
      </section>

      <div className="grid gap-5 lg:grid-cols-2">
        <section className="rounded-xl border border-neutral-200 bg-white p-5 shadow-card">
          <div className="flex items-center justify-between gap-3"><div className="flex items-center gap-2"><CheckCircle2 className="h-4 w-4 text-success-400" /><h2 className="text-sm font-semibold text-neutral-900">Accepted evidence</h2></div><span className="text-xs text-neutral-400">{accepted.length}</span></div>
          <p className="mt-2 text-xs leading-5 text-neutral-500">Analyst acceptance is a report disposition, not independent verification. Only verifier-produced evidence is labelled verified.</p>
          {accepted.length === 0 ? <p className="mt-4 text-xs text-neutral-500">No evidence was accepted.</p> : (
            <ul className="mt-4 space-y-3">{accepted.map((item) => <li key={item.evidenceId} className="rounded-lg border border-neutral-200 p-3"><div className="flex justify-between gap-3 text-xs"><span className="font-medium text-neutral-600">{item.kind}</span><span className="text-neutral-400">{Math.round(item.reliability * 100)}%</span></div><VerificationBadge state={item.verificationState} /><p className="mt-2 text-sm leading-5 text-neutral-700">{item.observation.summary}</p><p className="mt-2 text-xs text-neutral-400">Correlation group: {item.correlationGroup}</p></li>)}</ul>
          )}
        </section>
        <section className="rounded-xl border border-neutral-200 bg-white p-5 shadow-card">
          <div className="flex items-center justify-between gap-3"><div className="flex items-center gap-2"><XCircle className="h-4 w-4 text-danger-400" /><h2 className="text-sm font-semibold text-neutral-900">Rejected evidence</h2></div><span className="text-xs text-neutral-400">{rejected.length}</span></div>
          {rejected.length === 0 ? <p className="mt-4 text-xs text-neutral-500">No evidence was rejected.</p> : (
            <ul className="mt-4 space-y-3">{rejected.map((item) => <li key={item.evidenceId} className="rounded-lg border border-neutral-200 p-3"><div className="flex justify-between gap-3 text-xs"><span className="font-medium text-neutral-600">{item.kind}</span><span className="text-neutral-400">{Math.round(item.reliability * 100)}%</span></div><VerificationBadge state={item.verificationState} /><p className="mt-2 text-sm leading-5 text-neutral-700">{item.observation.summary}</p><p className="mt-2 text-xs text-neutral-400">Correlation group: {item.correlationGroup}</p></li>)}</ul>
          )}
        </section>
      </div>

      <section className="rounded-xl border border-neutral-200 bg-white p-5 shadow-card">
        <div className="flex items-center gap-2"><ShieldCheck className="h-4 w-4 text-brand-500" /><h2 className="text-sm font-semibold text-neutral-900">Provenance and uncertainty</h2></div>
        <dl className="mt-5 grid gap-5 text-xs sm:grid-cols-2 lg:grid-cols-4">
          <div><dt className="text-neutral-400">Execution boundary</dt><dd className="mt-1 font-medium text-neutral-800">{executionBoundary}</dd></div>
          <div><dt className="text-neutral-400">Model</dt><dd className="mt-1 font-medium text-neutral-800">{investigation.modelProvenance.modelId ?? 'Not recorded'}</dd></div>
          <div><dt className="text-neutral-400">Trace ID</dt><dd className="mt-1 break-all font-mono text-neutral-800">{investigation.traceId ?? 'Not recorded'}</dd></div>
          <div><dt className="text-neutral-400">Latest entropy</dt><dd className="mt-1 font-mono font-medium text-neutral-800">{latestBelief?.entropy.toFixed(3) ?? 'Not available'}</dd></div>
        </dl>
        {latestBelief && latestBelief.candidates.length > 0 && (
          <div className="mt-5 border-t border-neutral-100 pt-4">
            <p className="text-xs font-medium text-neutral-400">Latest baseline posterior (uncalibrated)</p>
            <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{latestBelief.candidates.map((candidate) => <div key={candidate.id}><div className="flex justify-between gap-2 text-xs"><span className="truncate text-neutral-700">{candidate.label}</span><span className="font-mono text-neutral-500">{Math.round(candidate.probability * 100)}%</span></div><div className="mt-1 h-1.5 overflow-hidden rounded-full bg-neutral-100"><span className="block h-full rounded-full bg-brand-400" style={{ width: `${candidate.probability * 100}%` }} /></div></div>)}</div>
          </div>
        )}
      </section>
    </article>
  )
}
