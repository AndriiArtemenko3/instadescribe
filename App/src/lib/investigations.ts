import { browserCsrfToken, type BrowserUploadContract } from './browserIntegration'

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const SHA256_PATTERN = /^[0-9a-f]{64}$/
const UTC_TIMESTAMP_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$/

export const INVESTIGATION_KINDS = ['geolocateProvenance', 'damageChange'] as const
export const CONNECTIVITY_POLICIES = ['local', 'textOnly', 'approvedCrops', 'connected'] as const
export const INVESTIGATION_STATUSES = [
  'awaitingUpload',
  'queued',
  'preprocessing',
  'investigating',
  'needsReview',
  'completed',
  'failed',
  'cancelled',
] as const

export type InvestigationKind = typeof INVESTIGATION_KINDS[number]
export type ConnectivityPolicy = typeof CONNECTIVITY_POLICIES[number]
export type InvestigationStatus = typeof INVESTIGATION_STATUSES[number]
export type BrowserRole = 'owner' | 'editor' | 'reviewer' | 'viewer'
export type EvidenceDecisionValue = 'accepted' | 'rejected'

export interface BrowserSessionUser {
  email: string
  displayName: string
  organizationId: string
  role: BrowserRole
}

export interface Hypothesis {
  id: string
  label: string
  countryCode: string | null
  region: string | null
  city: string | null
  latitude: number | null
  longitude: number | null
  summary: string | null
}

export type HypothesisInput = Pick<Hypothesis, 'id' | 'label'> & Partial<Omit<Hypothesis, 'id' | 'label'>>

export interface ModelProvenance {
  modelId: string | null
  modelDigest: string | null
  promptDigest: string | null
  executedLocally: boolean
}

export interface RuntimeProvenance {
  runtime: string | null
  runtimeVersion: string | null
  platform: string | null
}

export interface InvestigationSummary {
  investigationId: string
  projectId: string
  jobId: string
  name: string
  kind: InvestigationKind
  connectivityPolicy: ConnectivityPolicy
  status: InvestigationStatus
  abstained: boolean
  calibratedConfidence: number | null
  createdAt: string
  updatedAt: string
}

export interface InvestigationDetail extends InvestigationSummary {
  traceId: string | null
  modelProvenance: ModelProvenance
  runtimeProvenance: RuntimeProvenance
  finalHypothesis: Hypothesis | null
  abstentionReason: string | null
  completedAt: string | null
}

export interface NormalizedBoundingBox {
  x: number
  y: number
  width: number
  height: number
}

export interface EvidenceObservation {
  summary: string
}

export interface EvidenceItem {
  evidenceId: string
  kind: string
  observation: EvidenceObservation
  frameTimeMs: number | null
  bbox: NormalizedBoundingBox | null
  polarity: 'supports' | 'contradicts' | 'neutral'
  reliability: number
  verificationState: 'proposed' | 'verified' | 'rejected'
  correlationGroup: string
  createdAt: string
}

export interface InvestigationKeyframe {
  evidenceId: string
  frameTimeMs: number
  observation: EvidenceObservation
  bbox: NormalizedBoundingBox | null
  createdAt: string
}

export interface BeliefCandidate extends Hypothesis {
  probability: number
}

export interface BeliefSnapshot {
  beliefSnapshotId: string
  sequence: number
  candidates: BeliefCandidate[]
  entropy: number
  abstained: boolean
  createdAt: string
}

export interface PolicyDecision {
  decision: 'pending' | 'approved' | 'rejected' | 'notRequired'
  decidedByPrincipalId: string | null
  decidedAt: string | null
}

export interface InvestigationStep {
  stepId: string
  sequence: number
  kind: string
  tool: string
  state: 'pending' | 'running' | 'completed' | 'failed' | 'approved' | 'rejected'
  inputEvidenceIds: string[]
  outputEvidenceIds: string[]
  modelDigest: string | null
  promptDigest: string | null
  latencyMs: number | null
  peakMemoryMb: number | null
  costMicrounits: number | null
  policyDecision: PolicyDecision
  entropyBefore: number | null
  entropyAfter: number | null
  startedAt: string | null
  completedAt: string | null
}

export interface AnalystDecision {
  decisionId: string
  status: 'final'
  evidenceDecisions: Array<{ evidenceId: string; decision: EvidenceDecisionValue }>
  finalHypothesis: Hypothesis | null
  abstained: boolean
  abstentionReason: string | null
  notes: string | null
  decidedByPrincipalId: string
  createdAt: string
}

export interface InvestigationSourceRecord {
  sourceRecordId: string
  publisherUrl: string | null
  publishedAt: string | null
  collectedAt: string
  legalBasis: 'publicDomain' | 'licensed' | 'consent' | 'analystAuthorized'
  license: string | null
  mediaSha256: string | null
  redistributionPolicy: 'prohibited' | 'metadataOnly' | 'permitted'
  retentionDays: number
  purgeAfter: string
}

