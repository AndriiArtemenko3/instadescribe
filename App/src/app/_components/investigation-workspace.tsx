'use client'

import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import Link from 'next/link'
import {
  Activity,
  AlertTriangle,
  Check,
  ChevronRight,
  CircleDot,
  Clock3,
  FileCheck2,
  ImageIcon,
  LockKeyhole,
  MapPin,
  ShieldCheck,
  X,
} from 'lucide-react'
import {
  canCancelInvestigation,
  canFinalizeInvestigation,
  canReviewInvestigation,
  cancelInvestigation,
  finalizeInvestigation,
  formatFrameTime,
  InvestigationApiError,
  loadInvestigationWorkspace,
  type BeliefSnapshot,
  type EvidenceDecisionValue,
  type FinalizeInvestigationInput,
  type InvestigationDetail,
  type InvestigationKeyframe,
  type InvestigationWorkspaceData,
} from '@/lib/investigations'

type WorkspaceState =
  | { kind: 'loading' }
  | { kind: 'ready'; data: InvestigationWorkspaceData }
  | { kind: 'signedOut' }
  | { kind: 'notFound' }
  | { kind: 'unavailable'; message: string }

const statusLabels: Record<InvestigationDetail['status'], string> = {
  awaitingUpload: 'Awaiting upload',
  queued: 'Queued',
  preprocessing: 'Preprocessing',
  investigating: 'Investigating',
  needsReview: 'Needs review',
  completed: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
}

const policyLabels: Record<InvestigationDetail['connectivityPolicy'], string> = {
  local: 'Local only',
  textOnly: 'Unavailable mode',
  approvedCrops: 'Unavailable mode',
  connected: 'Unavailable mode',
}

const verificationLabels = {
  proposed: 'Proposed observation',
  verified: 'Verified by tool',
  rejected: 'Rejected by verifier',
} as const

const TERMINAL_STATUSES = new Set<InvestigationDetail['status']>(['completed', 'failed', 'cancelled'])
const POLL_BASE_INTERVAL_MS = 5_000
const POLL_MAX_INTERVAL_MS = 30_000
const DEFAULT_MACHINE_ABSTENTION_REASON = 'The machine belief state abstained because the available evidence was insufficient to support a candidate.'

function latestBelief(beliefs: BeliefSnapshot[]): BeliefSnapshot | null {
  return beliefs.reduce<BeliefSnapshot | null>((latest, belief) => (
    latest === null || belief.sequence > latest.sequence ? belief : latest
  ), null)
}

function CoordinatePanel({ investigation }: { investigation: InvestigationDetail }) {
  const hypothesis = investigation.finalHypothesis
  const hasCoordinates = typeof hypothesis?.latitude === 'number' && typeof hypothesis.longitude === 'number'
  const left = hasCoordinates ? ((hypothesis.longitude! + 180) / 360) * 100 : 50
  const top = hasCoordinates ? ((90 - hypothesis.latitude!) / 180) * 100 : 50

  return (
    <div
      className="relative h-32 overflow-hidden rounded-lg border border-neutral-200 bg-neutral-50"
      role="img"
      aria-label={hasCoordinates ? `Analyst-finalized location hypothesis: ${hypothesis.label}` : 'No analyst-finalized coordinates available'}
    >
      <div className="absolute inset-0 opacity-60" style={{
        backgroundImage: 'linear-gradient(#e0e0e0 1px, transparent 1px), linear-gradient(90deg, #e0e0e0 1px, transparent 1px)',
        backgroundSize: '25% 33.333%',
      }} />
      {hasCoordinates ? (
        <div className="absolute -translate-x-1/2 -translate-y-1/2" style={{ left: `${left}%`, top: `${top}%` }}>
          <span className="block h-3 w-3 rounded-full border-2 border-white bg-brand-400 shadow-card" />
          <span className="mt-1 block max-w-40 rounded bg-neutral-950 px-2 py-1 text-xs text-white">{hypothesis.label}</span>
        </div>
      ) : (
        <div className="absolute inset-0 grid place-items-center p-4 text-center">
          <p className="max-w-48 text-xs text-neutral-500">No analyst-finalized coordinates are present in the API response.</p>
        </div>
      )}
    </div>
  )
}

