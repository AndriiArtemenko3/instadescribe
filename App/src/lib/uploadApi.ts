// TODO: set VITE_API_BASE in .env.local to override (e.g. for production)
//   POST /api/jobs          → { jobId, projectId }
//   GET  /api/jobs/:jobId   → { progress, status, stage, chunks_done, chunks_total, error?,
//                               data_path?, scene_count?, tokens_used? }

import { useAppStore } from '@/store/appStore'
import { isCloudMode } from './cloudMode'
import { legacyApiBase } from './runtimeEnv'
import type { UploadSettings, Project } from '@/types'

const BASE = legacyApiBase()

export interface SubmitResult {
  jobId: string
  projectId: string
}

export interface PollResult {
  progress: number
  status: 'queued' | 'processing' | 'ready' | 'failed'
  stage: string
  chunks_done: number
  chunks_total: number
  error?: string
  data_path?: string
  video_file?: string
  poster_file?: string
  poster_avif_file?: string
  poster_placeholder?: string
  scene_count?: number
  tokens_used?: number
}

export async function submitJob(
  file: File,
  projectName: string,
  settings: UploadSettings,
  durationSecs: number,
): Promise<SubmitResult> {
  // G7 B6: no legacy route exists on the cloud-core — never call it there.
  if (isCloudMode()) throw new Error('Available in the Portfolio Strong release.')
  const form = new FormData()
  form.append('video', file)
  form.append('settings', JSON.stringify({ name: projectName, settings, durationSecs }))

  const res = await fetch(`${BASE}/api/jobs`, { method: 'POST', body: form })
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`Server error ${res.status}: ${text}`)
  }

  const { jobId, projectId } = await res.json() as SubmitResult

  // Register the project in the store immediately so the editor can navigate to it
  const project: Project = {
    id: projectId,
    name: projectName || 'Untitled Project',
    status: 'processing',
    createdAt: new Date().toISOString(),
    durationSecs: Math.round(durationSecs),
    model: settings.model,
    chunkSize: settings.chunkSizeSecs,
  }
  useAppStore.getState().addProject(project)

  return { jobId, projectId }
}

export async function pollStatus(jobId: string): Promise<PollResult> {
  // G7 B6: no legacy route exists on the cloud-core — never call it there.
  if (isCloudMode()) throw new Error('Available in the Portfolio Strong release.')
  const res = await fetch(`${BASE}/api/jobs/${jobId}`)
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`Poll error ${res.status}: ${text}`)
  }
  return res.json() as Promise<PollResult>
}

interface JobsListEntry {
  status: 'queued' | 'processing' | 'ready' | 'failed' | 'unknown' | 'not_found'
  progress: number
  project_name?: string
  duration_secs?: number
  model?: string
  chunk_size?: number
  data_path?: string
  video_file?: string
  poster_file?: string
  poster_avif_file?: string
  poster_placeholder?: string
  scene_count?: number
  tokens_used?: number
  starred?: boolean
  error?: string
}

export async function deleteProjectOnServer(id: string): Promise<void> {
  // G7 B6: no legacy route exists on the cloud-core — never call it there.
  if (isCloudMode()) throw new Error('Available in the Portfolio Strong release.')
  const res = await fetch(`${BASE}/api/jobs/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`Delete failed: ${res.status}`)
}

export async function patchProjectOnServer(
  id: string,
  patch: { name?: string; starred?: boolean },
): Promise<void> {
  // G7 B6: no legacy route exists on the cloud-core — never call it there.
  if (isCloudMode()) throw new Error('Available in the Portfolio Strong release.')
  const res = await fetch(`${BASE}/api/jobs/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
  if (!res.ok) throw new Error(`Patch failed: ${res.status}`)
}

let _reconcileInFlight: Promise<void> | null = null

// Reconcile local store against server's view of all jobs:
//   - patch existing projects with any missing fields (e.g. videoFile after a stale poll)
//   - add projects the local store doesn't know about (recovery from cleared localStorage)
// Concurrent calls are coalesced — React StrictMode mounts effects twice in dev, and
// without this guard the second pass races past the existence check and duplicates rows.
export async function reconcileProjectsWithServer(): Promise<void> {
  if (isCloudMode()) return // cloud reconciliation lives in cloudProjects.ts
  if (_reconcileInFlight) return _reconcileInFlight
  _reconcileInFlight = (async () => {
    let map: Record<string, JobsListEntry>
    try {
      const res = await fetch(`${BASE}/api/jobs`)
      if (!res.ok) return
      map = await res.json() as Record<string, JobsListEntry>
    } catch {
      return
    }

    for (const [id, j] of Object.entries(map)) {
      // Re-read state every iteration so addProject on an earlier iteration is
      // visible as `existing` on later iterations.
      const store = useAppStore.getState()
      const existing = store.projects.find((p) => p.id === id)

      if (existing) {
        const patch: Partial<Project> = {}
        if (j.status === 'ready' || j.status === 'failed' || j.status === 'processing') {
          if (j.status !== existing.status) patch.status = j.status as Project['status']
        }
        if (j.video_file  && existing.videoFile  !== j.video_file)  patch.videoFile  = j.video_file
        if (j.poster_file && existing.posterUrl  !== j.poster_file) patch.posterUrl  = j.poster_file
        if (j.poster_avif_file   && existing.posterAvifUrl     !== j.poster_avif_file)   patch.posterAvifUrl     = j.poster_avif_file
        if (j.poster_placeholder && existing.posterPlaceholder !== j.poster_placeholder) patch.posterPlaceholder = j.poster_placeholder
        if (j.data_path   && existing.dataPath   !== j.data_path)   patch.dataPath   = j.data_path
        if (j.scene_count != null && existing.sceneCount !== j.scene_count) patch.sceneCount = j.scene_count
        if (j.tokens_used != null && existing.tokensUsed !== j.tokens_used) patch.tokensUsed = j.tokens_used
        if (j.project_name && existing.name !== j.project_name) patch.name = j.project_name
        if (j.starred !== undefined && existing.starred !== j.starred) patch.starred = j.starred
        if (Object.keys(patch).length) store.updateProject(id, patch)
        continue
      }

      if (j.status === 'not_found' || j.status === 'unknown') continue

      const status: Project['status'] =
        j.status === 'ready'  ? 'ready'  :
        j.status === 'failed' ? 'failed' :
        'processing'

      store.addProject({
        id,
        name:         j.project_name ?? `Project ${id.slice(0, 6)}`,
        status,
        createdAt:    new Date().toISOString(),
        durationSecs: j.duration_secs ?? 0,
        sceneCount:   j.scene_count,
        tokensUsed:   j.tokens_used,
        model:        j.model     ?? 'gpt-4.1',
        chunkSize:    j.chunk_size ?? 60,
        videoFile:         j.video_file,
        posterUrl:         j.poster_file,
        posterAvifUrl:     j.poster_avif_file,
        posterPlaceholder: j.poster_placeholder,
        dataPath:          j.data_path,
        starred:           j.starred,
      })
    }
  })().finally(() => { _reconcileInFlight = null })
  return _reconcileInFlight
}
