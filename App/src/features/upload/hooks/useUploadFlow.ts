import { useState, useEffect, useRef, useCallback } from 'react'
import * as uploadApi from '@/lib/uploadApi'
import { useAppStore } from '@/store/appStore'
import { isCloudMode } from '@/lib/cloudMode'
import { CloudApiError, getCloudJob } from '@/lib/cloudApi'
import {
  CLOUD_MAX_FILE_BYTES,
  CLOUD_MAX_FILE_LABEL,
  CloudUploadSession,
  submitErrorMessage,
  validateCloudUpload,
} from '@/lib/cloudUpload'
import type { UploadSettings } from '@/types'
import { ObjectUrlOwner } from '@/lib/objectUrlOwner'
import { fenceCloudProjectReconciliation } from '@/lib/cloudProjects'

export type { PollResult } from '@/lib/uploadApi'

const MAX_FILE_BYTES = 4 * 1024 * 1024 * 1024  // 4 GB hard limit
const WARN_FILE_BYTES = 500 * 1024 * 1024       // 500 MB soft warning
// Cloud polling: non-overlapping ticks, bounded to ~30 minutes at 3 s.
const CLOUD_POLL_MS = 3000
const CLOUD_POLL_MAX_TICKS = 600

export const DEFAULT_SETTINGS: UploadSettings = {
  mode: 'cheap',
  model: 'gpt-4.1',
  fps: 0.5,
  frameQuality: 'low',
  chunkSizeSecs: 120,
  audioExtraction: true,
  detailLevel: 3,
  presetStyle: 'documentary',
  language: null,
}

// The cloud contract offers gpt-4.1 only — the "normal" preset stays valid
// but maps to the cloud-allowlisted values (legacy/demo keep gpt-5.4).
const PRESET_OVERRIDES: Record<'cheap' | 'normal', Partial<UploadSettings>> = {
  cheap:  { model: 'gpt-4.1', fps: 0.5, frameQuality: 'low', chunkSizeSecs: 120, audioExtraction: true },
  normal: isCloudMode()
    ? { model: 'gpt-4.1', fps: 1, frameQuality: 'low', chunkSizeSecs: 60, audioExtraction: true }
    : { model: 'gpt-5.4', fps: 1, frameQuality: 'low', chunkSizeSecs: 60, audioExtraction: true },
}

export function estimateTokens(durationSecs: number, s: UploadSettings): number {
  const frames = Math.ceil(durationSecs * s.fps)
  const tokensPerFrame = s.frameQuality === 'high' ? 1105 : 85
  const chunks = Math.ceil(durationSecs / s.chunkSizeSecs)
  return (frames * tokensPerFrame) + (chunks * 7000)
}

export function estimateMinutes(durationSecs: number, s: UploadSettings): number {
  const base = s.model === 'gpt-5.4' ? 2.0 : 0.8
  const fpsFactor = s.fps === 8 ? 3 : s.fps === 0.5 ? 0.5 : 1
  return Math.max(1, Math.ceil((durationSecs / 60) * base * fpsFactor))
}

function getVideoDuration(file: File): Promise<number> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file)
    const video = document.createElement('video')
    video.preload = 'metadata'
    video.onloadedmetadata = () => { URL.revokeObjectURL(url); resolve(video.duration) }
    video.onerror = () => { URL.revokeObjectURL(url); reject(new Error('Could not read video duration')) }
    video.src = url
  })
}

interface UploadFlowState {
  step: 1 | 2 | 3 | 4 | 5
  projectName: string
  file: File | null
  fileUrl: string | null       // object URL for thumbnail preview
  fileDurationSecs: number
  fileSizeWarning: boolean
  customPrompt: string
  settings: UploadSettings
  jobId: string | null
  newProjectId: string | null
  progress: number
  stage: string
  chunksDone: number
  chunksTotal: number
  isReady: boolean
  isFailed: boolean
  failedError: string | null
  submitError: string | null
}

const INITIAL: UploadFlowState = {
  step: 1,
  projectName: '',
  file: null,
  fileUrl: null,
  fileDurationSecs: 0,
  fileSizeWarning: false,
  customPrompt: '',
  settings: DEFAULT_SETTINGS,
  jobId: null,
  newProjectId: null,
  progress: 0,
  stage: '',
  chunksDone: 0,
  chunksTotal: 0,
  isReady: false,
  isFailed: false,
  failedError: null,
  submitError: null,
}