export interface InvestigationReport {
  investigation: InvestigationDetail
  source: InvestigationSourceRecord
  decision: AnalystDecision | null
  evidence: EvidenceItem[]
  latestBelief: BeliefSnapshot | null
}

export interface InvestigationWorkspaceData {
  investigation: InvestigationDetail
  keyframes: InvestigationKeyframe[]
  evidence: EvidenceItem[]
  beliefs: BeliefSnapshot[]
  steps: InvestigationStep[]
  role: BrowserRole
}

export interface CreateInvestigationInput {
  name: string
  kind: InvestigationKind
  connectivityPolicy: ConnectivityPolicy
  video: {
    fileName: string
    contentType: 'video/mp4' | 'video/quicktime' | 'video/webm'
    sizeBytes: number
    durationSeconds: number
  }
  source: {
    publisherUrl?: string
    publishedAt?: string
    legalBasis: 'publicDomain' | 'licensed' | 'consent' | 'analystAuthorized'
    license?: string
    redistributionPolicy: 'prohibited' | 'metadataOnly' | 'permitted'
    retentionDays?: number
  }
}

export interface CreateInvestigationResult {
  investigation: InvestigationDetail
  upload: BrowserUploadContract
}

export interface FinalizeInvestigationInput {
  evidenceDecisions: Array<{ evidenceId: string; decision: EvidenceDecisionValue }>
  finalHypothesis?: HypothesisInput | null
  abstain: boolean
  abstentionReason?: string | null
  notes?: string | null
}

type JsonPrimitive = string | number | boolean | null
type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue }
type InvestigationFetch = (input: string, init?: RequestInit) => Promise<Response>

const DETAILS_MAX_DEPTH = 6
const DETAILS_MAX_NODES = 2_048
const DETAILS_MAX_STRING_LENGTH = 16_384
const DETAILS_MAX_ARRAY_LENGTH = 256
const DETAILS_MAX_OBJECT_KEYS = 128

export class InvestigationApiError extends Error {
  constructor(readonly code: string, readonly status = 0) {
    super(code)
    this.name = 'InvestigationApiError'
  }
}

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function exactKeys(value: Record<string, unknown>, required: readonly string[], optional: readonly string[] = []): boolean {
  const allowed = new Set([...required, ...optional])
  return required.every((key) => Object.hasOwn(value, key)) && Object.keys(value).every((key) => allowed.has(key))
}

function oneOf<const T extends readonly string[]>(value: unknown, choices: T): value is T[number] {
  return typeof value === 'string' && choices.includes(value)
}

function boundedString(value: unknown, maximum = 4096): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= maximum
}

function boundedNonBlankString(value: unknown, maximum = 4096): value is string {
  return boundedString(value, maximum) && value.trim().length > 0
}

function nullableString(value: unknown, maximum = 4096): value is string | null {
  return value === null || boundedString(value, maximum)
}

function uuid(value: unknown): value is string {
  return typeof value === 'string' && UUID_PATTERN.test(value)
}

function utcTimestamp(value: unknown): value is string {
  return typeof value === 'string' && UTC_TIMESTAMP_PATTERN.test(value) && Number.isFinite(Date.parse(value))
}

function nullableTimestamp(value: unknown): value is string | null {
  return value === null || utcTimestamp(value)
}

function finiteRange(value: unknown, minimum: number, maximum: number): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= minimum && value <= maximum
}

function jsonValue(
  value: unknown,
  depth = 0,
  budget: { remaining: number } = { remaining: DETAILS_MAX_NODES },
): value is JsonValue {
  if (depth > DETAILS_MAX_DEPTH || budget.remaining <= 0) return false
  budget.remaining -= 1
  if (value === null || typeof value === 'boolean') return true
  if (typeof value === 'string') return value.length <= DETAILS_MAX_STRING_LENGTH
  if (typeof value === 'number') return Number.isFinite(value)
  if (Array.isArray(value)) {
    return value.length <= DETAILS_MAX_ARRAY_LENGTH && value.every((item) => jsonValue(item, depth + 1, budget))
  }
  const item = record(value)
  if (!item) return false
  const entries = Object.entries(item)
  return entries.length <= DETAILS_MAX_OBJECT_KEYS && entries.every(([key, entry]) => (
    key.length <= 256 && jsonValue(entry, depth + 1, budget)
  ))
}