function KeyframeRail({
  keyframes,
  selectedId,
  onSelect,
}: {
  keyframes: InvestigationKeyframe[]
  selectedId: string | null
  onSelect: (keyframe: InvestigationKeyframe) => void
}) {
  return (
    <aside className="rounded-xl border border-neutral-200 bg-white p-4 shadow-card">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-sm font-semibold text-neutral-900">Keyframe metadata</h2>
        <span className="rounded-full bg-neutral-100 px-2 py-1 text-xs text-neutral-500">{keyframes.length}</span>
      </div>
      {keyframes.length === 0 ? (
        <p className="mt-5 rounded-lg border border-dashed border-neutral-200 p-4 text-xs leading-5 text-neutral-500">
          Ranked frame metadata will appear after local preprocessing.
        </p>
      ) : (
        <ol className="mt-4 max-h-[42rem] space-y-2 overflow-y-auto pr-1">
          {[...keyframes].sort((a, b) => a.frameTimeMs - b.frameTimeMs).map((keyframe, index) => (
            <li key={keyframe.evidenceId}>
              <button
                type="button"
                onClick={() => onSelect(keyframe)}
                aria-pressed={selectedId === keyframe.evidenceId}
                aria-controls="keyframe-metadata-detail"
                className={`w-full rounded-lg border p-3 text-left transition ${
                  selectedId === keyframe.evidenceId
                    ? 'border-brand-400 bg-brand-50'
                    : 'border-neutral-200 bg-white hover:bg-neutral-50'
                }`}
              >
                <span className="flex items-center justify-between gap-2 text-xs">
                  <span className="font-semibold text-neutral-700">Frame {index + 1}</span>
                  <span className="font-mono text-neutral-400">{formatFrameTime(keyframe.frameTimeMs)}</span>
                </span>
                <span className="mt-2 line-clamp-3 block text-xs leading-5 text-neutral-600">{keyframe.observation.summary}</span>
              </button>
            </li>
          ))}
        </ol>
      )}
    </aside>
  )
}

