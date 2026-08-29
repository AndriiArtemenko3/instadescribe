'use client'

import { useState, useEffect, useRef } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, CheckCircle2, Download, Loader2, X, Headphones, Eye, GraduationCap, RotateCcw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { Logo } from '@/components/ui/Logo'
import { SceneListPanel } from '../components/SceneListPanel'
import { VideoPanel } from '../components/VideoPanel'
import { ScriptPanel } from '../components/ScriptPanel'
import { CharactersPanel } from '../components/CharactersPanel'
import { QualityPanel } from '../components/QualityPanel'
import type { RightPanelTab } from '../components/RightPanelTabs'
import {
  fetchScenes, fetchAudioEvents, fetchAdGaps, fetchEntities, fetchOverrides,
  patchScene, patchEntity, requestExport, pollExport, exportDownloadUrl,
  type VoiceId, type ExportStatus, type ExportFmt,
} from '@/lib/api'
import { useAppStore } from '@/store/appStore'
import { sceneGapSecs, getSceneCollision, type SceneCollision } from '@/lib/collisions'
import { queryKeys } from '@/lib/queryKeys'
import { loadEdits, persistSceneText, persistSceneActive } from '@/lib/persistence'
import {
  isStudyMode, logEvent, resetDemo,
  hasSeenTour, setTourSeen, resetTour,
  getCompletedTasks, markTaskComplete,
} from '@/lib/session'
import { isCloudMode } from '@/lib/cloudMode'
import { isDemoBuild } from '@/lib/session'
import {
  fetchCloudOverrides,
  finishCloudReview,
  requestCloudTtsPreviewAudio,
  CloudApiError,
  type CloudPatchResponse,
  type CloudSceneOverride,
  type CloudSceneReviewCommand,
} from '@/lib/cloudApi'
import { CloudDraftSaveDisposedError, CloudSceneSaveCoordinator } from '@/lib/cloudDraftSave'
import { useCloudEditorData } from '../hooks/useCloudEditorData'
import { useCloudReviewLifecycle } from '../hooks/useCloudReviewLifecycle'
import { STUDY_TASKS } from '@/features/study/studyTasks'
import { EditorTour, type TourStep } from '@/features/study/EditorTour'
import { HelpPanel } from '@/features/study/HelpPanel'
import type { Scene } from '@/types'
import { isAppRouterRuntime } from '@/lib/runtimeEnv'
import { cloudDeliverableHref } from '@/lib/reviewLifecycle'
import type { BrowserRole } from '@/lib/reviewAccess'

const CLOUD_DEFERRED_COPY = 'Available in the Portfolio Strong release.'
const EMPTY_OVERRIDES: Record<string, CloudSceneOverride> = {}

interface EditorWorkspaceProps {
  projectId: string
  expectedJobId?: string
  staticFixture?: boolean
  backHref?: string
  browserRole?: BrowserRole
}

/** Retained React Router adapter for the Vite rollback entry point. */
export default function EditorPage({ staticFixture = false }: { staticFixture?: boolean }) {
  const { projectId = '' } = useParams<{ projectId: string }>()
  return <EditorWorkspace projectId={projectId} staticFixture={staticFixture} />
}