function parseHypothesis(value: unknown): Hypothesis | null {
  const item = record(value)
  if (!item || !exactKeys(
    item,
    ['id', 'label'],
    ['countryCode', 'region', 'city', 'latitude', 'longitude', 'summary'],
  )) return null
  if (!boundedString(item.id, 120) || !boundedString(item.label, 200)) return null
  const countryCode = Object.hasOwn(item, 'countryCode') ? item.countryCode : null
  const region = Object.hasOwn(item, 'region') ? item.region : null
  const city = Object.hasOwn(item, 'city') ? item.city : null
  const latitude = Object.hasOwn(item, 'latitude') ? item.latitude : null
  const longitude = Object.hasOwn(item, 'longitude') ? item.longitude : null
  const summary = Object.hasOwn(item, 'summary') ? item.summary : null
  if (countryCode !== null && (typeof countryCode !== 'string' || !/^[A-Z]{2}$/.test(countryCode))) return null
  for (const key of ['region', 'city', 'summary'] as const) {
    const maximum = key === 'summary' ? 1000 : 200
    const field = { region, city, summary }[key]
    if (field !== null && !boundedString(field, maximum)) return null
  }
  if (latitude !== null && !finiteRange(latitude, -90, 90)) return null
  if (longitude !== null && !finiteRange(longitude, -180, 180)) return null
  if ((latitude === null) !== (longitude === null)) return null
  return {
    id: item.id,
    label: item.label,
    countryCode: countryCode as string | null,
    region: region as string | null,
    city: city as string | null,
    latitude: latitude as number | null,
    longitude: longitude as number | null,
    summary: summary as string | null,
  }
}

function parseModelProvenance(value: unknown): ModelProvenance | null {
  const item = record(value)
  if (!item || !exactKeys(item, ['executedLocally'], ['modelId', 'modelDigest', 'promptDigest'])) return null
  if (typeof item.executedLocally !== 'boolean') return null
  const modelId = Object.hasOwn(item, 'modelId') ? item.modelId : null
  const modelDigest = Object.hasOwn(item, 'modelDigest') ? item.modelDigest : null
  const promptDigest = Object.hasOwn(item, 'promptDigest') ? item.promptDigest : null
  if (modelId !== null && !boundedString(modelId, 200)) return null
  for (const digest of [modelDigest, promptDigest]) {
    if (digest !== null && (typeof digest !== 'string' || !SHA256_PATTERN.test(digest))) return null
  }
  return {
    executedLocally: item.executedLocally,
    modelId: modelId as string | null,
    modelDigest: modelDigest as string | null,
    promptDigest: promptDigest as string | null,
  }
}

function parseRuntimeProvenance(value: unknown): RuntimeProvenance | null {
  const item = record(value)
  if (!item || !exactKeys(item, [], ['runtime', 'runtimeVersion', 'platform'])) return null
  const runtime = Object.hasOwn(item, 'runtime') ? item.runtime : null
  const runtimeVersion = Object.hasOwn(item, 'runtimeVersion') ? item.runtimeVersion : null
  const platform = Object.hasOwn(item, 'platform') ? item.platform : null
  for (const field of [runtime, runtimeVersion, platform]) {
    if (field !== null && !boundedString(field, 120)) return null
  }
  return {
    runtime: runtime as string | null,
    runtimeVersion: runtimeVersion as string | null,
    platform: platform as string | null,
  }
}

const SUMMARY_KEYS = [
  'investigationId', 'projectId', 'jobId', 'name', 'kind', 'connectivityPolicy', 'status',
  'abstained', 'calibratedConfidence', 'createdAt', 'updatedAt',
] as const

function parseInvestigationSummary(value: unknown): InvestigationSummary | null {
  const item = record(value)
  if (!item || !exactKeys(item, SUMMARY_KEYS)) return null
  if (
    !uuid(item.investigationId) || !uuid(item.projectId) || !uuid(item.jobId) ||
    !boundedString(item.name, 200) || !oneOf(item.kind, INVESTIGATION_KINDS) ||
    !oneOf(item.connectivityPolicy, CONNECTIVITY_POLICIES) || !oneOf(item.status, INVESTIGATION_STATUSES) ||
    typeof item.abstained !== 'boolean' ||
    !(item.calibratedConfidence === null || finiteRange(item.calibratedConfidence, 0, 1)) ||
    !utcTimestamp(item.createdAt) || !utcTimestamp(item.updatedAt)
  ) return null
  return item as unknown as InvestigationSummary
}

export function parseInvestigationDetail(value: unknown): InvestigationDetail | null {
  const item = record(value)
  const detailKeys = [
    ...SUMMARY_KEYS,
    'traceId', 'modelProvenance', 'runtimeProvenance', 'finalHypothesis',
    'abstentionReason', 'completedAt',
  ]
  if (!item || !exactKeys(item, detailKeys)) return null
  const summaryCandidate = Object.fromEntries(SUMMARY_KEYS.map((key) => [key, item[key]]))
  const summary = parseInvestigationSummary(summaryCandidate)
  const modelProvenance = parseModelProvenance(item.modelProvenance)
  const runtimeProvenance = parseRuntimeProvenance(item.runtimeProvenance)
  const finalHypothesis = item.finalHypothesis === null ? null : parseHypothesis(item.finalHypothesis)
  if (
    !summary || !(item.traceId === null || uuid(item.traceId)) || !modelProvenance || !runtimeProvenance ||
    (item.finalHypothesis !== null && !finalHypothesis) || !nullableString(item.abstentionReason, 500) ||
    !nullableTimestamp(item.completedAt)
  ) return null
  const abstentionReason = item.abstentionReason as string | null
  const completedAt = item.completedAt as string | null
  const completed = summary.status === 'completed'
  if (completed !== (completedAt !== null)) return null
  if (summary.abstained) {
    if (finalHypothesis !== null || !boundedNonBlankString(abstentionReason, 500)) return null
  } else {
    if (abstentionReason !== null || completed !== (finalHypothesis !== null)) return null
  }
  return {
    ...summary,
    traceId: item.traceId as string | null,
    modelProvenance,
    runtimeProvenance,
    finalHypothesis,
    abstentionReason,
    completedAt,
  }
}