export function useUploadFlow() {
  const [state, setState] = useState<UploadFlowState>(INITIAL)
  const jobIdRef = useRef<string | null>(null)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const submittingRef = useRef(false) // double-submit guard (G7 B3)
  // One resumable session per selected file (G7.1 A): retries resume the
  // SAME create/upload; only upload-complete repeats for the same jobId.
  const cloudSessionRef = useRef<CloudUploadSession | null>(null)
  const mountedRef = useRef(true)
  const flowGenerationRef = useRef(0)
  // Holds only a DURABLE visible project: creation publishes IDs to this
  // flow immediately but never registers a card until the S3 POST succeeds.
  const registeredRef = useRef<string | null>(null)
  const objectUrlOwnerRef = useRef(new ObjectUrlOwner())

  // G7.1 D2: the final object URL is revoked on unmount.
  useEffect(() => {
    mountedRef.current = true
    const objectUrlOwner = objectUrlOwnerRef.current
    return () => {
      mountedRef.current = false
      flowGenerationRef.current += 1
      objectUrlOwner.clear()
      cloudSessionRef.current?.abandon()
    }
  }, [])

  const updateProject = useAppStore((s) => s.updateProject)

  const setFile = useCallback(async (file: File) => {
    // Cloud enforces the 250 MiB contract BEFORE advancing (G7.1 D1);
    // legacy keeps its 4 GB behavior.
    if (isCloudMode() && file.size > CLOUD_MAX_FILE_BYTES) {
      setState((s) => ({
        ...s,
        submitError: `The cloud demo accepts files up to ${CLOUD_MAX_FILE_LABEL}.`,
      }))
      return
    }
    if (file.size > MAX_FILE_BYTES) {
      setState((s) => ({ ...s, submitError: 'File exceeds the 4 GB limit.' }))
      return
    }
    flowGenerationRef.current += 1
    cloudSessionRef.current?.abandon()
    cloudSessionRef.current = null // a new file starts a fresh session
    registeredRef.current = null
    jobIdRef.current = null

    // G7.1 D2: replacing a file revokes the PREVIOUS object URL.
    const fileUrl = objectUrlOwnerRef.current.replace(file)
    const fileSizeWarning = file.size > WARN_FILE_BYTES
    let fileDurationSecs = 0
    try { fileDurationSecs = await getVideoDuration(file) } catch { /* fallback 0 */ }

    // A newer selection (or cancel) superseded this one while metadata was
    // being read — never commit a stale file over the current state.
    if (!objectUrlOwnerRef.current.owns(fileUrl)) {
      objectUrlOwnerRef.current.revoke(fileUrl)
      return
    }

    setState((s) => ({
      ...s,
      file,
      fileUrl,
      fileDurationSecs,
      fileSizeWarning,
      jobId: null,
      newProjectId: null,
      submitError: null,
    }))
  }, [])

  // A created-but-not-uploaded cloud job carries the payload from creation
  // time. When the user edits inputs BEFORE any byte was uploaded, the
  // stale session (and its provisional project registration) is discarded
  // so the next submit creates a job with the edited payload. After the
  // upload, the session must survive: only completion is retried.
  const invalidatePreByteSession = useCallback(() => {
    const session = cloudSessionRef.current
    if (!session || (session.stage !== 'validated' && session.stage !== 'created')) return
    flowGenerationRef.current += 1
    session.abandon()
    cloudSessionRef.current = null
    jobIdRef.current = null
    setState((state) => ({ ...state, jobId: null, newProjectId: null }))
  }, [])

  const setProjectName = useCallback((projectName: string) => {
    invalidatePreByteSession()
    setState((s) => ({ ...s, projectName }))
  }, [invalidatePreByteSession])

  const setCustomPrompt = useCallback((customPrompt: string) => {
    invalidatePreByteSession()
    setState((s) => ({ ...s, customPrompt }))
  }, [invalidatePreByteSession])

  const setSettings = useCallback((patch: Partial<UploadSettings>) => {
    invalidatePreByteSession()
    setState((s) => {
      const merged = { ...s.settings, ...patch }
      // apply preset overrides when mode changes away from custom
      if (patch.mode && patch.mode !== 'custom') {
        return { ...s, settings: { ...merged, ...PRESET_OVERRIDES[patch.mode] } }
      }
      return { ...s, settings: merged }
    })
  }, [invalidatePreByteSession])

  const next = useCallback(() => {
    setState((s) => ({ ...s, step: Math.min(s.step + 1, 5) as UploadFlowState['step'] }))
  }, [])

  const back = useCallback(() => {
    setState((s) => ({ ...s, step: Math.max(s.step - 1, 1) as UploadFlowState['step'] }))
  }, [])

  const registerCloudProject = useCallback((projectId: string, projectVersion: number, jobId: string) => {
    if (registeredRef.current === projectId) return
    // The session re-created its job (contract expiry before any byte):
    // the superseded ghost project is removed before the new registration.
    if (registeredRef.current) {
      const staleId = registeredRef.current
      useAppStore.setState((s) => ({ projects: s.projects.filter((p) => p.id !== staleId) }))
    }
    registeredRef.current = projectId
    fenceCloudProjectReconciliation()
    // Both DISTINCT identifiers are stored: id = projectId (route + store
    // identity), jobId = processing-job identity (cloud calls).
    useAppStore.getState().addProject({
      id: projectId,
      projectVersion,
      jobId,
      name: state.projectName || 'Untitled Project',
      status: 'confirmation_pending',
      completionPending: true,
      createdAt: new Date().toISOString(),
      durationSecs: Math.round(state.fileDurationSecs),
      model: state.settings.model,
      chunkSize: state.settings.chunkSizeSecs,
    })
  }, [state.projectName, state.fileDurationSecs, state.settings])

  const submit = useCallback(async () => {
    if (!state.file || submittingRef.current) return
    submittingRef.current = true
    setState((s) => ({ ...s, submitError: null }))
    try {
      if (isCloudMode()) {
        // G7.1 A: staged, resumable — validate -> create once -> upload once
        // -> retry completion only, all for the SAME job.
        const input = {
          file: state.file,
          projectName: state.projectName || 'Untitled Project',
          settings: state.settings,
          customPrompt: state.customPrompt,
          durationSecs: state.fileDurationSecs,
        }
        const invalid = validateCloudUpload(input)
        if (invalid) {
          setState((s) => ({ ...s, submitError: invalid }))
          return
        }
        const session = cloudSessionRef.current ?? new CloudUploadSession()
        cloudSessionRef.current = session
        const generation = flowGenerationRef.current
        const isCurrentRun = () => (
          mountedRef.current &&
          flowGenerationRef.current === generation &&
          cloudSessionRef.current === session
        )
        try {
          const { projectId, jobId } = await session.run(input, {
            onCreated: (ids) => {
              if (!isCurrentRun()) return
              // Publish durable identities before the S3 POST resolves, but
              // keep the AWAITING_UPLOAD reservation invisible.
              jobIdRef.current = ids.jobId
              setState((s) => ({ ...s, jobId: ids.jobId, newProjectId: ids.projectId }))
            },
            onUploaded: (ids) => {
              if (!isCurrentRun()) return
              // Source bytes now exist; keep this durable project visible if
              // completion must be retried or reconciliation runs later.
              registerCloudProject(ids.projectId, ids.projectVersion, ids.jobId)
            },
          })
          if (!isCurrentRun()) return
          jobIdRef.current = jobId
          fenceCloudProjectReconciliation()
          // Completion changes job status, not project metadata. Never replay
          // the create-time projectVersion over a concurrent rename/star N+1.
          updateProject(projectId, { status: 'processing', completionPending: false })
          setState((s) => ({ ...s, jobId, newProjectId: projectId, step: 5, progress: 0 }))
        } catch (err) {
          if (!isCurrentRun()) return
          // A dead job (gone or terminally conflicted) cannot be resumed —
          // the next Confirm starts a genuinely fresh session.
          if (
            err instanceof CloudApiError &&
            (err.category === 'not_found' || err.category === 'conflict')
          ) {
            cloudSessionRef.current = null
          }
          setState((s) => ({ ...s, submitError: submitErrorMessage(err, session.stage) }))
        }
        return
      }
      const { jobId, projectId } = await uploadApi.submitJob(
        state.file,
        state.projectName || 'Untitled Project',
        state.settings,
        state.fileDurationSecs,
      )
      jobIdRef.current = jobId
      setState((s) => ({ ...s, jobId, newProjectId: projectId, step: 5, progress: 0 }))
    } catch {
      if (mountedRef.current) {
        setState((s) => ({ ...s, submitError: 'Failed to start job. Please try again.' }))
      }
    } finally {
      submittingRef.current = false
    }
  }, [state.file, state.projectName, state.settings, state.customPrompt, state.fileDurationSecs, registerCloudProject, updateProject])

  const cancel = useCallback(() => {
    flowGenerationRef.current += 1
    objectUrlOwnerRef.current.clear()
    if (intervalRef.current) clearInterval(intervalRef.current)
    cloudSessionRef.current?.abandon()
    cloudSessionRef.current = null
    registeredRef.current = null
    setState(INITIAL)
  }, [])

  // Cloud polling — non-overlapping setTimeout chain, bounded tick budget,
  // stops on terminal state; updates the project by PROJECT id (G7 B3).
  useEffect(() => {
    if (!isCloudMode() || state.step !== 5 || !state.jobId) return
    let cancelled = false
    let ticks = 0
    let timer: ReturnType<typeof setTimeout> | null = null
    const projectId = state.newProjectId

    const tick = async () => {
      if (cancelled) return
      ticks += 1
      let terminal = false
      try {
        const job = await getCloudJob(jobIdRef.current ?? '')
        if (cancelled) return
        const ready = job.status === 'ready'
        const failed = job.status === 'failed'
        terminal = ready || failed
        setState((s) => ({
          ...s,
          progress: job.progress ?? 0,
          stage: job.stage ?? '',
          isReady: ready,
          isFailed: failed,
          failedError: failed ? 'Processing failed for this clip.' : null,
        }))
        if (ready && projectId) updateProject(projectId, { status: 'ready' })
        if (failed && projectId) updateProject(projectId, { status: 'failed' })
      } catch {
        // Transient poll failure: skip this tick silently (no raw errors).
      }
      if (cancelled || terminal) return
      if (ticks >= CLOUD_POLL_MAX_TICKS) {
        setState((s) => ({
          ...s,
          isFailed: true,
          failedError: 'Still processing after the polling window. Check the dashboard later.',
        }))
        return
      }
      timer = setTimeout(tick, CLOUD_POLL_MS)
    }
    timer = setTimeout(tick, 500) // immediate-ish first status check
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.step, state.jobId])

  // Legacy polling — active only on step 5
  useEffect(() => {
    if (isCloudMode()) return
    if (state.step !== 5 || !state.jobId) return

    intervalRef.current = setInterval(async () => {
      const jid = jobIdRef.current
      if (!jid) return

      let result: uploadApi.PollResult
      try {
        result = await uploadApi.pollStatus(jid)
      } catch (err) {
        // Network error — don't stop polling, just skip this tick
        console.warn('Poll error:', err)
        return
      }

      const { progress, status, stage, chunks_done, chunks_total,
              data_path, video_file, scene_count, tokens_used } = result

      setState((s) => ({
        ...s,
        progress,
        stage:       stage ?? '',
        chunksDone:  chunks_done ?? 0,
        chunksTotal: chunks_total ?? 0,
        isReady:     status === 'ready',
        isFailed:    status === 'failed',
        failedError: status === 'failed' ? 'Processing failed for this clip.' : null,
      }))

      if (status === 'ready' || status === 'failed') {
        if (intervalRef.current) clearInterval(intervalRef.current)
        if (status === 'ready' && state.newProjectId) {
          // Only patch fields the API actually returned — don't clobber an existing
          // videoFile with undefined if a stale server omits the field.
          const patch: Parameters<typeof updateProject>[1] = {
            status:     'ready',
            dataPath:   data_path ?? `/data/${state.newProjectId}`,
            sceneCount: scene_count,
            tokensUsed: tokens_used,
          }
          if (video_file) patch.videoFile = video_file
          updateProject(state.newProjectId, patch)
        }
        if (status === 'failed' && state.newProjectId) {
          updateProject(state.newProjectId, { status: 'failed' })
        }
      }
    }, 3000)

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.step, state.jobId])

  const estimatedTokens = estimateTokens(state.fileDurationSecs, state.settings)
  const estimatedMinutes = estimateMinutes(state.fileDurationSecs, state.settings)

  return {
    state, estimatedTokens, estimatedMinutes,
    setFile, setProjectName, setCustomPrompt, setSettings,
    next, back, submit, cancel,
  }
}