/** Browser-heavy editor island shared by the explicit Next review route. */
export function EditorWorkspace({
  projectId,
  expectedJobId,
  staticFixture = false,
  backHref = '/dashboard/projects',
  browserRole,
}: EditorWorkspaceProps) {
  const project = useAppStore((s) => {
    const candidate = s.projects.find((item) => item.id === projectId) ?? null
    if (expectedJobId && candidate?.jobId !== expectedJobId) return null
    return candidate
  })
  const queryClient = useQueryClient()

  // Demo/study/tutorial branch BEFORE any cloud token/manifest logic (G7 B4).
  // Only the registry-validated public route can set staticFixture.
  const cloud = isCloudMode() && !isDemoBuild() && !isStudyMode() && !staticFixture
  const appRouter = isAppRouterRuntime()
  const jobId = project?.jobId
  // Cloud editor readiness comes from a VALID manifest, not project.dataPath.
  const cloudEnabled = cloud && !!jobId && project?.status === 'ready'
  const cloudData = useCloudEditorData(projectId, jobId, cloudEnabled)
  const cloudLifecycle = useCloudReviewLifecycle(jobId, cloud && appRouter && !!jobId)
  const appReviewReadOnly = cloud && appRouter && (
    browserRole === 'viewer' || cloudLifecycle.review?.state !== 'open'
  )
  const canFinishReview = cloud && appRouter && (
    browserRole === 'owner' || browserRole === 'reviewer'
  ) && cloudLifecycle.review?.state === 'open'
  const cloudPreviewEnabled = cloud && appRouter && (
    browserRole === 'owner' || browserRole === 'editor' || browserRole === 'reviewer'
  ) && !!jobId && cloudLifecycle.review?.state === 'open'
  const [saveError, setSaveError] = useState<string | null>(null)
  const [reviewSavingSceneId, setReviewSavingSceneId] = useState<number | null>(null)
  const [overrideRefreshFailure, setOverrideRefreshFailure] = useState<{
    scope: string
    sceneKey: string
  } | null>(null)
  const cloudSaveCoordinatorRef = useRef<CloudSceneSaveCoordinator | null>(null)
  const cloudOverrideScope = cloud ? `${projectId}\u0000${jobId ?? ''}` : ''
  const acknowledgedOverridesRef = useRef<{
    scope: string
    overrides: Record<string, CloudSceneOverride>
  }>({ scope: cloudOverrideScope, overrides: {} })
  if (acknowledgedOverridesRef.current.scope !== cloudOverrideScope) {
    acknowledgedOverridesRef.current = { scope: cloudOverrideScope, overrides: {} }
  }

  function cloudSaveCoordinator(): CloudSceneSaveCoordinator {
    cloudSaveCoordinatorRef.current ??= new CloudSceneSaveCoordinator()
    return cloudSaveCoordinatorRef.current
  }

  useEffect(() => {
    // StrictMode deliberately runs setup/cleanup/setup on mount. Nulling the
    // disposed instance lets the second setup own a fresh coordinator.
    const coordinator = cloudSaveCoordinatorRef.current ?? new CloudSceneSaveCoordinator()
    cloudSaveCoordinatorRef.current = coordinator
    return () => {
      coordinator.dispose()
      if (cloudSaveCoordinatorRef.current === coordinator) {
        cloudSaveCoordinatorRef.current = null
      }
    }
  }, [])

  const hasData = cloud ? !!cloudData.manifest : !!project?.dataPath

  // ── Study mode ────────────────────────────────────────────────────────────
  const study = isStudyMode()
  const loggedScenesRef = useRef<Set<number>>(new Set())
  const patchTimersRef = useRef<Record<number, number>>({})
  const [completed, setCompleted] = useState<Set<string>>(() => new Set(getCompletedTasks()))
  const [appliedSceneId, setAppliedSceneId] = useState<number | null>(null)
  const appliedTimer = useRef<number | null>(null)
  const [tourOpen, setTourOpen] = useState(false)
  const [helpOpen, setHelpOpen] = useState(false)
  const [previewOpen, setPreviewOpen] = useState(false)
  const [previewState, setPreviewState] = useState<ExportStatus | null>(null)
  const [previewVideoUrl, setPreviewVideoUrl] = useState<string | null>(null)
  const [previewExportId, setPreviewExportId] = useState<string | null>(null)
  const previewTimer = useRef<number | null>(null)

  const { data: legacyRawScenes = [], isLoading: legacyScenesLoading } = useQuery({
    queryKey: queryKeys.scenes(projectId),
    queryFn: () => fetchScenes(projectId),
    enabled: !cloud && !!projectId && hasData,
    retry: false,
  })

  const { data: legacyAudioEvents = [] } = useQuery({
    queryKey: queryKeys.audioEvents(projectId),
    queryFn: () => fetchAudioEvents(projectId),
    enabled: !cloud && !!projectId && hasData,
    retry: false,
  })

  const { data: legacyAdGaps = [] } = useQuery({
    queryKey: queryKeys.adGaps(projectId),
    queryFn: () => fetchAdGaps(projectId),
    enabled: !cloud && !!projectId && hasData,
    retry: false,
  })

  const { data: legacyEntities = [] } = useQuery({
    queryKey: queryKeys.entities(projectId),
    queryFn: () => fetchEntities(projectId),
    enabled: !cloud && !!projectId && hasData,
    retry: false,
  })

  const overridesQuery = useQuery({
    // G7.1 C: every cloud key carries BOTH stable IDs so a project
    // reconciled to a NEW processing job can never reuse an earlier job's
    // cached data.
    queryKey: cloud
      ? queryKeys.cloudOverrides(projectId, jobId ?? '')
      : queryKeys.overrides(projectId),
    // Cloud overrides resolve projectId -> the stored jobId (G7 B5).
    queryFn: async () => {
      if (!cloud) return fetchOverrides(projectId)
      const fetched = await fetchCloudOverrides(jobId!)
      const acknowledged = acknowledgedOverridesRef.current
      if (acknowledged.scope !== cloudOverrideScope) return fetched
      const merged = { ...fetched }
      for (const [sceneKey, override] of Object.entries(acknowledged.overrides)) {
        merged[sceneKey] = { ...(merged[sceneKey] ?? {}), ...override }
      }
      return merged
    },
    enabled: !!projectId && hasData && (!cloud || !!jobId),
    retry: false,
  })
  const serverOverrides = overridesQuery.data ?? EMPTY_OVERRIDES
  // The legacy and cloud query branches share one hook invocation, so the
  // inferred result is a union. Past this explicit cloud boundary, only the
  // exact versioned cloud contract is legal.
  const cloudOverrides = cloud
    ? serverOverrides as Record<string, CloudSceneOverride>
    : EMPTY_OVERRIDES

  const rawScenes = cloud ? cloudData.rawScenes : legacyRawScenes
  const audioEvents = cloud ? cloudData.audioEvents : legacyAudioEvents
  const adGaps = cloud ? cloudData.adGaps : legacyAdGaps
  const entities = cloud ? cloudData.entities : legacyEntities
  const scenesLoading = cloud ? cloudData.scenesLoading : legacyScenesLoading

  const [scenes, setScenes] = useState<Scene[]>([])
  const [finishSaving, setFinishSaving] = useState(false)

  const decidedSceneCount = scenes.filter((scene) => {
    const status = cloudOverrides[scene.sceneKey]?.reviewStatus
    return status === 'approved' || status === 'rejected'
  }).length
  const approvedSceneCount = scenes.filter(
    (scene) => cloudOverrides[scene.sceneKey]?.reviewStatus === 'approved',
  ).length
  const reviewComplete = scenes.length > 0 && decidedSceneCount === scenes.length

  async function handleFinishReview() {
    if (!jobId || !reviewComplete || finishSaving || !canFinishReview) return
    const zeroAdConfirmed = approvedSceneCount === 0
    if (zeroAdConfirmed && !window.confirm(
      'No scenes are approved. Finish as a zero-audio-description review and create the neutral deliverable set?',
    )) return
    setFinishSaving(true)
    setSaveError(null)
    try {
      await finishCloudReview(jobId, zeroAdConfirmed)
      await cloudLifecycle.refreshAfterFinish()
      setFinishSaving(false)
    } catch (error) {
      setSaveError(error instanceof CloudApiError
        ? `Finish Review failed (${error.code ?? error.category}).`
        : 'Finish Review failed.')
      setFinishSaving(false)
    }
  }

  useEffect(() => {
    if (!rawScenes.length) return
    // Cloud drafts are scoped to the PROCESSING JOB: a project reconciled to
    // a new jobId must never replay an earlier job's positional drafts.
    const edits = loadEdits(projectId, cloud ? jobId : undefined)
    setScenes(
      rawScenes.map((s) => {
        const local = edits.scenes[s.id]
        // Cloud merges by the EXACT canonical pipeline scene id; legacy and
        // demo/study keep their existing positional identity behavior.
        const remote = serverOverrides[cloud ? s.sceneKey : `scene_${s.sceneNumber}`] ?? {}
        // A session draft is the newest local user intent. It must win over
        // the last acknowledged server override after remount/reload.
        const text = local?.text ?? remote.ad ?? s.text
        // Study mode: scenes load inactive until the participant reviews and
        // activates each one. Non-study app keeps the pipeline default (active).
        const active = local?.active ?? remote.active ?? (study ? false : s.active)
        const locked = remote.locked ?? s.locked
        const voiceId = remote.voice ?? s.voiceId
        const voiceSpeed = remote.speed ?? s.voiceSpeed ?? 1.0
        return { ...s, text, active, locked, voiceId, voiceSpeed }
      })
    )
  }, [rawScenes, projectId, jobId, serverOverrides, study, cloud])

  const [activeSceneId, setActiveSceneId] = useState<number | null>(null)
  const [currentTime, setCurrentTime] = useState(0)
  const [rightTab, setRightTab] = useState<RightPanelTab>('script')

  const activeScene = scenes.find((s) => s.id === activeSceneId) ?? scenes[0] ?? null
  const activeCloudOverride = cloud && activeScene
    ? cloudOverrides[activeScene.sceneKey]
    : undefined
  const activeCloudDraft = cloud && activeScene
    ? loadEdits(projectId, jobId).scenes[activeScene.id]
    : undefined
  const activeOverrideRefreshFailed = !!(
    activeScene &&
    overrideRefreshFailure?.scope === cloudOverrideScope &&
    overrideRefreshFailure.sceneKey === activeScene.sceneKey
  )
  const cloudReviewUnavailable = cloud && (
    activeOverrideRefreshFailed || overridesQuery.isError || overridesQuery.isRefetchError
  )
  const cloudReviewLoading = cloud && !cloudReviewUnavailable && !overridesQuery.isSuccess
  // `generated` is evidence from a successful authoritative map in which the
  // exact key is absent. It is never a loading/error fallback.
  const activeCloudReviewStatus = cloud && !cloudReviewLoading && !cloudReviewUnavailable
    ? (activeCloudOverride?.reviewStatus ?? 'generated')
    : undefined

  // Study mode: track how many scenes the participant has activated so far.
  const activatedCount = scenes.filter((s) => s.active).length
  const noneActive = study && scenes.length > 0 && activatedCount === 0

  // Guided-task completion: union of action-recorded ticks with live server
  // state, so rename/activate stay ticked even after a reload re-derives them.
  const taskDone: Record<string, boolean> = {
    rename:    completed.has('rename')    || entities.some((e) => e.user_renamed),
    activate:  completed.has('activate')  || activatedCount > 0,
    apply:     completed.has('apply'),
    voiceline: completed.has('voiceline'),
    preview:   completed.has('preview'),
  }
  const doneCount = STUDY_TASKS.filter((t) => taskDone[t.id]).length

  // Walkthrough steps. Each page points at a region tagged with a data-tour
  // attribute below; the spotlight moves between pages within a step. onEnter
  // switches the right panel so the highlighted target is on screen.
  const tourSteps: TourStep[] = [
    {
      title: 'Scene Panel',
      pages: [
        {
          selector: '[data-tour="scenes"]',
          body: "This panel lists every audio description line for the clip. Click a scene's plus icon to turn it on, or its tick to turn it off. If a line talks over the dialogue, a Conflict warning helps you find a better spot.",
        },
      ],
    },
    {
      title: 'Script Edit Panel',
      onEnter: () => setRightTab('script'),
      pages: [
        {
          selector: '[data-tour="script-edit"]',
          body: "Edit the audio description here, or use Smart Fill when it's offered to shorten the line so it fits the clip.",
        },
        {
          selector: '[data-tour="script-controls"]',
          body: 'Choose a voice and set the speed, press Preview to hear the line, then click Apply to export at the bottom when you are happy with it.',
        },
      ],
    },
    {
      title: 'Characters Tab',
      onEnter: () => setRightTab('characters'),
      pages: [
        {
          selector: '[data-tour="script"]',
          body: 'Open the Characters tab to rename a character, and the script updates on its own. If you rename someone, activate those scenes again afterwards, so it is best to set names before you start.',
        },
      ],
    },
    {
      title: 'Video Player and Timeline',
      onEnter: () => setRightTab('script'),
      pages: [
        {
          selector: '[data-tour="video-player"]',
          body: 'Play the original clip to get a feel for how it looks and sounds.',
        },
        {
          selector: '[data-tour="video-timeline"]',
          body: 'The timeline marks safe placement areas in green and dialogue in blue. When a description collides, the affected dialogue turns red. Click anywhere on it to jump to that moment.',
        },
      ],
    },
    {
      title: 'Preview with audio',
      pages: [
        {
          selector: '[data-tour="preview"]',
          body: 'When your changes are ready, click here to render the video with your audio description mixed in. It takes about 30 seconds. Then close your eyes and listen.',
        },
      ],
    },
  ]

  function closeTour() {
    setTourSeen()
    setTourOpen(false)
    setRightTab('script')
    logEvent('tour_done')
  }

  function replayTour() {
    resetTour()
    setHelpOpen(false)
    setTourOpen(true)
    logEvent('tour_replay')
  }

  // Fire the walkthrough once, after the editor regions have laid out, only when
  // the participant has not seen it before.
  useEffect(() => {
    if (!study || !hasData || scenesLoading || hasSeenTour()) return
    const t = window.setTimeout(() => setTourOpen(true), 300)
    return () => window.clearTimeout(t)
  }, [study, hasData, scenesLoading])

  // Real silence available to the active scene: the largest AD gap that overlaps
  // its time window, or 0 when none intersects. No fallback to scene duration, so
  // Smart Fill stays disabled when there is no genuine gap to shorten into.
  const availableGapSecs = activeScene ? sceneGapSecs(activeScene, adGaps) : 0

  // Per-scene collision check: which active, non-empty scenes overrun their gap.
  const collisionsBySceneId: Record<number, SceneCollision> = {}
  for (const s of scenes) collisionsBySceneId[s.id] = getSceneCollision(s, audioEvents, adGaps)
  const activeCollision = activeScene ? collisionsBySceneId[activeScene.id] ?? null : null

  function handleSceneSelect(scene: Scene) {
    setActiveSceneId(scene.id)
    setCurrentTime(scene.startSecs)
  }

  // Mark a guided task complete (idempotent, persisted per session). The Help
  // panel checklist reflects these as they happen.
  function markTask(id: string) {
    if (!study) return
    setCompleted((prev) => (prev.has(id) ? prev : new Set(markTaskComplete(id))))
  }

  function acknowledgeCloudSave(expectedSceneKey: string, response: CloudPatchResponse) {
    if (
      response.projectId !== projectId ||
      response.jobId !== jobId ||
      response.sceneId !== expectedSceneKey
    ) {
      throw new Error('cloud scene acknowledgement identity mismatch')
    }
    const current = acknowledgedOverridesRef.current
    if (current.scope !== cloudOverrideScope) {
      throw new Error('cloud scene acknowledgement scope changed')
    }
    const acknowledged = {
      ...(current.overrides[expectedSceneKey] ?? {}),
      ...response.override,
    }
    acknowledgedOverridesRef.current = {
      scope: current.scope,
      overrides: { ...current.overrides, [expectedSceneKey]: acknowledged },
    }
    queryClient.setQueryData<Record<string, CloudSceneOverride>>(
      queryKeys.cloudOverrides(projectId, jobId!),
      (cached) => ({
        ...(cached ?? {}),
        [expectedSceneKey]: {
          ...(cached?.[expectedSceneKey] ?? {}),
          ...acknowledged,
        },
      }),
    )
    setOverrideRefreshFailure((failure) => (
      failure?.scope === cloudOverrideScope && failure.sceneKey === expectedSceneKey
        ? null
        : failure
    ))
  }

  function cloudExpectedVersion(sceneKey: string): number {
    const acknowledged = acknowledgedOverridesRef.current.overrides[sceneKey]
    return acknowledged?.version ?? cloudOverrides[sceneKey]?.version ?? 0
  }

  async function refreshCloudOverrideAfterConflict(sceneKey: string): Promise<boolean> {
    const requestedScope = cloudOverrideScope
    const current = acknowledgedOverridesRef.current
    if (current.scope === cloudOverrideScope && sceneKey in current.overrides) {
      const next = { ...current.overrides }
      delete next[sceneKey]
      acknowledgedOverridesRef.current = { scope: current.scope, overrides: next }
    }
    if (!jobId) return false
    const key = queryKeys.cloudOverrides(projectId, jobId)
    // Cancel/ignore an older query, then make a request that starts after the
    // 409. Only a successfully validated response is installed as evidence.
    let fetched: Record<string, CloudSceneOverride>
    try {
      await queryClient.cancelQueries({ queryKey: key, exact: true })
      fetched = await fetchCloudOverrides(jobId)
    } catch {
      setOverrideRefreshFailure({ scope: requestedScope, sceneKey })
      return false
    }
    if (acknowledgedOverridesRef.current.scope !== requestedScope) return false
    const acknowledged = acknowledgedOverridesRef.current
    const merged = { ...fetched }
    if (acknowledged.scope === requestedScope) {
      for (const [key, override] of Object.entries(acknowledged.overrides)) {
        merged[key] = { ...(merged[key] ?? {}), ...override }
      }
    }
    queryClient.setQueryData(key, merged)
    setOverrideRefreshFailure((failure) => (
      failure?.scope === requestedScope && failure.sceneKey === sceneKey
        ? null
        : failure
    ))
    return true
  }

  async function reportCloudSaveFailure(
    error: unknown,
    sceneKey: string,
    hasDraftBackedField: boolean,
    fallback: string,
  ): Promise<void> {
    if (error instanceof CloudDraftSaveDisposedError) return
    if (error instanceof CloudApiError && error.code === 'stale_version') {
      const refreshed = await refreshCloudOverrideAfterConflict(sceneKey)
      setSaveError(
        refreshed
          ? hasDraftBackedField
            ? 'This scene changed elsewhere. The latest review state was loaded; your draft remains in this session. Review it and retry.'
            : 'This scene changed elsewhere. The latest review state was loaded; retry your change.'
          : hasDraftBackedField
            ? 'This scene changed elsewhere, but the latest review state could not be loaded. Your draft remains in this session; refresh before retrying.'
            : 'This scene changed elsewhere, but the latest review state could not be loaded. Refresh before retrying.',
      )
      return
    }
    setSaveError(
      error instanceof CloudApiError && error.category === 'auth'
        ? 'Save failed: access token missing or not accepted.'
        : fallback,
    )
  }

  // One save path (G7 B5): cloud PATCHes the EXACT retained pipeline scene
  // id via the stored jobId; a failure is VISIBLE (inline banner), never
  // only console.error. Legacy/study keep their existing behavior.
  function saveScene(sceneId: number, patch: Parameters<typeof patchScene>[2]) {
    if (appReviewReadOnly) return
    if (cloud) {
      const target = scenes.find((s) => s.id === sceneId)
      if (!target || !jobId) return
      const hasDraftBackedField = typeof patch.ad === 'string' || typeof patch.active === 'boolean'
      cloudSaveCoordinator().save(
        projectId,
        jobId,
        sceneId,
        target.sceneKey,
        patch,
        cloudExpectedVersion(target.sceneKey),
        (response) => acknowledgeCloudSave(target.sceneKey, response),
      )
        .then(({ latest }) => {
          if (!latest) return
          setSaveError(null)
        })
        .catch((error) => reportCloudSaveFailure(
          error,
          target.sceneKey,
          hasDraftBackedField,
          hasDraftBackedField
            ? 'Save failed. Unsaved description text or active-state changes remain in this session; retry the action.'
            : 'Save failed. This setting was not applied; retry the action.',
        ))
      return
    }
    patchScene(projectId, sceneId, patch).catch(console.error)
  }

  function handleAdChange(sceneId: number, text: string) {
    if (appReviewReadOnly) return
    setScenes((prev) => prev.map((s) => (s.id === sceneId ? { ...s, text } : s)))
    persistSceneText(projectId, sceneId, text, cloud ? jobId : undefined)
    if (study) {
      if (!loggedScenesRef.current.has(sceneId)) {
        loggedScenesRef.current.add(sceneId)
        logEvent('edit_ad_line', { sceneId })
      }
      // Debounced server sync so the eyes-closed preview reflects the edit.
      const timers = patchTimersRef.current
      if (timers[sceneId]) window.clearTimeout(timers[sceneId])
      timers[sceneId] = window.setTimeout(() => {
        patchScene(projectId, sceneId, { ad: text }).catch(console.error)
      }, 600)
    }
  }

  function handleActiveToggle(sceneId: number) {
    if (appReviewReadOnly) return
    const target = scenes.find((scene) => scene.id === sceneId)
    if (!target) return
    const next = !target.active
    setScenes((prev) => prev.map((s) => (s.id === sceneId ? { ...s, active: next } : s)))
    persistSceneActive(projectId, sceneId, next, cloud ? jobId : undefined)
    saveScene(sceneId, { active: next })
    if (study) {
      logEvent('toggle_scene', { sceneId, active: next })
      if (next) markTask('activate')
    }
  }

  function handleApply(sceneId: number) {
    if (appReviewReadOnly) return
    const target = scenes.find((s) => s.id === sceneId)
    if (!target) return
    const applyPatch = {
      ad: target.text,
      active: true,
      voice: (target.voiceId as VoiceId | undefined) ?? 'onyx',
      speed: target.voiceSpeed ?? 1.0,
    }
    if (cloud) {
      if (!jobId) return
      cloudSaveCoordinator().save(
        projectId,
        jobId,
        sceneId,
        target.sceneKey,
        applyPatch,
        cloudExpectedVersion(target.sceneKey),
        (response) => acknowledgeCloudSave(target.sceneKey, response),
      )
        .then(({ latest }) => {
          if (!latest) return
          setSaveError(null)
          const remaining = loadEdits(projectId, jobId).scenes[sceneId]
          // Do not imply the currently visible newer draft was included in
          // an older successful request. Normal no-race Apply shows success.
          if (remaining?.text !== undefined || remaining?.active !== undefined) return
          setAppliedSceneId(sceneId)
          if (appliedTimer.current) window.clearTimeout(appliedTimer.current)
          appliedTimer.current = window.setTimeout(() => setAppliedSceneId(null), 3000)
        })
        .catch((error) => reportCloudSaveFailure(
          error,
          target.sceneKey,
          true,
          'Save failed. Unsaved description text or active-state changes remain in this session; press Apply again.',
        ))
      return
    }
    patchScene(projectId, sceneId, applyPatch)
      .then(() => {
        if (!study) return
        markTask('apply')
        setAppliedSceneId(sceneId)
        if (appliedTimer.current) window.clearTimeout(appliedTimer.current)
        appliedTimer.current = window.setTimeout(() => setAppliedSceneId(null), 2200)
      })
      .catch((err) => console.error('apply failed', err))
  }

  function handleVoiceChange(sceneId: number, voice: VoiceId) {
    if (appReviewReadOnly) return
    setScenes((prev) => prev.map((s) => (s.id === sceneId ? { ...s, voiceId: voice } : s)))
    saveScene(sceneId, { voice })
  }

  function handleSpeedChange(sceneId: number, speed: number) {
    if (appReviewReadOnly) return
    setScenes((prev) => prev.map((s) => (s.id === sceneId ? { ...s, voiceSpeed: speed } : s)))
    saveScene(sceneId, { speed })
  }

  function handleLockedChange(sceneId: number, locked: boolean) {
    if (appReviewReadOnly) return
    setScenes((prev) => prev.map((s) => (s.id === sceneId ? { ...s, locked } : s)))
    saveScene(sceneId, { locked })
  }

  function handleReview(sceneId: number, reviewStatus: Exclude<CloudSceneReviewCommand, 'edited'>) {
    if (!cloud || !jobId || appReviewReadOnly) return
    const target = scenes.find((scene) => scene.id === sceneId)
    if (!target) return
    setReviewSavingSceneId(sceneId)
    cloudSaveCoordinator().save(
      projectId,
      jobId,
      sceneId,
      target.sceneKey,
      {
        ad: target.text,
        active: target.active,
        locked: target.locked,
        voice: target.voiceId ?? 'onyx',
        speed: target.voiceSpeed ?? 1,
        reviewStatus,
      },
      cloudExpectedVersion(target.sceneKey),
      (response) => acknowledgeCloudSave(target.sceneKey, response),
    )
      .then(({ latest }) => {
        if (latest) setSaveError(null)
      })
      .catch((error) => reportCloudSaveFailure(
        error,
        target.sceneKey,
        true,
        'Review decision failed. Your description draft remains in this session; retry the action.',
      ))
      .finally(() => setReviewSavingSceneId((current) => current === sceneId ? null : current))
  }

  function handleCloudPreview(
    sceneKey: string,
    text: string,
    voice: VoiceId,
    speed: number,
    signal: AbortSignal,
  ): Promise<Blob> {
    if (!jobId || !cloudPreviewEnabled) return Promise.reject(new CloudApiError('auth'))
    return requestCloudTtsPreviewAudio(jobId, sceneKey, text, voice, speed, { signal })
  }

  async function performRename(characterId: string, newName: string) {
    if (cloud) {
      setSaveError(`Character rename: ${CLOUD_DEFERRED_COPY}`)
      return
    }
    await patchEntity(projectId, characterId, newName)
    await queryClient.invalidateQueries({ queryKey: queryKeys.entities(projectId) })
    await queryClient.invalidateQueries({ queryKey: queryKeys.scenes(projectId) })
    if (study) {
      logEvent('rename_character', { characterId })
      markTask('rename')
    }
  }

  // ── Study: eyes-closed preview + finish ─────────────────────────────────────
  async function startPreview() {
    setPreviewOpen(true)
    setPreviewVideoUrl(null)
    setPreviewState({ status: 'queued', progress: 0, stage: 'queued' })
    logEvent('preview_start')
    markTask('preview')
    try {
      // Flush current edits to the server so the mix reflects them.
      await Promise.all(
        scenes.map((s) =>
          patchScene(projectId, s.id, { ad: s.text, active: s.active }).catch(() => {}),
        ),
      )
      const { exportId: eid } = await requestExport(projectId, 'onyx', 'mp4')
      setPreviewExportId(eid)
      const poll = async () => {
        try {
          const st = await pollExport(projectId, eid)
          setPreviewState(st)
          if (st.status === 'ready') {
            setPreviewVideoUrl(exportDownloadUrl(projectId, eid, true))
            logEvent('preview_ready')
          } else if (st.status === 'processing' || st.status === 'queued') {
            previewTimer.current = window.setTimeout(poll, 2000)
          }
        } catch (err) {
          setPreviewState({
            status: 'failed', progress: 0, stage: 'error',
            error: err instanceof Error ? err.message : String(err),
          })
        }
      }
      poll()
    } catch (err) {
      setPreviewState({
        status: 'failed', progress: 0, stage: 'error',
        error: err instanceof Error ? err.message : String(err),
      })
    }
  }

  function closePreview() {
    if (previewTimer.current) window.clearTimeout(previewTimer.current)
    setPreviewOpen(false)
    setPreviewState(null)
    setPreviewVideoUrl(null)
    setPreviewExportId(null)
  }

  function restartDemo() {
    logEvent('restart_demo')
    resetDemo()
    window.location.href = '/study'
  }

  async function handleRenameRequest(characterId: string, currentName: string) {
    const next = window.prompt('Rename character', currentName)?.trim()
    if (!next || next === currentName) return
    try {
      await performRename(characterId, next)
    } catch (err) {
      window.alert(`Rename failed: ${err instanceof Error ? err.message : err}`)
    }
  }

  // ── Export modal state ────────────────────────────────────────────────────
  const [exportOpen, setExportOpen] = useState(false)
  const [exportVoice, setExportVoice] = useState<VoiceId>('onyx')
  const [exportFormat, setExportFormat] = useState<ExportFmt>('mp4')
  const [exportState, setExportState] = useState<ExportStatus | null>(null)
  const [exportId, setExportId] = useState<string | null>(null)
  const pollTimer = useRef<number | null>(null)

  useEffect(() => {
    return () => {
      if (pollTimer.current) window.clearTimeout(pollTimer.current)
      if (previewTimer.current) window.clearTimeout(previewTimer.current)
      if (appliedTimer.current) window.clearTimeout(appliedTimer.current)
    }
  }, [])

  async function startExport() {
    setExportState({ status: 'queued', progress: 0, stage: 'queued' })
    try {
      const { exportId: eid } = await requestExport(projectId, exportVoice, exportFormat)
      setExportId(eid)
      const poll = async () => {
        try {
          const s = await pollExport(projectId, eid)
          setExportState(s)
          if (s.status === 'processing' || s.status === 'queued') {
            pollTimer.current = window.setTimeout(poll, 2000)
          }
        } catch (err) {
          setExportState({
            status: 'failed', progress: 0, stage: 'error',
            error: err instanceof Error ? err.message : String(err),
          })
        }
      }
      poll()
    } catch (err) {
      setExportState({
        status: 'failed', progress: 0, stage: 'error',
        error: err instanceof Error ? err.message : String(err),
      })
    }
  }

  function closeExport() {
    if (pollTimer.current) window.clearTimeout(pollTimer.current)
    setExportOpen(false)
    setExportState(null)
    setExportId(null)
  }

  function triggerDownload() {
    if (!exportId) return
    window.location.href = exportDownloadUrl(projectId, exportId)
  }

  const duration = project?.durationSecs ?? 0

  if (!project) {
    return (
      <div className="flex h-screen items-center justify-center bg-neutral-50">
        <p className="text-sm text-neutral-500">Project not found.</p>
      </div>
    )
  }

  if (!hasData) {
    const isProcessing = project.status === 'processing'
    const isFailed     = project.status === 'failed'
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-3 bg-neutral-50 text-center">
        {isProcessing && (
          <>
            <div className="h-6 w-6 animate-spin rounded-full border-2 border-neutral-300 border-t-neutral-700" />
            <p className="text-sm font-medium text-neutral-700">Pipeline is still processing…</p>
            <p className="text-xs text-neutral-400">Come back when it finishes. The Projects page shows current status.</p>
          </>
        )}
        {isFailed && (
          <>
            <p className="text-sm font-medium text-danger-500">Pipeline failed</p>
            <p className="text-xs text-neutral-400">Check the server logs for details.</p>
          </>
        )}
        {!isProcessing && !isFailed && (
          <p className="text-sm text-neutral-500">No pipeline output connected to this project.</p>
        )}
      </div>
    )
  }

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-neutral-50">
      <header className="flex h-topnav shrink-0 items-center gap-3 border-b border-neutral-200 bg-neutral-0 px-4">
        {study ? (
          <button
            onClick={restartDemo}
            className="flex items-center gap-1.5 text-xs text-neutral-400 hover:text-neutral-700 transition-colors"
            title="Reset the demo to a clean state"
          >
            <RotateCcw size={14} />
            Restart demo
          </button>
        ) : staticFixture ? (
          <a href="/tutorials" className="text-neutral-400 hover:text-neutral-700 transition-colors">
            <ArrowLeft size={16} />
          </a>
        ) : (
          <a href={backHref} className="text-neutral-400 hover:text-neutral-700 transition-colors">
            <ArrowLeft size={16} />
          </a>
        )}
        <Separator orientation="vertical" className="h-4" />
        <Logo size={18} className="text-brand-400" />
        <span className="text-sm font-medium text-neutral-900 truncate max-w-xs">
          {project?.name ?? projectId}
        </span>
        {scenesLoading && <Skeleton className="h-4 w-24 ml-2" />}
        <div className="flex-1" />
        {study ? (
          <>
            {scenes.length > 0 && (
              <span className="mr-1 text-xs tabular-nums text-neutral-500">
                {activatedCount} / {scenes.length} activated
              </span>
            )}
            <span data-tour="preview" title={noneActive ? 'Activate at least one scene first.' : undefined}>
              <Button
                variant="default"
                size="sm"
                className="gap-2"
                disabled={noneActive}
                onClick={startPreview}
              >
                <Headphones size={14} />
                Preview with audio
              </Button>
            </span>
          </>
        ) : cloud && appRouter && cloudLifecycle.review?.state === 'open' ? (
          <span className="flex items-center gap-2">
            {browserRole === 'viewer' ? (
              <span className="text-xs text-neutral-400">Read-only review</span>
            ) : !reviewComplete ? (
              <span className="text-xs text-neutral-400">
                Decide every scene ({decidedSceneCount}/{scenes.length})
              </span>
            ) : null}
            {reviewComplete && !canFinishReview && (
              <span className="text-xs text-neutral-400">
                An Owner or Reviewer must finish this review.
              </span>
            )}
            {(browserRole === 'owner' || browserRole === 'reviewer') && (
              <Button
                variant="default"
                size="sm"
                className="gap-2"
                disabled={!reviewComplete || finishSaving || !canFinishReview}
                onClick={handleFinishReview}
              >
                {finishSaving ? <Loader2 size={14} className="animate-spin" /> : <CheckCircle2 size={14} />}
                Finish Review
              </Button>
            )}
          </span>
        ) : cloud && appRouter ? (
          <span className="text-xs font-medium text-neutral-500">
            {cloudLifecycle.review?.state === 'completed'
              ? cloudLifecycle.render?.state === 'completed'
                ? 'Deliverables ready'
                : `Render ${cloudLifecycle.render?.state ?? 'queued'}`
              : cloudLifecycle.review?.state === 'expired'
                ? 'Review expired'
                : 'Loading review…'}
          </span>
        ) : cloud ? (
          <span className="flex items-center gap-2">
            <span id="export-deferred-note" className="text-xs text-neutral-400">
              Coming in v0.2
            </span>
            <Button
              variant="outline"
              size="sm"
              className="gap-2"
              disabled
              aria-describedby="export-deferred-note"
            >
              <Download size={14} />
              Export
            </Button>
          </span>
        ) : (
          <Button
            variant="outline"
            size="sm"
            className="gap-2"
            onClick={() => setExportOpen(true)}
          >
            <Download size={14} />
            Export
          </Button>
        )}
      </header>

      {saveError && (
        <div
          role="alert"
          className="flex shrink-0 items-center gap-2 border-b border-danger-200 bg-danger-50 px-4 py-2"
        >
          <p className="text-xs font-medium text-danger-500">{saveError}</p>
          <button
            className="ml-auto text-xs text-neutral-500 hover:text-neutral-700"
            onClick={() => setSaveError(null)}
          >
            Dismiss
          </button>
        </div>
      )}

      {cloud && appRouter && cloudLifecycle.unavailable && (
        <div role="alert" className="shrink-0 border-b border-warning-200 bg-warning-50 px-4 py-2 text-xs text-neutral-700">
          Review status is temporarily unavailable. Editing remains locked until the server can confirm it.
        </div>
      )}

      {cloud && appRouter && cloudLifecycle.review?.state === 'open' && (() => {
        const remaining = Date.parse(cloudLifecycle.review.expiresAt) - Date.now()
        if (!(remaining > 0 && remaining <= 7 * 24 * 60 * 60 * 1000)) return null
        const days = Math.max(1, Math.ceil(remaining / (24 * 60 * 60 * 1000)))
        return (
          <div role="status" className="shrink-0 border-b border-warning-200 bg-warning-50 px-4 py-2 text-xs text-neutral-700">
            This unfinished review expires after {days} {days === 1 ? 'day' : 'days'} of remaining inactivity. Any saved scene change renews the 30-day window.
          </div>
        )
      })()}

      {cloud && appRouter && cloudLifecycle.review?.state === 'expired' && (
        <div role="alert" className="shrink-0 border-b border-danger-200 bg-danger-50 px-4 py-2 text-xs text-danger-500">
          This review expired after 30 days of inactivity and can no longer be edited.
        </div>
      )}

      {cloud && appRouter && cloudLifecycle.review?.state === 'completed' && (
        <div className="shrink-0 border-b border-brand-200 bg-brand-50 px-4 py-3">
          <div className="flex flex-wrap items-center gap-3">
            <div>
              <p className="text-xs font-semibold text-neutral-900">
                {cloudLifecycle.render?.state === 'completed'
                  ? 'All five deliverables are ready.'
                  : cloudLifecycle.render?.state === 'failed'
                    ? 'Rendering failed.'
                    : cloudLifecycle.render?.state === 'cancelled'
                      ? 'Rendering was cancelled.'
                      : 'Review locked. Rendering all five deliverables…'}
              </p>
              <p className="mt-0.5 text-[11px] text-neutral-500">
                {cloudLifecycle.render?.state === 'completed'
                  ? 'Each link requests a fresh version-pinned S3 download.'
                  : 'This page polls the durable render state; a terminal webhook is delivered independently.'}
              </p>
            </div>
            {cloudLifecycle.deliverables.length === 5 && (
              <div className="ml-auto flex flex-wrap gap-2">
                {cloudLifecycle.deliverables.map((deliverable) => (
                  <a
                    key={deliverable.id}
                    href={cloudDeliverableHref(deliverable.id)}
                    download={deliverable.fileName}
                    className="inline-flex items-center gap-1.5 rounded-md border border-brand-200 bg-white px-2.5 py-1.5 text-xs font-medium uppercase text-brand-500 hover:bg-brand-50"
                  >
                    <Download size={12} />
                    {deliverable.kind}
                  </a>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {study && (
        <div className="shrink-0 border-b border-brand-200 bg-brand-50 px-4 py-2" data-tour="help">
          <div className="flex items-center gap-3">
            <span className="text-sm font-semibold text-neutral-900">Try InstaDescribe:</span>
            <Button
              variant="outline"
              size="sm"
              className="gap-1.5 bg-neutral-0"
              onClick={() => setHelpOpen(true)}
            >
              <GraduationCap size={14} />
              Show me how
            </Button>
            <span className="ml-auto text-xs tabular-nums text-neutral-500">
              {doneCount} / {STUDY_TASKS.length} done
            </span>
          </div>
        </div>
      )}

      <div className="flex flex-1 overflow-hidden">
        <div data-tour="scenes" className="flex">
          <SceneListPanel
            scenes={scenes}
            activeSceneId={activeScene?.id ?? null}
            onSceneSelect={handleSceneSelect}
            onActiveToggle={handleActiveToggle}
            collisions={collisionsBySceneId}
            loading={scenesLoading}
            readOnly={appReviewReadOnly}
          />
        </div>

        <div data-tour="video" className="flex min-w-0 flex-1 overflow-hidden">
          <VideoPanel
            projectId={projectId}
            videoSrc={cloud ? cloudData.videoUrl : project?.videoFile}
            duration={duration}
            scenes={scenes}
            adGaps={adGaps}
            audioEvents={audioEvents}
            collisions={collisionsBySceneId}
            currentTime={currentTime}
            onSeek={setCurrentTime}
            onTimeUpdate={setCurrentTime}
          />
        </div>

        <div data-tour="script" className="flex">
          {rightTab === 'script' ? (
            <ScriptPanel
              projectId={projectId}
              scene={activeScene}
              characters={entities.filter((e) =>
                activeScene?.characterIds.includes(e.id) ?? false
              )}
              availableGapSecs={availableGapSecs}
              collision={activeCollision}
              activeTab={rightTab}
              onTabChange={setRightTab}
              onAdChange={handleAdChange}
              onActiveToggle={handleActiveToggle}
              onApply={handleApply}
              justApplied={appliedSceneId !== null && appliedSceneId === activeScene?.id}
              onPreviewUsed={() => markTask('voiceline')}
              onVoiceChange={handleVoiceChange}
              onSpeedChange={handleSpeedChange}
              onLockedChange={handleLockedChange}
              onRenameRequest={handleRenameRequest}
              cloudDeferred={cloud}
              onCloudPreview={cloudPreviewEnabled ? handleCloudPreview : undefined}
              cloudReviewEnabled={cloud}
              cloudReviewStatus={activeCloudReviewStatus}
              cloudReviewLoading={cloudReviewLoading}
              cloudReviewUnavailable={cloudReviewUnavailable}
              cloudReviewedAt={activeCloudReviewStatus ? (activeCloudOverride?.reviewedAt ?? null) : undefined}
              cloudReviewSaving={reviewSavingSceneId === activeScene?.id}
              cloudHasUnsavedDraft={!!activeCloudDraft && (
                activeCloudDraft.text !== undefined || activeCloudDraft.active !== undefined
              )}
              onCloudReview={handleReview}
              readOnly={appReviewReadOnly}
            />
          ) : rightTab === 'characters' ? (
            <CharactersPanel
              entities={entities}
              scenes={scenes}
              activeTab={rightTab}
              onTabChange={setRightTab}
              onRename={performRename}
              cloudDeferred={cloud}
            />
          ) : (
            <QualityPanel
              scenes={scenes}
              audioEvents={audioEvents}
              entities={entities}
              activeTab={rightTab}
              onTabChange={setRightTab}
              onSelectScene={(id) => {
                setActiveSceneId(id)
                setRightTab('script')
              }}
            />
          )}
        </div>
      </div>

      {exportOpen && (
        <ExportDialog
          voice={exportVoice}
          format={exportFormat}
          state={exportState}
          onVoiceChange={setExportVoice}
          onFormatChange={setExportFormat}
          onStart={startExport}
          onDownload={triggerDownload}
          onClose={closeExport}
        />
      )}

      {previewOpen && (
        <StudyPreviewDialog
          state={previewState}
          videoUrl={previewVideoUrl}
          projectId={projectId}
          exportId={previewExportId}
          onClose={closePreview}
        />
      )}

      {study && tourOpen && <EditorTour steps={tourSteps} onClose={closeTour} />}

      {study && (
        <HelpPanel open={helpOpen} onOpenChange={setHelpOpen} onReplayTour={replayTour} taskDone={taskDone} />
      )}
    </div>
  )
}

interface StudyPreviewProps {
  state: ExportStatus | null
  videoUrl: string | null
  projectId: string
  exportId: string | null
  onClose: () => void
}

function StudyPreviewDialog({ state, videoUrl, projectId, exportId, onClose }: StudyPreviewProps) {
  const rendering = !videoUrl && (!state || state.status === 'queued' || state.status === 'processing')
  const failed = state?.status === 'failed'

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-neutral-900/50 p-4"
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="w-full max-w-xl rounded-xl bg-neutral-0 p-5 shadow-xl">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-neutral-900">Preview with audio description</h2>
          <button
            onClick={onClose}
            className="rounded p-1 text-neutral-400 hover:bg-neutral-150 hover:text-neutral-700 transition-colors"
            aria-label="Close preview"
          >
            <X size={14} />
          </button>
        </div>

        {rendering && (
          <div className="py-10 text-center">
            <Loader2 className="mx-auto animate-spin text-brand-400" size={24} />
            <p className="mt-3 text-sm text-neutral-600">
              Rendering the spoken description… {state?.progress ?? 0}%
            </p>
            <p className="mt-1 text-xs text-neutral-400">This usually takes under a minute.</p>
          </div>
        )}

        {failed && (
          <p className="rounded bg-danger-50 p-2 text-xs text-danger-500">
            {state?.error || 'Preview failed. Please try again.'}
          </p>
        )}

        {videoUrl && (
          <div className="space-y-3">
            <div className="rounded-lg border border-brand-200 bg-brand-50 p-3 text-sm text-neutral-800">
              <strong className="flex items-center gap-1.5"><Eye size={14} /> Now close your eyes.</strong>
              <p className="mt-1 text-xs text-neutral-600">
                Play it through without looking. This is what a blind or low-vision viewer
                hears. Tweak any line and render it again.
              </p>
            </div>
            <video src={videoUrl} controls autoPlay className="w-full rounded-lg bg-black" />
            <div className="flex gap-2">
              {exportId && (
                <a
                  href={exportDownloadUrl(projectId, exportId, false)}
                  download
                  className="flex-1 rounded-lg border border-neutral-200 bg-neutral-0 px-4 py-2 text-center text-sm font-medium text-neutral-700 hover:bg-neutral-50 transition-colors"
                >
                  Download video
                </a>
              )}
              <button
                onClick={onClose}
                className="flex-1 rounded-lg bg-brand-400 px-4 py-2 text-sm font-medium text-neutral-0 hover:bg-brand-500 transition-colors"
              >
                Done listening
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

interface ExportDialogProps {
  voice: VoiceId
  format: ExportFmt
  state: ExportStatus | null
  onVoiceChange: (v: VoiceId) => void
  onFormatChange: (f: ExportFmt) => void
  onStart: () => void
  onDownload: () => void
  onClose: () => void
}

const FORMAT_OPTIONS: { value: ExportFmt; label: string; hint: string }[] = [
  { value: 'mp4',  label: 'mp4',  hint: 'Video with narrated AD mixed in' },
  { value: 'mp3',  label: 'mp3',  hint: 'Audio-only mix for sound engineers' },
  { value: 'srt',  label: 'srt',  hint: 'Subtitle file with AD timecodes' },
  { value: 'csv',  label: 'csv',  hint: 'Scene table for spreadsheets' },
  { value: 'docx', label: 'docx', hint: 'Formatted script document' },
]

const AUDIO_FORMATS: Set<ExportFmt> = new Set(['mp4', 'mp3'])

function ExportDialog({
  voice, format, state,
  onVoiceChange, onFormatChange, onStart, onDownload, onClose,
}: ExportDialogProps) {
  const isRunning = state?.status === 'processing' || state?.status === 'queued'
  const isReady = state?.status === 'ready'
  const isFailed = state?.status === 'failed'
  const needsVoice = AUDIO_FORMATS.has(format)
  const formatHint = FORMAT_OPTIONS.find((o) => o.value === format)?.hint

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-neutral-900/40"
      onClick={(e) => { if (e.target === e.currentTarget && !isRunning) onClose() }}
    >
      <div className="w-full max-w-sm rounded-xl bg-neutral-0 p-5 shadow-xl">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-neutral-900">Export</h2>
          <button
            onClick={onClose}
            disabled={isRunning}
            className="rounded p-1 text-neutral-400 hover:bg-neutral-150 hover:text-neutral-700 disabled:opacity-40 transition-colors"
            aria-label="Close export dialog"
          >
            <X size={14} />
          </button>
        </div>

        {!state && (
          <>
            <div className="mb-4 space-y-1.5">
              <label htmlFor="export-format" className="text-xs font-medium text-neutral-500">Format</label>
              <select
                id="export-format"
                name="export-format"
                className="w-full rounded-lg border border-neutral-200 bg-neutral-0 px-3 py-2 text-sm text-neutral-900 outline-none focus:border-brand-400"
                value={format}
                onChange={(e) => onFormatChange(e.target.value as ExportFmt)}
              >
                {FORMAT_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
              {formatHint && (
                <p className="text-xs text-neutral-400">{formatHint}</p>
              )}
            </div>

            {needsVoice && (
              <div className="mb-4 space-y-1.5">
                <label htmlFor="export-voice" className="text-xs font-medium text-neutral-500">Default voice</label>
                <select
                  id="export-voice"
                  name="export-voice"
                  className="w-full rounded-lg border border-neutral-200 bg-neutral-0 px-3 py-2 text-sm text-neutral-900 outline-none focus:border-brand-400"
                  value={voice}
                  onChange={(e) => onVoiceChange(e.target.value as VoiceId)}
                >
                  <option value="onyx">Onyx</option>
                  <option value="nova">Nova</option>
                  <option value="alloy">Alloy</option>
                  <option value="shimmer">Shimmer</option>
                </select>
                <p className="text-xs text-neutral-400">
                  Per-scene voice overrides take precedence over this default.
                </p>
              </div>
            )}

            <Button className="w-full" onClick={onStart}>Start export</Button>
          </>
        )}

        {state && (
          <div className="space-y-3">
            <div className="space-y-1.5">
              <div className="flex items-center justify-between text-xs">
                <span className="text-neutral-500">{state.stage.replace(/_/g, ' ')}</span>
                <span className="font-medium text-neutral-700">{state.progress}%</span>
              </div>
              <div className="h-2 w-full overflow-hidden rounded-full bg-neutral-150">
                <div
                  className={isFailed ? 'h-full bg-danger-400 transition-all' : 'h-full bg-brand-400 transition-all'}
                  style={{ width: `${state.progress}%` }}
                />
              </div>
              {state.total_scenes != null && state.done != null && (
                <p className="text-xs text-neutral-400">
                  {state.done} / {state.total_scenes} narration lines
                </p>
              )}
            </div>

            {isRunning && (
              <div className="flex items-center gap-2 text-xs text-neutral-500">
                <Loader2 size={12} className="animate-spin" />
                Rendering — keep this tab open.
              </div>
            )}

            {isReady && (
              <Button className="w-full" onClick={onDownload}>
                Download {state.format ?? format}
              </Button>
            )}

            {isFailed && (
              <p className="rounded bg-danger-50 p-2 text-xs text-danger-500">
                {state.error || 'Export failed.'}
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