function parseObservation(value: unknown): EvidenceObservation | null {
  const item = record(value)
  if (!item || !exactKeys(item, ['summary'], ['details']) || !boundedString(item.summary, 1000)) return null
  const details = Object.hasOwn(item, 'details') ? item.details : null
  if (details !== null && (!record(details) || !jsonValue(details))) return null
  // `details` is internal machine material. Validate a transitional wire
  // member defensively, then discard it so UI state cannot retain or render it.
  return { summary: item.summary }
}

function parseBoundingBox(value: unknown): NormalizedBoundingBox | null {
  const item = record(value)
  if (!item || !exactKeys(item, ['x', 'y', 'width', 'height'])) return null
  if (
    !finiteRange(item.x, 0, 1) || !finiteRange(item.y, 0, 1) ||
    !finiteRange(item.width, 0, 1) || item.width === 0 ||
    !finiteRange(item.height, 0, 1) || item.height === 0 ||
    item.x + item.width > 1 || item.y + item.height > 1
  ) return null
  return item as unknown as NormalizedBoundingBox
}

function parseEvidence(value: unknown): EvidenceItem | null {
  const item = record(value)
  if (!item || !exactKeys(
    item,
    ['evidenceId', 'kind', 'observation', 'frameTimeMs', 'bbox', 'polarity', 'reliability', 'verificationState', 'correlationGroup', 'createdAt'],
  )) return null
  const observation = parseObservation(item.observation)
  const bbox = item.bbox === null ? null : parseBoundingBox(item.bbox)
  if (
    !uuid(item.evidenceId) || !oneOf(item.kind, ['keyframe', 'visual', 'ocr', 'audio', 'metadata', 'web', 'geospatial', 'change'] as const) || !observation ||
    !(item.frameTimeMs === null || (Number.isSafeInteger(item.frameTimeMs) && (item.frameTimeMs as number) >= 0)) ||
    (item.bbox !== null && !bbox) ||
    !oneOf(item.polarity, ['supports', 'contradicts', 'neutral'] as const) ||
    !finiteRange(item.reliability, 0, 1) ||
    !oneOf(item.verificationState, ['proposed', 'verified', 'rejected'] as const) ||
    !boundedString(item.correlationGroup, 120) || !utcTimestamp(item.createdAt)
  ) return null
  return {
    evidenceId: item.evidenceId,
    kind: item.kind,
    observation,
    frameTimeMs: item.frameTimeMs as number | null,
    bbox,
    polarity: item.polarity,
    reliability: item.reliability,
    verificationState: item.verificationState,
    correlationGroup: item.correlationGroup,
    createdAt: item.createdAt,
  }
}

function parseKeyframe(value: unknown): InvestigationKeyframe | null {
  const item = record(value)
  if (!item || !exactKeys(item, ['evidenceId', 'frameTimeMs', 'observation', 'bbox', 'createdAt'])) return null
  const observation = parseObservation(item.observation)
  const bbox = item.bbox === null ? null : parseBoundingBox(item.bbox)
  if (
    !uuid(item.evidenceId) || !Number.isSafeInteger(item.frameTimeMs) || (item.frameTimeMs as number) < 0 || !observation ||
    (item.bbox !== null && !bbox) || !utcTimestamp(item.createdAt)
  ) return null
  return {
    evidenceId: item.evidenceId,
    frameTimeMs: item.frameTimeMs as number,
    observation,
    bbox,
    createdAt: item.createdAt,
  }
}

