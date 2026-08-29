// Local scene DRAFTS (edited AD text / active flags).
//
// G7.1 C makes the storage claim exact: cloud drafts are SESSION-SCOPED
// (sessionStorage) and cleared on logout; a failed save may retain a session
// draft for retry; after a successful server Apply the redundant local draft
// is cleared. Cloud draft keys carry the JOB id — scene ids are positional,
// so a project reconciled to a NEW processing job must never replay an
// earlier job's drafts. Tokens and signed URLs are never stored here.
// Legacy/demo/study keep their original localStorage behavior and key shape
// unchanged (demo/study take precedence via isCloudSession).

import { isCloudSession } from './cloudMode'

export interface SceneEdit {
  text?: string
  active?: boolean
}

interface ProjectEdits {
  scenes: Record<number, SceneEdit>
}

const EDITS_SUFFIX = ':edits'

function storage(): Storage {
  return isCloudSession() ? sessionStorage : localStorage
}

function isDraftKey(value: string | null): value is string {
  return !!value && value.startsWith('instascribe:') && value.endsWith(EDITS_SUFFIX)
}

function key(projectId: string, jobId?: string): string {
  if (isCloudSession() && jobId) {
    return `instascribe:${projectId}:${jobId}${EDITS_SUFFIX}`
  }
  return `instascribe:${projectId}${EDITS_SUFFIX}`
}

export function loadEdits(projectId: string, jobId?: string): ProjectEdits {
  try {
    const raw = storage().getItem(key(projectId, jobId))
    if (!raw) return { scenes: {} }
    return JSON.parse(raw) as ProjectEdits
  } catch {
    return { scenes: {} }
  }
}

function saveEdits(projectId: string, edits: ProjectEdits, jobId?: string): void {
  const storageKey = key(projectId, jobId)
  if (Object.keys(edits.scenes).length === 0) {
    storage().removeItem(storageKey)
    return
  }
  storage().setItem(storageKey, JSON.stringify(edits))
}

export function persistSceneText(projectId: string, sceneId: number, text: string, jobId?: string): void {
  const edits = loadEdits(projectId, jobId)
  edits.scenes[sceneId] = { ...edits.scenes[sceneId], text }
  saveEdits(projectId, edits, jobId)
}

export function persistSceneActive(projectId: string, sceneId: number, active: boolean, jobId?: string): void {
  const edits = loadEdits(projectId, jobId)
  edits.scenes[sceneId] = { ...edits.scenes[sceneId], active }
  saveEdits(projectId, edits, jobId)
}

/** After a successful server Apply the local draft is redundant — clear it
    so the server override is the single source (G7.1 C). */
export function clearSceneDraft(projectId: string, sceneId: number, jobId?: string): void {
  try {
    const edits = loadEdits(projectId, jobId)
    if (edits.scenes[sceneId] === undefined) return
    delete edits.scenes[sceneId]
    saveEdits(projectId, edits, jobId)
  } catch {
    /* draft cleanup is best-effort */
  }
}

/** A PARTIAL save (e.g. only `active`) must clear only the fields it sent —
    an unsent text draft survives for the next Apply. */
export function clearSceneDraftFields(
  projectId: string,
  sceneId: number,
  fields: Array<keyof SceneEdit>,
  jobId?: string,
): void {
  try {
    const edits = loadEdits(projectId, jobId)
    const entry = edits.scenes[sceneId]
    if (entry === undefined) return
    for (const field of fields) delete entry[field]
    if (Object.keys(entry).length === 0) delete edits.scenes[sceneId]
    saveEdits(projectId, edits, jobId)
  } catch {
    /* draft cleanup is best-effort */
  }
}

/** Compare-and-clear: a request may resolve after the user has typed again.
    Delete a submitted field only while the current draft still equals the
    exact value captured for that request. */
export function clearSceneDraftFieldsIfUnchanged(
  projectId: string,
  sceneId: number,
  submitted: SceneEdit,
  jobId?: string,
): void {
  try {
    const edits = loadEdits(projectId, jobId)
    const entry = edits.scenes[sceneId]
    if (entry === undefined) return
    for (const field of Object.keys(submitted) as Array<keyof SceneEdit>) {
      if (entry[field] === submitted[field]) delete entry[field]
    }
    if (Object.keys(entry).length === 0) delete edits.scenes[sceneId]
    saveEdits(projectId, edits, jobId)
  } catch {
    /* draft cleanup is best-effort */
  }
}

export interface DraftStorageStats {
  keys: number
  bytes: number
  scope: 'session' | 'local'
}

/** Settings uses this same selector as draft persistence; token/session
    metadata is excluded because only exact draft-key shapes are counted. */
export function getDraftStorageStats(): DraftStorageStats {
  const target = storage()
  let keys = 0
  let bytes = 0
  for (let i = 0; i < target.length; i++) {
    const candidate = target.key(i)
    if (!isDraftKey(candidate)) continue
    const raw = target.getItem(candidate) ?? ''
    try {
      const parsed = JSON.parse(raw) as { scenes?: unknown }
      if (
        typeof parsed?.scenes !== 'object' ||
        parsed.scenes === null ||
        Array.isArray(parsed.scenes) ||
        !Object.values(parsed.scenes).some((entry) => (
          typeof entry === 'object' &&
          entry !== null &&
          !Array.isArray(entry) &&
          (
            typeof (entry as SceneEdit).text === 'string' ||
            typeof (entry as SceneEdit).active === 'boolean'
          )
        ))
      ) {
        continue
      }
    } catch {
      continue
    }
    keys += 1
    bytes += raw.length
  }
  return { keys, bytes, scope: isCloudSession() ? 'session' : 'local' }
}

export function clearCurrentModeDrafts(): void {
  const target = storage()
  const doomed: string[] = []
  for (let i = 0; i < target.length; i++) {
    const candidate = target.key(i)
    if (isDraftKey(candidate)) doomed.push(candidate)
  }
  doomed.forEach((candidate) => target.removeItem(candidate))
}

/** Cloud logout: remove every session-scoped draft (G7.1 B/C). */
export function clearAllCloudDrafts(): void {
  try {
    const doomed: string[] = []
    for (let i = 0; i < sessionStorage.length; i++) {
      const k = sessionStorage.key(i)
      if (isDraftKey(k)) doomed.push(k)
    }
    doomed.forEach((k) => sessionStorage.removeItem(k))
  } catch {
    /* nothing to clear */
  }
}
