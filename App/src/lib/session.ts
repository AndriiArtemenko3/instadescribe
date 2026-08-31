// Study-mode helpers — feature flag, anonymous session, provisioning, logging.
// Active only when the build is produced with VITE_STUDY_MODE=1; the normal app
// is untouched. The public evaluation protocol documents the study behavior.
import type { Project } from '@/types'
import { legacyApiBase, publicFlag } from './runtimeEnv'

const API_BASE = legacyApiBase()
// These persisted keys intentionally retain the v0.1 namespace so the Vite
// rollback can read existing anonymous study sessions during the beta cutover.
const SESSION_KEY = 'instascribe:studySessionId'
const CONSENT_KEY = 'instascribe:studyConsent'
const TOUR_KEY = 'instascribe:studyTourDone'
const TASKS_KEY = 'instascribe:studyTasks'
const SECURE_SESSION_ID_RE =
  /^s-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/

export function isStudyMode(): boolean {
  return publicFlag('studyMode')
}

/**
 * Public zero-API demo build (VITE_DEMO_MODE=1). Every OpenAI/TTS/render call is
 * short-circuited to committed fixtures or local computation (see lib/demoApi.ts),
 * so the whole editor runs from static files with no key and nothing to break.
 */
export function isDemoBuild(): boolean {
  return publicFlag('demoMode')
}

function secureSessionUuid(): string {
  const webCrypto = globalThis.crypto
  if (!webCrypto) {
    throw new Error('Secure random identifier generation is unavailable')
  }
  if (typeof webCrypto.randomUUID === 'function') {
    return webCrypto.randomUUID()
  }
  if (typeof webCrypto.getRandomValues !== 'function') {
    throw new Error('Secure random identifier generation is unavailable')
  }

  const bytes = new Uint8Array(16)
  webCrypto.getRandomValues(bytes)
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('')
  return [
    hex.slice(0, 8),
    hex.slice(8, 12),
    hex.slice(12, 16),
    hex.slice(16, 20),
    hex.slice(20),
  ].join('-')
}

/** Stable anonymous id for this browser/participant. Created once, never tied to a name. */
export function getSessionId(): string {
  let id = localStorage.getItem(SESSION_KEY)
  if (!id || !SECURE_SESSION_ID_RE.test(id)) {
    id = `s-${secureSessionUuid()}`
    localStorage.setItem(SESSION_KEY, id)
  }
  return id
}

export function hasConsented(): boolean {
  return localStorage.getItem(CONSENT_KEY) === '1'
}

export function setConsented(): void {
  localStorage.setItem(CONSENT_KEY, '1')
}

// Tour-seen is scoped to the session id, not the browser, so a fresh participant
// (new session) always sees the walkthrough — even on a shared or lab machine —
// while the same participant reloading does not re-trigger it.
function tourKey(): string {
  return `${TOUR_KEY}:${getSessionId()}`
}

/** True once this participant (session) has seen or skipped the editor walkthrough. */
export function hasSeenTour(): boolean {
  return localStorage.getItem(tourKey()) === '1'
}

export function setTourSeen(): void {
  localStorage.setItem(tourKey(), '1')
}

/** Clear the flag so the walkthrough plays again on the next open. */
export function resetTour(): void {
  localStorage.removeItem(tourKey())
}

function tasksKey(): string {
  return `${TASKS_KEY}:${getSessionId()}`
}

/** Task ids this participant has completed, persisted across reloads. */
export function getCompletedTasks(): string[] {
  try {
    const raw = localStorage.getItem(tasksKey())
    const parsed = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

/** Record a task as complete; returns the full updated list. */
export function markTaskComplete(id: string): string[] {
  const current = getCompletedTasks()
  if (current.includes(id)) return current
  const next = [...current, id]
  localStorage.setItem(tasksKey(), JSON.stringify(next))
  return next
}

/**
 * Wipe every demo-local key (session id, consent, tour-seen, tasks, per-project
 * edits) so the next visit starts from a clean slate — a fresh session id, a
 * freshly provisioned clip, and the walkthrough playing again. Used by the
 * "Restart demo" control.
 */
export function resetDemo(): void {
  const toRemove: string[] = []
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i)
    if (k && k.startsWith('instascribe:')) toRemove.push(k)
  }
  toRemove.forEach((k) => localStorage.removeItem(k))
}

interface ProvisionResult {
  projectId: string
  name: string
  dataPath: string
  videoFile: string
  durationSecs: number
  sceneCount: number
  status: string
}

/** Ask the backend for an isolated copy of the frozen study clip for this session. */
export async function provisionStudySession(): Promise<Project> {
  const sessionId = getSessionId()
  const res = await fetch(`${API_BASE}/api/study/session`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sessionId }),
  })
  if (!res.ok) throw new Error(`Could not start the session (${res.status})`)
  const r = (await res.json()) as ProvisionResult
  return {
    id: r.projectId,
    name: r.name,
    status: 'ready',
    createdAt: new Date().toISOString(),
    durationSecs: r.durationSecs,
    sceneCount: r.sceneCount,
    videoFile: r.videoFile,
    dataPath: r.dataPath,
  }
}

/** Fire-and-forget anonymised event log. Never throws into the UI. */
export function logEvent(event: string, detail?: unknown): void {
  try {
    const sessionId = getSessionId()
    void fetch(`${API_BASE}/api/log`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sessionId, event, detail, ts: new Date().toISOString() }),
      keepalive: true,
    }).catch(() => {})
  } catch {
    /* logging must never break the participant's session */
  }
}