function parseBelief(value: unknown): BeliefSnapshot | null {
  const item = record(value)
  if (!item || !exactKeys(item, ['beliefSnapshotId', 'sequence', 'candidates', 'entropy', 'abstained', 'createdAt'])) return null
  if (
    !uuid(item.beliefSnapshotId) || !Number.isSafeInteger(item.sequence) || (item.sequence as number) < 1 ||
    !Array.isArray(item.candidates) || item.candidates.length > 100 ||
    !finiteRange(item.entropy, 0, Number.MAX_SAFE_INTEGER) || typeof item.abstained !== 'boolean' || !utcTimestamp(item.createdAt)
  ) return null
  const candidates: BeliefCandidate[] = []
  for (const candidateValue of item.candidates) {
    const candidate = record(candidateValue)
    if (!candidate || !exactKeys(
      candidate,
      ['id', 'label', 'probability'],
      ['countryCode', 'region', 'city', 'latitude', 'longitude', 'summary'],
    ) || !finiteRange(candidate.probability, 0, 1)) return null
    const { probability, ...hypothesisValue } = candidate
    const hypothesis = parseHypothesis(hypothesisValue)
    if (!hypothesis) return null
    candidates.push({ ...hypothesis, probability })
  }
  const total = candidates.reduce((sum, candidate) => sum + candidate.probability, 0)
  if (candidates.length > 0 && Math.abs(total - 1) > 0.00001) return null
  return {
    beliefSnapshotId: item.beliefSnapshotId,
    sequence: item.sequence as number,
    candidates,
    entropy: item.entropy,
    abstained: item.abstained,
    createdAt: item.createdAt,
  }
}

function parsePolicyDecision(value: unknown): PolicyDecision | null {
  const item = record(value)
  if (!item || !exactKeys(item, ['decision', 'decidedByPrincipalId', 'decidedAt'])) return null
  if (
    !oneOf(item.decision, ['pending', 'approved', 'rejected', 'notRequired'] as const) ||
    !(item.decidedByPrincipalId === null || uuid(item.decidedByPrincipalId)) ||
    !nullableTimestamp(item.decidedAt)
  ) return null
  return item as unknown as PolicyDecision
}

function parseStep(value: unknown): InvestigationStep | null {
  const item = record(value)
  if (!item || !exactKeys(
    item,
    [
      'stepId', 'sequence', 'kind', 'tool', 'state', 'inputEvidenceIds', 'outputEvidenceIds',
      'modelDigest', 'promptDigest', 'latencyMs', 'peakMemoryMb', 'costMicrounits',
      'policyDecision', 'entropyBefore', 'entropyAfter', 'startedAt', 'completedAt',
    ],
  )) return null
  const policyDecision = parsePolicyDecision(item.policyDecision)
  if (
    !uuid(item.stepId) || !Number.isSafeInteger(item.sequence) || (item.sequence as number) < 1 ||
    !boundedString(item.kind, 40) || !boundedString(item.tool, 120) ||
    !oneOf(item.state, ['pending', 'running', 'completed', 'failed', 'approved', 'rejected'] as const) ||
    !Array.isArray(item.inputEvidenceIds) || !Array.isArray(item.outputEvidenceIds) ||
    item.inputEvidenceIds.length > 10_000 || item.outputEvidenceIds.length > 10_000 ||
    !item.inputEvidenceIds.every(uuid) || !item.outputEvidenceIds.every(uuid) ||
    !(item.modelDigest === null || (typeof item.modelDigest === 'string' && SHA256_PATTERN.test(item.modelDigest))) ||
    !(item.promptDigest === null || (typeof item.promptDigest === 'string' && SHA256_PATTERN.test(item.promptDigest))) ||
    !(item.latencyMs === null || (Number.isSafeInteger(item.latencyMs) && (item.latencyMs as number) >= 0)) ||
    !(item.peakMemoryMb === null || (Number.isSafeInteger(item.peakMemoryMb) && (item.peakMemoryMb as number) >= 0)) ||
    !(item.costMicrounits === null || (Number.isSafeInteger(item.costMicrounits) && (item.costMicrounits as number) >= 0)) ||
    !policyDecision ||
    !(item.entropyBefore === null || finiteRange(item.entropyBefore, 0, Number.MAX_SAFE_INTEGER)) ||
    !(item.entropyAfter === null || finiteRange(item.entropyAfter, 0, Number.MAX_SAFE_INTEGER)) ||
    !nullableTimestamp(item.startedAt) || !nullableTimestamp(item.completedAt)
  ) return null
  return {
    stepId: item.stepId,
    sequence: item.sequence as number,
    kind: item.kind,
    tool: item.tool,
    state: item.state,
    inputEvidenceIds: item.inputEvidenceIds as string[],
    outputEvidenceIds: item.outputEvidenceIds as string[],
    modelDigest: item.modelDigest as string | null,
    promptDigest: item.promptDigest as string | null,
    latencyMs: item.latencyMs as number | null,
    peakMemoryMb: item.peakMemoryMb as number | null,
    costMicrounits: item.costMicrounits as number | null,
    policyDecision,
    entropyBefore: item.entropyBefore as number | null,
    entropyAfter: item.entropyAfter as number | null,
    startedAt: item.startedAt as string | null,
    completedAt: item.completedAt as string | null,
  }
}