export function InvestigationWorkspace({ investigationId }: { investigationId: string }) {
  const [state, setState] = useState<WorkspaceState>({ kind: 'loading' })
  const [selectedKeyframe, setSelectedKeyframe] = useState<InvestigationKeyframe | null>(null)
  const [evidenceDecisions, setEvidenceDecisions] = useState<Record<string, EvidenceDecisionValue>>({})
  const [selectedCandidateId, setSelectedCandidateId] = useState('')
  const [abstain, setAbstain] = useState(false)
  const [abstentionReason, setAbstentionReason] = useState('')
  const [notes, setNotes] = useState('')
  const [actionMessage, setActionMessage] = useState<string | null>(null)
  const [busyAction, setBusyAction] = useState<string | null>(null)
  const cancelAttemptKey = useRef<string | null>(null)
  const finalizeAttempt = useRef<{ fingerprint: string; key: string } | null>(null)

  useEffect(() => {
    cancelAttemptKey.current = null
    finalizeAttempt.current = null
  }, [investigationId])

  useEffect(() => {
    const controller = new AbortController()
    let active = true
    let hasLoaded = false
    let failureCount = 0
    let timer: ReturnType<typeof setTimeout> | undefined

    const schedule = (delayMs: number) => {
      if (!active) return
      timer = setTimeout(() => void load(), delayMs)
    }

    const load = async () => {
      try {
        const data = await loadInvestigationWorkspace(investigationId, controller.signal)
        if (!active) return
        hasLoaded = true
        failureCount = 0
        setState({ kind: 'ready', data })
        setSelectedKeyframe((current) => (
          data.keyframes.find((keyframe) => keyframe.evidenceId === current?.evidenceId) ?? data.keyframes[0] ?? null
        ))
        const belief = latestBelief(data.beliefs)
        setSelectedCandidateId((current) => (
          belief?.abstained
            ? ''
            : belief?.candidates.some((candidate) => candidate.id === current)
            ? current
            : belief?.candidates[0]?.id ?? ''
        ))
        if (!TERMINAL_STATUSES.has(data.investigation.status)) schedule(POLL_BASE_INTERVAL_MS)
      } catch (error: unknown) {
        if (!active || (error instanceof DOMException && error.name === 'AbortError')) return
        if (error instanceof InvestigationApiError && error.status === 401) {
          setState({ kind: 'signedOut' })
          return
        }
        if (error instanceof InvestigationApiError && error.status === 404) {
          setState({ kind: 'notFound' })
          return
        }
        if (!hasLoaded) {
          setState({
            kind: 'unavailable',
            message: error instanceof InvestigationApiError && error.code === 'invalid_response'
              ? 'The service response did not match the trusted investigation contract.'
              : 'The investigation service is temporarily unavailable.',
          })
        }
        failureCount += 1
        const backoffMs = Math.min(POLL_BASE_INTERVAL_MS * (2 ** Math.min(failureCount, 3)), POLL_MAX_INTERVAL_MS)
        schedule(backoffMs)
      }
    }

    void load()
    return () => {
      active = false
      controller.abort()
      if (timer !== undefined) clearTimeout(timer)
    }
  }, [investigationId])

  const currentBelief = useMemo(() => state.kind === 'ready' ? latestBelief(state.data.beliefs) : null, [state])
  const machineAbstained = currentBelief?.abstained === true
  const recordedMachineAbstentionReason = state.kind === 'ready'
    ? state.data.investigation.abstentionReason?.trim() || DEFAULT_MACHINE_ABSTENTION_REASON
    : DEFAULT_MACHINE_ABSTENTION_REASON

  useEffect(() => {
    if (!machineAbstained) return
    setSelectedCandidateId('')
    setAbstentionReason((current) => current.trim() || recordedMachineAbstentionReason)
  }, [machineAbstained, recordedMachineAbstentionReason])

  async function cancel() {
    if (state.kind !== 'ready' || !canCancelInvestigation(state.data.role)) return
    setBusyAction('cancel')
    setActionMessage(null)
    const idempotencyKey = cancelAttemptKey.current ?? crypto.randomUUID()
    cancelAttemptKey.current = idempotencyKey
    try {
      const investigation = await cancelInvestigation(investigationId, idempotencyKey)
      cancelAttemptKey.current = null
      setState({ kind: 'ready', data: { ...state.data, investigation } })
      setActionMessage('Investigation cancelled. Stale worker output cannot be published.')
    } catch {
      setActionMessage('Cancellation could not be confirmed.')
    } finally {
      setBusyAction(null)
    }
  }

  async function finalize(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (state.kind !== 'ready' || !canFinalizeInvestigation(state.data.role)) return
    if (state.data.evidence.some((item) => !evidenceDecisions[item.evidenceId])) {
      setActionMessage('Accept or reject every evidence item before finalizing.')
      return
    }
    const effectiveAbstain = machineAbstained || abstain
    const effectiveAbstentionReason = abstentionReason.trim() || (machineAbstained ? recordedMachineAbstentionReason : '')
    const selectedCandidate = effectiveAbstain
      ? undefined
      : currentBelief?.candidates.find((candidate) => candidate.id === selectedCandidateId)
    if (!effectiveAbstain && !selectedCandidate) {
      setActionMessage('Choose a candidate or explicitly abstain.')
      return
    }
    if (effectiveAbstain && !effectiveAbstentionReason) {
      setActionMessage('Record why the available evidence is insufficient.')
      return
    }
    setBusyAction('finalize')
    setActionMessage(null)
    const input: FinalizeInvestigationInput = {
        evidenceDecisions: state.data.evidence.map((item) => ({
          evidenceId: item.evidenceId,
          decision: evidenceDecisions[item.evidenceId],
        })),
        ...(effectiveAbstain ? {} : { finalHypothesis: {
          id: selectedCandidate!.id,
          label: selectedCandidate!.label,
          countryCode: selectedCandidate!.countryCode,
          region: selectedCandidate!.region,
          city: selectedCandidate!.city,
          latitude: selectedCandidate!.latitude,
          longitude: selectedCandidate!.longitude,
          summary: selectedCandidate!.summary,
        } }),
        abstain: effectiveAbstain,
        ...(effectiveAbstain ? { abstentionReason: effectiveAbstentionReason } : {}),
        ...(notes.trim() ? { notes: notes.trim() } : {}),
      }
    const fingerprint = JSON.stringify(input)
    const attempt = finalizeAttempt.current?.fingerprint === fingerprint
      ? finalizeAttempt.current
      : { fingerprint, key: crypto.randomUUID() }
    finalizeAttempt.current = attempt
    try {
      await finalizeInvestigation(investigationId, input, attempt.key)
      finalizeAttempt.current = null
      window.location.assign(`/investigations/${encodeURIComponent(investigationId)}/report`)
    } catch (error) {
      setActionMessage(error instanceof InvestigationApiError && error.code === 'evidence_decisions_incomplete'
        ? 'The evidence set changed. Reload and review every current item.'
        : 'The analyst decision was not accepted.')
      setBusyAction(null)
    }
  }

  if (state.kind === 'loading') {
    return <div className="h-[40rem] animate-pulse rounded-xl border border-neutral-200 bg-white" aria-label="Loading investigation" />
  }

  if (state.kind === 'signedOut') {
    return (
      <section className="rounded-xl border border-neutral-200 bg-white p-10 text-center">
        <LockKeyhole className="mx-auto h-6 w-6 text-neutral-400" />
        <h1 className="mt-4 text-xl font-semibold text-neutral-900">Sign in to open this investigation</h1>
        <Link href={`/login?returnTo=${encodeURIComponent(`/investigations/${investigationId}`)}`} className="mt-5 inline-flex rounded-lg bg-brand-400 px-4 py-2 text-sm font-medium text-white">Sign in</Link>
      </section>
    )
  }

  if (state.kind === 'notFound') {
    return (
      <section className="rounded-xl border border-neutral-200 bg-white p-10 text-center">
        <h1 className="text-xl font-semibold text-neutral-900">Investigation not found</h1>
        <p className="mt-2 text-sm text-neutral-500">Missing and cross-organisation identifiers are intentionally indistinguishable.</p>
        <Link href="/investigations" className="mt-5 inline-flex text-sm font-medium text-brand-500">Back to investigations</Link>
      </section>
    )
  }

  if (state.kind === 'unavailable') {
    return <p className="rounded-xl border border-warning-200 bg-warning-50 p-6 text-sm text-neutral-700" role="alert">{state.message}</p>
  }

  const { investigation, keyframes, evidence, steps, role } = state.data
  const mayReview = canReviewInvestigation(role)
  const mayFinalize = canFinalizeInvestigation(role)
  const selectedEvidence = selectedKeyframe
    ? evidence.find((item) => item.evidenceId === selectedKeyframe.evidenceId)
    : null
  const terminal = TERMINAL_STATUSES.has(investigation.status)
  const decidedCount = evidence.filter((item) => Boolean(evidenceDecisions[item.evidenceId])).length
  const executionLabel = investigation.modelProvenance.modelId === 'deterministic-fixture'
    ? 'Deterministic fixture · no model inference'
    : investigation.status === 'investigating'
    ? 'Local execution in progress'
    : ['needsReview', 'completed'].includes(investigation.status) && investigation.modelProvenance.executedLocally
    ? 'Model executed locally'
    : ['failed', 'cancelled'].includes(investigation.status)
    ? 'Local execution configured · no completed run recorded'
    : investigation.modelProvenance.executedLocally
    ? 'Local execution configured · pending'
    : 'Local execution configured · pending'

  return (
    <div className="space-y-5">
      <header className="flex flex-col gap-5 border-b border-neutral-200 pb-5 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <div className="flex flex-wrap items-center gap-2 text-xs text-neutral-500">
            <Link href="/investigations" className="hover:text-neutral-900">Investigations</Link>
            <ChevronRight className="h-3.5 w-3.5" />
            <span>{investigation.kind === 'geolocateProvenance' ? 'Geolocation & provenance' : 'Visual change review'}</span>
          </div>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight text-neutral-900">{investigation.name}</h1>
          <div className="mt-3 flex flex-wrap gap-2">
            <span className="rounded-full bg-neutral-100 px-2.5 py-1 text-xs font-medium text-neutral-600">{statusLabels[investigation.status]}</span>
            <span className="rounded-full bg-brand-50 px-2.5 py-1 text-xs font-medium text-brand-800">{policyLabels[investigation.connectivityPolicy]}</span>
            <span className="rounded-full bg-neutral-100 px-2.5 py-1 text-xs text-neutral-500">Role: {role}</span>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {investigation.status === 'completed' && (
            <Link href={`/investigations/${investigationId}/report`} className="inline-flex items-center gap-2 rounded-lg bg-brand-400 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500">
              <FileCheck2 className="h-4 w-4" /> View report
            </Link>
          )}
          {canCancelInvestigation(role) && !terminal && (
            <button type="button" onClick={() => void cancel()} disabled={busyAction !== null} className="rounded-lg border border-neutral-300 bg-white px-4 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-50">
              {busyAction === 'cancel' ? 'Cancelling…' : 'Cancel'}
            </button>
          )}
        </div>
      </header>

      {actionMessage && <p className="rounded-lg border border-neutral-200 bg-white px-4 py-3 text-sm text-neutral-600" role="status">{actionMessage}</p>}

      <div className="grid gap-4 xl:grid-cols-[14rem_minmax(0,1fr)_21rem]">
        <KeyframeRail keyframes={keyframes} selectedId={selectedKeyframe?.evidenceId ?? null} onSelect={setSelectedKeyframe} />

        <section aria-label="Investigation evidence workspace" className="min-w-0 space-y-4">
          <section id="keyframe-metadata-detail" className="overflow-hidden rounded-xl border border-neutral-200 bg-white shadow-card">
            <div className="flex items-center justify-between border-b border-neutral-200 px-4 py-3">
              <h2 className="text-sm font-semibold text-neutral-900">Keyframe metadata detail</h2>
              <span className="font-mono text-xs text-neutral-400">{selectedKeyframe ? formatFrameTime(selectedKeyframe.frameTimeMs) : 'No frame selected'}</span>
            </div>
            <div className="relative aspect-video overflow-hidden bg-neutral-950">
              <div className="absolute inset-0 opacity-20" style={{
                backgroundImage: 'linear-gradient(#737373 1px, transparent 1px), linear-gradient(90deg, #737373 1px, transparent 1px)',
                backgroundSize: '12.5% 16.666%',
              }} />
              <div className="absolute inset-0 grid place-items-center p-8 text-center">
                <div>
                  <ImageIcon className="mx-auto h-8 w-8 text-neutral-600" />
                  <p className="mt-3 text-sm text-neutral-300">
                    {selectedKeyframe ? selectedKeyframe.observation.summary : 'No keyframe metadata has been published.'}
                  </p>
                  <p className="mt-2 text-xs text-neutral-500">Metadata overlay only · no source pixels are returned by this API.</p>
                </div>
              </div>
              {selectedKeyframe?.bbox && (
                <div
                  className="absolute border-2 border-brand-200 bg-brand-400/10"
                  style={{
                    left: `${selectedKeyframe.bbox.x * 100}%`,
                    top: `${selectedKeyframe.bbox.y * 100}%`,
                    width: `${selectedKeyframe.bbox.width * 100}%`,
                    height: `${selectedKeyframe.bbox.height * 100}%`,
                  }}
                  role="img"
                  aria-label="Normalized bounding-box metadata overlay"
                  aria-describedby="keyframe-bbox-description"
                />
              )}
            </div>
            <dl className="grid gap-3 border-t border-neutral-200 p-4 text-xs sm:grid-cols-3">
              <div><dt className="text-neutral-400">Frame evidence</dt><dd className="mt-1 font-medium text-neutral-700">{selectedKeyframe?.evidenceId ?? 'Not available'}</dd></div>
              <div><dt className="text-neutral-400">Reliability</dt><dd className="mt-1 font-medium text-neutral-700">{selectedEvidence ? `${Math.round(selectedEvidence.reliability * 100)}%` : 'Not scored'}</dd></div>
              <div><dt className="text-neutral-400">Correlation group</dt><dd className="mt-1 font-medium text-neutral-700">{selectedEvidence?.correlationGroup ?? 'Not assigned'}</dd></div>
            </dl>
            <p id="keyframe-bbox-description" className="border-t border-neutral-100 px-4 py-3 text-xs text-neutral-500">
              Any outlined region represents normalized bounding-box coordinates from metadata, not source pixels.
            </p>
          </section>

          <section className="rounded-xl border border-neutral-200 bg-white p-4 shadow-card">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h2 className="text-sm font-semibold text-neutral-900">Objective tool trace</h2>
                <p className="mt-1 text-xs text-neutral-500">Recorded actions and policy decisions—not hidden model reasoning.</p>
              </div>
              {investigation.traceId && <span className="font-mono text-xs text-neutral-400">{investigation.traceId.slice(0, 8)}…</span>}
            </div>
            {steps.length === 0 ? (
              <p className="mt-4 rounded-lg border border-dashed border-neutral-200 p-4 text-xs text-neutral-500">No investigation steps have been recorded.</p>
            ) : (
              <ol className="mt-4 space-y-3">
                {[...steps].sort((a, b) => a.sequence - b.sequence).map((step) => (
                    <li key={step.stepId} className="rounded-lg border border-neutral-200 p-3">
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div className="flex gap-3">
                          <span className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full bg-neutral-100 text-xs font-semibold text-neutral-500">{step.sequence}</span>
                          <div>
                            <p className="text-sm font-medium text-neutral-800">{step.kind}</p>
                            <p className="mt-1 font-mono text-xs text-neutral-400">{step.tool}</p>
                          </div>
                        </div>
                        <span className="rounded-full bg-neutral-100 px-2 py-1 text-xs text-neutral-500">{step.state}</span>
                      </div>
                      {(step.entropyBefore !== null || step.entropyAfter !== null) && (
                        <p className="mt-3 text-xs text-neutral-500">Entropy {step.entropyBefore?.toFixed(3) ?? '—'} → {step.entropyAfter?.toFixed(3) ?? '—'}</p>
                      )}
                      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-neutral-400">
                        <span>{step.inputEvidenceIds.length} inputs / {step.outputEvidenceIds.length} outputs</span>
                        <span>Policy: {step.policyDecision.decision}</span>
                        {step.latencyMs !== null && <span>{step.latencyMs} ms</span>}
                        {step.peakMemoryMb !== null && <span>{step.peakMemoryMb} MB peak</span>}
                        {step.costMicrounits !== null && <span>{step.costMicrounits} cost µ-units</span>}
                      </div>
                    </li>
                  ))}
              </ol>
            )}
          </section>
        </section>

        <aside className="space-y-4">
          <section className="rounded-xl border border-neutral-200 bg-white p-4 shadow-card">
            <div className="flex items-center gap-2">
              <MapPin className="h-4 w-4 text-brand-500" />
              <h2 className="text-sm font-semibold text-neutral-900">Baseline posterior (uncalibrated)</h2>
            </div>
            <div className="mt-4"><CoordinatePanel investigation={investigation} /></div>
            {currentBelief ? (
              <div className="mt-4 space-y-3">
                {currentBelief.candidates.map((candidate) => (
                  <div key={candidate.id}>
                    <div className="flex justify-between gap-3 text-xs"><span className="truncate font-medium text-neutral-700">{candidate.label}</span><span className="font-mono text-neutral-500">{Math.round(candidate.probability * 100)}%</span></div>
                    <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-neutral-100"><span className="block h-full rounded-full bg-brand-400" style={{ width: `${candidate.probability * 100}%` }} /></div>
                  </div>
                ))}
                <div className="flex items-center justify-between border-t border-neutral-100 pt-3 text-xs text-neutral-500"><span>Entropy</span><span className="font-mono">{currentBelief.entropy.toFixed(3)}</span></div>
                {currentBelief.abstained && <p className="rounded-lg bg-warning-50 p-2 text-xs text-warning-800">The model-side belief state is abstaining.</p>}
              </div>
            ) : <p className="mt-4 text-xs leading-5 text-neutral-500">No baseline posterior snapshot has been published.</p>}
          </section>

          <section className="rounded-xl border border-neutral-200 bg-white p-4 shadow-card">
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-sm font-semibold text-neutral-900">Evidence ledger</h2>
              <span className="text-xs text-neutral-400">{decidedCount}/{evidence.length} decided</span>
            </div>
            {evidence.length === 0 ? (
              <p className="mt-4 rounded-lg border border-dashed border-neutral-200 p-4 text-xs text-neutral-500">No evidence has been proposed.</p>
            ) : (
              <ul className="mt-4 max-h-[34rem] space-y-3 overflow-y-auto pr-1">
                {evidence.map((item) => (
                  <li key={item.evidenceId} className="rounded-lg border border-neutral-200 p-3">
                    <div className="flex items-center justify-between gap-2">
                      <span className="rounded bg-neutral-100 px-1.5 py-1 text-xs font-medium text-neutral-600">{item.kind}</span>
                      <span className={`text-xs ${item.polarity === 'contradicts' ? 'text-danger-800' : item.polarity === 'supports' ? 'text-success-800' : 'text-neutral-400'}`}>{item.polarity}</span>
                    </div>
                    <p className="mt-2 text-xs leading-5 text-neutral-700">{item.observation.summary}</p>
                    <div className="mt-2 flex justify-between gap-3 text-xs text-neutral-400"><span>{Math.round(item.reliability * 100)}% reliability</span><span>{verificationLabels[item.verificationState]}</span></div>
                    {mayReview && investigation.status === 'needsReview' && (
                      <div className="mt-3 grid grid-cols-2 gap-2">
                        <button type="button" aria-label={`Accept evidence ${item.evidenceId}`} aria-pressed={evidenceDecisions[item.evidenceId] === 'accepted'} onClick={() => setEvidenceDecisions((current) => ({ ...current, [item.evidenceId]: 'accepted' }))} className={`inline-flex items-center justify-center gap-1 rounded-md border px-2 py-1.5 text-xs font-medium ${evidenceDecisions[item.evidenceId] === 'accepted' ? 'border-success-400 bg-success-50 text-success-800' : 'border-neutral-200 text-neutral-600'}`}><Check className="h-3.5 w-3.5" />Accept</button>
                        <button type="button" aria-label={`Reject evidence ${item.evidenceId}`} aria-pressed={evidenceDecisions[item.evidenceId] === 'rejected'} onClick={() => setEvidenceDecisions((current) => ({ ...current, [item.evidenceId]: 'rejected' }))} className={`inline-flex items-center justify-center gap-1 rounded-md border px-2 py-1.5 text-xs font-medium ${evidenceDecisions[item.evidenceId] === 'rejected' ? 'border-danger-400 bg-danger-50 text-danger-800' : 'border-neutral-200 text-neutral-600'}`}><X className="h-3.5 w-3.5" />Reject</button>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </aside>
      </div>

      {investigation.status === 'needsReview' && (
        <form onSubmit={finalize} className="rounded-xl border border-neutral-200 bg-white p-5 shadow-card">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <div className="flex items-center gap-2"><ShieldCheck className="h-5 w-5 text-brand-500" /><h2 className="font-semibold text-neutral-900">Analyst decision</h2></div>
              <p className="mt-2 max-w-2xl text-sm text-neutral-500">Every evidence item requires an explicit decision. Finalization locks the review and preserves the evidence trace.</p>
            </div>
            {!mayFinalize && <span className="rounded-lg bg-neutral-100 px-3 py-2 text-xs text-neutral-500">Only an Owner or Reviewer can finalize.</span>}
          </div>
          {machineAbstained && (
            <p id="machine-abstention-constraint" className="mt-5 rounded-lg border border-warning-200 bg-warning-50 p-4 text-sm text-warning-800" role="alert">
              Machine abstention constraint: the latest belief state abstained. Finalization must preserve abstention, so a final candidate cannot be selected.
            </p>
          )}
          <div className="mt-5 grid gap-4 lg:grid-cols-3">
            <label className="text-xs font-medium text-neutral-600">
              Final candidate
              <select value={machineAbstained ? '' : selectedCandidateId} onChange={(event) => setSelectedCandidateId(event.target.value)} disabled={!mayFinalize || abstain || machineAbstained} aria-describedby={machineAbstained ? 'machine-abstention-constraint' : undefined} className="mt-1 block w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm font-normal text-neutral-900 disabled:bg-neutral-100">
                <option value="">Select a candidate</option>
                {currentBelief?.candidates.map((candidate) => <option key={candidate.id} value={candidate.id}>{candidate.label}</option>)}
              </select>
            </label>
            <label className="text-xs font-medium text-neutral-600">
              Notes <span className="font-normal text-neutral-400">(optional)</span>
              <input value={notes} onChange={(event) => setNotes(event.target.value)} maxLength={2000} disabled={!mayFinalize} className="mt-1 block w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm font-normal text-neutral-900" />
            </label>
            <div>
              <label className="flex items-center gap-2 text-xs font-medium text-neutral-600">
                <input type="checkbox" checked={machineAbstained || abstain} onChange={(event) => setAbstain(event.target.checked)} disabled={!mayFinalize || machineAbstained} aria-describedby={machineAbstained ? 'machine-abstention-constraint' : undefined} />
                Abstain: evidence is insufficient
              </label>
              {(machineAbstained || abstain) && <input value={abstentionReason} onChange={(event) => setAbstentionReason(event.target.value)} required maxLength={500} aria-label="Abstention reason" placeholder="Why is the evidence insufficient?" disabled={!mayFinalize} className="mt-2 block w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm text-neutral-900" />}
            </div>
          </div>
          <div className="mt-5 flex flex-wrap items-center gap-3">
            <button type="submit" disabled={!mayFinalize || busyAction !== null || decidedCount !== evidence.length || (evidence.length === 0 && !(machineAbstained || abstain))} className="inline-flex items-center gap-2 rounded-lg bg-brand-400 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500 disabled:cursor-not-allowed disabled:opacity-50">
              <FileCheck2 className="h-4 w-4" />{busyAction === 'finalize' ? 'Finalizing…' : 'Finalize investigation'}
            </button>
            <span className="text-xs text-neutral-500">{decidedCount} of {evidence.length} evidence decisions recorded</span>
          </div>
        </form>
      )}

      <footer className="grid gap-3 rounded-xl border border-neutral-200 bg-neutral-950 p-4 text-xs text-neutral-300 sm:grid-cols-3">
        <span className="flex items-center gap-2"><Activity className="h-4 w-4 text-brand-200" />{executionLabel}</span>
        <span className="flex items-center gap-2"><Clock3 className="h-4 w-4 text-brand-200" />Updated {new Date(investigation.updatedAt).toLocaleString()}</span>
        <span className="flex items-center gap-2"><CircleDot className="h-4 w-4 text-brand-200" />{investigation.calibratedConfidence === null ? 'Calibration pending benchmark' : `${Math.round(investigation.calibratedConfidence * 100)}% calibrated confidence`}</span>
      </footer>

      {investigation.status === 'failed' && (
        <p className="flex items-start gap-2 rounded-lg border border-danger-400 bg-danger-50 p-4 text-sm text-danger-800"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />The investigation failed. No unverified output has been promoted to a report.</p>
      )}
    </div>
  )
}