function parseAnalystDecision(value: unknown): AnalystDecision | null {
  const item = record(value)
  if (!item || !exactKeys(item, [
    'decisionId', 'status', 'evidenceDecisions', 'finalHypothesis', 'abstained',
    'abstentionReason', 'notes', 'decidedByPrincipalId', 'createdAt',
  ])) return null
  if (
    !uuid(item.decisionId) || item.status !== 'final' || !Array.isArray(item.evidenceDecisions) ||
    item.evidenceDecisions.length > 10_000 || typeof item.abstained !== 'boolean' ||
    !nullableString(item.abstentionReason, 500) || !nullableString(item.notes, 2000) ||
    !uuid(item.decidedByPrincipalId) || !utcTimestamp(item.createdAt)
  ) return null
  const evidenceDecisions: AnalystDecision['evidenceDecisions'] = []
  for (const value of item.evidenceDecisions) {
    const decision = record(value)
    if (
      !decision || !exactKeys(decision, ['evidenceId', 'decision']) || !uuid(decision.evidenceId) ||
      !oneOf(decision.decision, ['accepted', 'rejected'] as const)
    ) return null
    evidenceDecisions.push(decision as unknown as AnalystDecision['evidenceDecisions'][number])
  }
  const hypothesis = item.finalHypothesis === null ? null : parseHypothesis(item.finalHypothesis)
  if (item.finalHypothesis !== null && !hypothesis) return null
  if (
    item.abstained
      ? hypothesis !== null || item.abstentionReason === null
      : hypothesis === null || item.abstentionReason !== null
  ) return null
  return {
    decisionId: item.decisionId,
    status: 'final',
    evidenceDecisions,
    finalHypothesis: hypothesis,
    abstained: item.abstained,
    abstentionReason: item.abstentionReason as string | null,
    notes: item.notes as string | null,
    decidedByPrincipalId: item.decidedByPrincipalId,
    createdAt: item.createdAt,
  }
}

function parseSourceRecord(value: unknown): InvestigationSourceRecord | null {
  const item = record(value)
  if (!item || !exactKeys(
    item,
    [
      'sourceRecordId', 'collectedAt', 'legalBasis', 'redistributionPolicy',
      'retentionDays', 'purgeAfter',
    ],
    ['publisherUrl', 'publishedAt', 'license', 'mediaSha256'],
  )) return null
  const publisherUrl = Object.hasOwn(item, 'publisherUrl') ? item.publisherUrl : null
  const publishedAt = Object.hasOwn(item, 'publishedAt') ? item.publishedAt : null
  const license = Object.hasOwn(item, 'license') ? item.license : null
  const mediaSha256 = Object.hasOwn(item, 'mediaSha256') ? item.mediaSha256 : null
  if (
    !uuid(item.sourceRecordId) || !utcTimestamp(item.collectedAt) || !utcTimestamp(item.purgeAfter) ||
    !oneOf(item.legalBasis, ['publicDomain', 'licensed', 'consent', 'analystAuthorized'] as const) ||
    !oneOf(item.redistributionPolicy, ['prohibited', 'metadataOnly', 'permitted'] as const) ||
    !Number.isSafeInteger(item.retentionDays) || (item.retentionDays as number) < 1 || (item.retentionDays as number) > 30 ||
    !(publishedAt === null || utcTimestamp(publishedAt)) ||
    !(license === null || boundedString(license, 200)) ||
    !(mediaSha256 === null || (typeof mediaSha256 === 'string' && SHA256_PATTERN.test(mediaSha256))) ||
    Date.parse(item.purgeAfter) <= Date.parse(item.collectedAt)
  ) return null
  if (item.legalBasis === 'licensed' && license === null) return null
  if (publisherUrl !== null) {
    if (!boundedString(publisherUrl, 2048) || publisherUrl.split('').some((character) => /\s/.test(character))) return null
    let parsed: URL
    try {
      parsed = new URL(publisherUrl)
    } catch {
      return null
    }
    if (parsed.protocol !== 'https:' || !parsed.hostname || parsed.username || parsed.password || parsed.hash) return null
  }
  return {
    sourceRecordId: item.sourceRecordId,
    publisherUrl: publisherUrl as string | null,
    publishedAt: publishedAt as string | null,
    collectedAt: item.collectedAt,
    legalBasis: item.legalBasis,
    license: license as string | null,
    mediaSha256: mediaSha256 as string | null,
    redistributionPolicy: item.redistributionPolicy,
    retentionDays: item.retentionDays as number,
    purgeAfter: item.purgeAfter,
  }
}

function parseEnvelope<T>(value: unknown, parseItem: (item: unknown) => T | null, maximum = 10_000): T[] | null {
  const envelope = record(value)
  if (!envelope || !exactKeys(envelope, ['data']) || !Array.isArray(envelope.data) || envelope.data.length > maximum) return null
  const result: T[] = []
  for (const item of envelope.data) {
    const parsed = parseItem(item)
    if (!parsed) return null
    result.push(parsed)
  }
  return result
}

function parseSession(value: unknown): BrowserSessionUser | null {
  const envelope = record(value)
  const user = record(envelope?.user)
  if (!envelope || !exactKeys(envelope, ['user']) || !user || !exactKeys(user, ['email', 'displayName', 'organizationId', 'role'])) return null
  if (
    !boundedString(user.email, 254) || !boundedString(user.displayName, 512) ||
    !boundedString(user.organizationId, 512) || !oneOf(user.role, ['owner', 'editor', 'reviewer', 'viewer'] as const)
  ) return null
  return user as unknown as BrowserSessionUser
}

function parseUpload(value: unknown): BrowserUploadContract | null {
  const item = record(value)
  const fields = record(item?.fields)
  if (!item || !exactKeys(item, ['method', 'url', 'fields', 'expiresAt']) || item.method !== 'POST' || !fields || !utcTimestamp(item.expiresAt)) return null
  if (!boundedString(item.url, 16_384)) return null
  let url: URL
  try {
    url = new URL(item.url)
  } catch {
    return null
  }
  const loopback = ['localhost', '127.0.0.1', '::1'].includes(url.hostname)
  if ((url.protocol !== 'https:' && !(url.protocol === 'http:' && loopback)) || url.username || url.password) return null
  const normalizedFields: Record<string, string> = {}
  if (Object.keys(fields).length > 100) return null
  for (const [name, field] of Object.entries(fields)) {
    if (!boundedString(name, 256) || typeof field !== 'string' || field.length > 32_768) return null
    normalizedFields[name] = field
  }
  return { method: 'POST', url: url.href, fields: normalizedFields, expiresAt: item.expiresAt }
}

async function errorCode(response: Response): Promise<string> {
  try {
    const body = record(await response.json())
    const problemCode = body?.code
    const nested = record(body?.error)?.code
    const code = typeof problemCode === 'string' ? problemCode : nested
    return typeof code === 'string' && /^[a-z0-9_]{1,80}$/.test(code) ? code : 'request_failed'
  } catch {
    return 'request_failed'
  }
}

async function getJson(path: string, investigationFetch: InvestigationFetch, signal?: AbortSignal): Promise<unknown> {
  const response = await investigationFetch(`/api/bff/cloud/${path}`, {
    credentials: 'same-origin',
    cache: 'no-store',
    redirect: 'error',
    referrerPolicy: 'no-referrer',
    signal,
  })
  if (!response.ok) throw new InvestigationApiError(await errorCode(response), response.status)
  try {
    return await response.json()
  } catch {
    throw new InvestigationApiError('invalid_response', response.status)
  }
}

async function postJson(
  path: string,
  body: unknown,
  idempotencyKey: string,
  investigationFetch: InvestigationFetch,
): Promise<unknown> {
  if (typeof idempotencyKey !== 'string' || !/^[\x21-\x7e]{1,255}$/.test(idempotencyKey)) {
    throw new InvestigationApiError('invalid_idempotency_key')
  }
  const response = await investigationFetch(`/api/bff/cloud/${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': idempotencyKey,
      'X-CSRF-Token': browserCsrfToken(),
    },
    body: JSON.stringify(body),
    credentials: 'same-origin',
    cache: 'no-store',
    redirect: 'error',
    referrerPolicy: 'no-referrer',
  })
  if (!response.ok) throw new InvestigationApiError(await errorCode(response), response.status)
  try {
    return await response.json()
  } catch {
    throw new InvestigationApiError('invalid_response', response.status)
  }
}

export async function loadBrowserSession(signal?: AbortSignal, investigationFetch: InvestigationFetch = fetch): Promise<BrowserSessionUser> {
  const response = await investigationFetch('/api/bff/session', {
    credentials: 'same-origin',
    cache: 'no-store',
    redirect: 'error',
    referrerPolicy: 'no-referrer',
    signal,
  })
  if (!response.ok) throw new InvestigationApiError(await errorCode(response), response.status)
  let value: unknown
  try {
    value = await response.json()
  } catch {
    throw new InvestigationApiError('invalid_response', response.status)
  }
  const user = parseSession(value)
  if (!user) throw new InvestigationApiError('invalid_response', response.status)
  return user
}

export async function listInvestigations(signal?: AbortSignal, investigationFetch: InvestigationFetch = fetch): Promise<InvestigationSummary[]> {
  const parsed = parseEnvelope(await getJson('investigations', investigationFetch, signal), parseInvestigationSummary, 100)
  if (!parsed) throw new InvestigationApiError('invalid_response')
  return parsed
}

export async function loadInvestigationWorkspace(
  investigationId: string,
  signal?: AbortSignal,
  investigationFetch: InvestigationFetch = fetch,
): Promise<InvestigationWorkspaceData> {
  if (!uuid(investigationId)) throw new InvestigationApiError('invalid_investigation_id')
  const [detailValue, keyframesValue, evidenceValue, beliefsValue, stepsValue, role] = await Promise.all([
    getJson(`investigations/${investigationId}`, investigationFetch, signal),
    getJson(`investigations/${investigationId}/keyframes`, investigationFetch, signal),
    getJson(`investigations/${investigationId}/evidence`, investigationFetch, signal),
    getJson(`investigations/${investigationId}/beliefs`, investigationFetch, signal),
    getJson(`investigations/${investigationId}/steps`, investigationFetch, signal),
    loadBrowserSession(signal, investigationFetch),
  ])
  const investigation = parseInvestigationDetail(detailValue)
  const keyframes = parseEnvelope(keyframesValue, parseKeyframe, 10_000)
  const evidence = parseEnvelope(evidenceValue, parseEvidence, 10_000)
  const beliefs = parseEnvelope(beliefsValue, parseBelief, 10_000)
  const steps = parseEnvelope(stepsValue, parseStep, 10_000)
  if (!investigation || !keyframes || !evidence || !beliefs || !steps) throw new InvestigationApiError('invalid_response')
  return { investigation, keyframes, evidence, beliefs, steps, role: role.role }
}

export async function createInvestigation(
  input: CreateInvestigationInput,
  idempotencyKey: string,
  investigationFetch: InvestigationFetch = fetch,
): Promise<CreateInvestigationResult> {
  const value = record(await postJson('investigations', input, idempotencyKey, investigationFetch))
  if (!value || !exactKeys(value, ['investigation', 'upload'])) throw new InvestigationApiError('invalid_response')
  const investigation = parseInvestigationDetail(value.investigation)
  const upload = parseUpload(value.upload)
  if (!investigation || !upload) throw new InvestigationApiError('invalid_response')
  return { investigation, upload }
}

export async function cancelInvestigation(
  investigationId: string,
  idempotencyKey: string,
  investigationFetch: InvestigationFetch = fetch,
): Promise<InvestigationDetail> {
  if (!uuid(investigationId)) throw new InvestigationApiError('invalid_investigation_id')
  const investigation = parseInvestigationDetail(await postJson(
    `investigations/${investigationId}/cancel`,
    {},
    idempotencyKey,
    investigationFetch,
  ))
  if (!investigation) throw new InvestigationApiError('invalid_response')
  return investigation
}

export async function finalizeInvestigation(
  investigationId: string,
  input: FinalizeInvestigationInput,
  idempotencyKey: string,
  investigationFetch: InvestigationFetch = fetch,
): Promise<{ investigation: InvestigationDetail; decision: AnalystDecision }> {
  if (!uuid(investigationId)) throw new InvestigationApiError('invalid_investigation_id')
  const value = record(await postJson(
    `investigations/${investigationId}/decision`,
    input,
    idempotencyKey,
    investigationFetch,
  ))
  if (!value || !exactKeys(value, ['investigation', 'decision'])) throw new InvestigationApiError('invalid_response')
  const investigation = parseInvestigationDetail(value.investigation)
  const decision = parseAnalystDecision(value.decision)
  if (!investigation || !decision) throw new InvestigationApiError('invalid_response')
  return { investigation, decision }
}

export async function loadInvestigationReport(
  investigationId: string,
  signal?: AbortSignal,
  investigationFetch: InvestigationFetch = fetch,
): Promise<InvestigationReport> {
  if (!uuid(investigationId)) throw new InvestigationApiError('invalid_investigation_id')
  const value = record(await getJson(`investigations/${investigationId}/report`, investigationFetch, signal))
  if (!value || !exactKeys(value, ['investigation', 'source', 'decision', 'evidence', 'latestBelief']) || !Array.isArray(value.evidence)) {
    throw new InvestigationApiError('invalid_response')
  }
  const investigation = parseInvestigationDetail(value.investigation)
  const source = parseSourceRecord(value.source)
  const decision = value.decision === null ? null : parseAnalystDecision(value.decision)
  const evidence = value.evidence.map(parseEvidence)
  const latestBelief = value.latestBelief === null ? null : parseBelief(value.latestBelief)
  if (
    !investigation || !source || (value.decision !== null && !decision) || evidence.some((item) => !item) ||
    (value.latestBelief !== null && !latestBelief)
  ) throw new InvestigationApiError('invalid_response')
  return { investigation, source, decision, evidence: evidence as EvidenceItem[], latestBelief }
}

export function canCreateInvestigation(role: BrowserRole): boolean {
  return role === 'owner' || role === 'editor'
}

export function canCancelInvestigation(role: BrowserRole): boolean {
  return role === 'owner' || role === 'editor'
}

export function canReviewInvestigation(role: BrowserRole): boolean {
  return role === 'owner' || role === 'reviewer'
}

export function canFinalizeInvestigation(role: BrowserRole): boolean {
  return role === 'owner' || role === 'reviewer'
}

export function formatFrameTime(frameTimeMs: number): string {
  const totalSeconds = Math.max(0, Math.floor(frameTimeMs / 1000))
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
    : `${minutes}:${String(seconds).padStart(2, '0')}`
}
