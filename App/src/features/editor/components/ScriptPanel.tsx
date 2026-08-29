import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Lock, Unlock, Play, Pause, Loader2, Sparkles, Check, CircleSlash } from 'lucide-react'
import { cn } from '@/lib/utils'
import { getSceneStatus } from '@/types'
import { previewTts, smartFillScene, patchScene, type VoiceId } from '@/lib/api'
import type { Scene, Entity, SceneStatus } from '@/types'
import type { CloudSceneReviewStatus } from '@/lib/cloudApi'
import { canSmartFill, type SceneCollision } from '@/lib/collisions'
import { RightPanelTabs, type RightPanelTab } from './RightPanelTabs'

function formatTime(secs: number): string {
  const m = Math.floor(secs / 60)
  const s = Math.floor(secs % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

const STATUS_LABEL: Record<SceneStatus, string> = {
  ok:       'Placed',
  empty:    'Empty',
  conflict: 'Conflict',
  inactive: 'Inactive',
}

const STATUS_STYLE: Record<SceneStatus, string> = {
  ok:       'bg-success-50 text-success-400',
  empty:    'bg-warning-50 text-warning-400',
  conflict: 'bg-danger-50 text-danger-400',
  inactive: 'bg-neutral-150 text-neutral-400',
}

const VOICES: { value: VoiceId; label: string }[] = [
  { value: 'onyx', label: 'Onyx' },
  { value: 'nova', label: 'Nova' },
  { value: 'alloy', label: 'Alloy' },
  { value: 'shimmer', label: 'Shimmer' },
]

const SPEEDS = [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5]

const REVIEW_LABEL: Record<CloudSceneReviewStatus, string> = {
  generated: 'Generated',
  edited: 'Edited',
  approved: 'Approved',
  rejected: 'Rejected',
}

const REVIEW_STYLE: Record<CloudSceneReviewStatus, string> = {
  generated: 'bg-neutral-150 text-neutral-500',
  edited: 'bg-warning-50 text-warning-400',
  approved: 'bg-success-50 text-success-400',
  rejected: 'bg-danger-50 text-danger-400',
}

interface ScriptPanelProps {
  projectId: string
  scene: Scene | null
  characters: Entity[]
  availableGapSecs: number
  collision: SceneCollision | null
  activeTab: RightPanelTab
  onTabChange: (tab: RightPanelTab) => void
  onAdChange: (sceneId: number, text: string) => void
  onActiveToggle: (sceneId: number) => void
  onApply: (sceneId: number) => void
  justApplied?: boolean
  onPreviewUsed?: () => void
  onVoiceChange: (sceneId: number, voice: VoiceId) => void
  onSpeedChange: (sceneId: number, speed: number) => void
  onLockedChange: (sceneId: number, locked: boolean) => void
  onRenameRequest: (characterId: string, currentName: string) => void
  /** Cloud mode keeps legacy-only mutations fenced. A supplied preview
      callback is the sole authenticated cloud preview seam. */
  cloudDeferred?: boolean
  onCloudPreview?: (
    sceneId: string,
    text: string,
    voice: VoiceId,
    speed: number,
    signal: AbortSignal,
  ) => Promise<Blob>
  cloudReviewEnabled?: boolean
  cloudReviewStatus?: CloudSceneReviewStatus
  cloudReviewLoading?: boolean
  cloudReviewUnavailable?: boolean
  cloudReviewedAt?: string | null
  cloudReviewSaving?: boolean
  cloudHasUnsavedDraft?: boolean
  onCloudReview?: (sceneId: number, status: 'approved' | 'rejected') => void
  readOnly?: boolean
}

export function ScriptPanel({
  projectId,
  scene,
  characters,
  availableGapSecs,
  collision,
  activeTab,
  onTabChange,
  onAdChange,
  onActiveToggle,
  onApply,
  justApplied,
  onPreviewUsed,
  onVoiceChange,
  onSpeedChange,
  onLockedChange,
  onRenameRequest,
  cloudDeferred = false,
  onCloudPreview,
  cloudReviewEnabled = false,
  cloudReviewStatus,
  cloudReviewLoading = false,
  cloudReviewUnavailable = false,
  cloudReviewedAt,
  cloudReviewSaving = false,
  cloudHasUnsavedDraft = false,
  onCloudReview,
  readOnly = false,
}: ScriptPanelProps) {
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const blobUrlRef = useRef<string | null>(null)
  const previewAbortRef = useRef<AbortController | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [isPlaying, setIsPlaying] = useState(false)
  const [hasLoaded, setHasLoaded] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [isSmartFilling, setIsSmartFilling] = useState(false)
  const [smartFillNote, setSmartFillNote] = useState<string | null>(null)

  function resetPreview() {
    previewAbortRef.current?.abort()
    previewAbortRef.current = null
    const a = audioRef.current
    if (a) {
      a.onended = null
      a.onerror = null
      a.pause()
    }
    audioRef.current = null
    if (blobUrlRef.current) {
      URL.revokeObjectURL(blobUrlRef.current)
      blobUrlRef.current = null
    }
    setIsPlaying(false)
    setHasLoaded(false)
    setIsLoading(false)
  }

  useEffect(() => {
    return () => { resetPreview() }
  }, [])

  // Drop any loaded audio when the scene / voice / speed / text changes so the
  // next click re-renders with the new parameters instead of resuming stale audio.
  const previewKey = `${scene?.sceneKey ?? ''}::${scene?.text ?? ''}::${scene?.voiceId ?? ''}::${scene?.voiceSpeed ?? ''}`
  useEffect(() => {
    resetPreview()
    setPreviewError(null)
  }, [previewKey])

  useEffect(() => {
    if (readOnly) resetPreview()
  }, [readOnly])

  if (!scene) {
    return (
      <aside className="flex h-full w-script-panel shrink-0 flex-col border-l border-neutral-200 bg-neutral-0">
        <RightPanelTabs active={activeTab} onChange={onTabChange} />
        <div className="flex flex-1 items-center justify-center">
          <p className="text-sm text-neutral-400">Select a scene to edit</p>
        </div>
      </aside>
    )
  }

  const status = getSceneStatus(scene, collision?.collides)
  const locked = scene.locked
  const voice = (scene.voiceId as VoiceId | undefined) ?? 'onyx'
  const speed = scene.voiceSpeed ?? 1.0

  // Smart Fill only helps when narration talks over dialogue AND a silence gap
  // sits nearby to shorten the line into.
  const fillable = canSmartFill(collision)
  const fillTitle = fillable
    ? `Shorten to fit the ${availableGapSecs.toFixed(1)}s silence gap`
    : collision?.collides
      ? 'No silence gap nearby to shorten into'
      : 'This description already fits without talking over dialogue'

  async function handlePreviewToggle() {
    if (!scene || !scene.text.trim()) return
    onPreviewUsed?.()

    const a = audioRef.current
    if (a && hasLoaded) {
      if (isPlaying) {
        a.pause()
        setIsPlaying(false)
      } else {
        try {
          await a.play()
          setIsPlaying(true)
        } catch {
          setPreviewError('Playback failed')
          setIsPlaying(false)
        }
      }
      return
    }

    setIsLoading(true)
    setPreviewError(null)
    const controller = new AbortController()
    previewAbortRef.current = controller
    try {
      const blob = onCloudPreview
        ? await onCloudPreview(scene.sceneKey, scene.text, voice, speed, controller.signal)
        : await previewTts(projectId, scene.sceneNumber, scene.text, voice, speed)
      if (controller.signal.aborted || previewAbortRef.current !== controller) return
      const url = URL.createObjectURL(blob)
      blobUrlRef.current = url
      const audio = new Audio(url)
      audio.onended = () => setIsPlaying(false)
      audio.onerror = () => {
        setPreviewError('Playback failed')
        setIsPlaying(false)
      }
      audioRef.current = audio
      setHasLoaded(true)
      await audio.play()
      setIsPlaying(true)
    } catch (err) {
      if (controller.signal.aborted) return
      setPreviewError(err instanceof Error ? err.message : String(err))
    } finally {
      if (previewAbortRef.current === controller) {
        previewAbortRef.current = null
        setIsLoading(false)
      }
    }
  }

  function previewLabel() {
    if (isLoading) return 'Loading…'
    if (isPlaying) return 'Pause'
    if (hasLoaded) return 'Resume'
    return 'Preview'
  }

  async function handleSmartFill() {
    if (!scene || !fillable) return
    setIsSmartFilling(true)
    setSmartFillNote(null)
    try {
      const result = await smartFillScene(projectId, scene.text, availableGapSecs)
      onAdChange(scene.id, result.ad)
      resetPreview()
      // Commit so the rewrite survives a refetch (e.g. after a character rename).
      patchScene(projectId, scene.sceneNumber, { ad: result.ad }).catch(console.error)
      setSmartFillNote(
        `Target ${result.target_secs.toFixed(1)}s · est. ${result.estimated_secs.toFixed(1)}s`
      )
    } catch (err) {
      setSmartFillNote(err instanceof Error ? err.message : String(err))
    } finally {
      setIsSmartFilling(false)
    }
  }

  return (
    <aside className="flex h-full w-script-panel shrink-0 flex-col border-l border-neutral-200 bg-neutral-0">
      <RightPanelTabs active={activeTab} onChange={onTabChange} />
      <div className="flex h-10 shrink-0 items-center gap-2 border-b border-neutral-200 px-4">
        <span className="text-xs font-medium text-neutral-500">
          Scene {scene.sceneNumber}
          <span className="mx-1 text-neutral-300">·</span>
          {formatTime(scene.startSecs)} – {formatTime(scene.endSecs)}
          <span className="mx-1 text-neutral-300">·</span>
          {scene.durationSecs.toFixed(1)}s
        </span>

        <span className={cn(
          'ml-auto rounded-full px-2 py-0.5 text-xs font-medium',
          STATUS_STYLE[status],
        )}>
          {STATUS_LABEL[status]}
        </span>

        <button
          onClick={() => onLockedChange(scene.id, !locked)}
          disabled={readOnly}
          className="rounded p-1 text-neutral-400 hover:bg-neutral-150 hover:text-neutral-700 transition-colors"
          title={locked ? 'Locked' : 'Unlocked'}
        >
          {locked ? <Lock size={13} /> : <Unlock size={13} />}
        </button>
      </div>

      <div className={cn(
        'flex-1 overflow-y-auto p-4 space-y-4 transition-opacity',
        !scene.active && 'opacity-50 pointer-events-none',
      )}>
        <div className="space-y-1.5" data-tour="script-edit">
          <div className="flex items-center justify-between gap-1">
            <label htmlFor="ad-text" className="text-xs font-medium text-neutral-500">Audio Description</label>
            <div className="flex items-center gap-1">
              <button
                onClick={cloudDeferred ? undefined : handleSmartFill}
                disabled={readOnly || cloudDeferred || isSmartFilling || !scene.text.trim() || locked || !fillable}
                aria-describedby={cloudDeferred ? 'script-deferred-note' : undefined}
                className="inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-medium text-brand-500 hover:bg-brand-50 disabled:opacity-40 disabled:hover:bg-transparent transition-colors"
                title={cloudDeferred ? undefined : fillTitle}
              >
                {isSmartFilling
                  ? <Loader2 size={12} className="animate-spin" />
                  : <Sparkles size={12} />}
                Smart Fill
              </button>
              <button
                onClick={cloudDeferred && !onCloudPreview ? undefined : handlePreviewToggle}
                disabled={readOnly || (cloudDeferred && !onCloudPreview) || isLoading || !scene.text.trim() || locked}
                aria-describedby={cloudDeferred && !onCloudPreview ? 'script-deferred-note' : undefined}
                className="inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-medium text-brand-500 hover:bg-brand-50 disabled:opacity-40 disabled:hover:bg-transparent transition-colors"
                title={cloudDeferred && !onCloudPreview ? undefined : (isPlaying ? 'Pause preview' : (hasLoaded ? 'Resume preview' : 'Preview narration'))}
              >
                {isLoading
                  ? <Loader2 size={12} className="animate-spin" />
                  : isPlaying
                    ? <Pause size={12} />
                    : <Play size={12} />}
                {previewLabel()}
              </button>
            </div>
          </div>
          {cloudDeferred && (
            <p id="script-deferred-note" className="text-[11px] text-neutral-400">
              {onCloudPreview
                ? 'Smart Fill & character rename — coming later.'
                : 'Smart Fill, Preview & character rename — coming later.'}
            </p>
          )}
          <textarea
            id="ad-text"
            name="ad-text"
            className="w-full resize-none rounded-lg border border-neutral-200 bg-neutral-50 p-3 text-sm text-neutral-900 leading-relaxed outline-none focus:border-brand-400 transition-colors"
            rows={6}
            value={scene.text}
            readOnly={locked || readOnly}
            onChange={(e) => onAdChange(scene.id, e.target.value)}
            placeholder="Write the audio description for this scene…"
          />
          {smartFillNote && (
            <p className="text-xs text-neutral-500">{smartFillNote}</p>
          )}
          {previewError && (
            <p className="text-xs text-danger-500">{previewError}</p>
          )}
        </div>

        {characters.length > 0 && (
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-neutral-500">Characters</label>
            <div className="flex flex-wrap gap-1.5">
              {characters.map((ch) => (
                <button
                  key={ch.id}
                  disabled={readOnly || cloudDeferred}
                  aria-describedby={cloudDeferred ? 'script-deferred-note' : undefined}
                  onClick={cloudDeferred ? undefined : () => onRenameRequest(ch.id, ch.name)}
                  className="inline-flex items-center rounded-full bg-brand-50 px-2.5 py-1 text-xs font-medium text-brand-500 hover:bg-brand-100 transition-colors disabled:opacity-40 disabled:hover:bg-brand-50"
                  title={cloudDeferred ? undefined : 'Click to rename'}
                >
                  {ch.name}
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="space-y-1.5" data-tour="script-controls">
          <label htmlFor="voice-select" className="text-xs font-medium text-neutral-500">Voice</label>
          <div className="flex items-center gap-2">
            <select
              id="voice-select"
              name="voice"
              className="flex-1 rounded-lg border border-neutral-200 bg-neutral-0 px-3 py-2 text-sm text-neutral-900 outline-none focus:border-brand-400"
              value={voice}
              disabled={readOnly}
              onChange={(e) => onVoiceChange(scene.id, e.target.value as VoiceId)}
            >
              {VOICES.map((v) => (
                <option key={v.value} value={v.value}>{v.label}</option>
              ))}
            </select>
            <select
              id="speed-select"
              name="speed"
              aria-label="Speed"
              className="w-20 rounded-lg border border-neutral-200 bg-neutral-0 px-2 py-2 text-sm text-neutral-700 outline-none focus:border-brand-400"
              value={speed}
              disabled={readOnly}
              onChange={(e) => onSpeedChange(scene.id, parseFloat(e.target.value))}
            >
              {SPEEDS.map((s) => (
                <option key={s} value={s}>{s.toFixed(2).replace(/\.?0+$/, '')}×</option>
              ))}
            </select>
          </div>
        </div>
      </div>

      <div className="shrink-0 border-t border-neutral-200 p-4 space-y-2">
        {cloudReviewEnabled && onCloudReview && !readOnly && (
          <div className="mb-3 space-y-2 rounded-lg border border-neutral-200 bg-neutral-50 p-3">
            <div className="flex items-center gap-2">
              <span className="text-xs font-medium text-neutral-600">Human review</span>
              <span className={cn(
                'ml-auto rounded-full px-2 py-0.5 text-xs font-medium',
                cloudReviewStatus
                  ? REVIEW_STYLE[cloudReviewStatus]
                  : cloudReviewUnavailable
                    ? 'bg-danger-50 text-danger-400'
                    : 'bg-neutral-150 text-neutral-500',
              )}>
                {cloudReviewStatus
                  ? REVIEW_LABEL[cloudReviewStatus]
                  : cloudReviewUnavailable
                    ? 'Unavailable'
                    : 'Loading…'}
              </span>
            </div>
            {cloudReviewLoading && (
              <p className="text-[11px] text-neutral-400">Loading the persisted review state…</p>
            )}
            {cloudReviewUnavailable && (
              <p className="text-[11px] text-danger-400">Review state could not be loaded. Refresh before making a decision.</p>
            )}
            {cloudHasUnsavedDraft && (
              <p className="text-[11px] text-warning-400">Unsaved session draft — review will save the visible text first.</p>
            )}
            {!cloudHasUnsavedDraft && cloudReviewedAt && (
              <p className="text-[11px] text-neutral-400">Decision saved persistently.</p>
            )}
            <div className="grid grid-cols-2 gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={cloudReviewSaving || cloudReviewLoading || cloudReviewUnavailable || !cloudReviewStatus || !scene.text.trim()}
                onClick={() => onCloudReview(scene.id, 'rejected')}
              >
                Reject
              </Button>
              <Button
                variant="default"
                size="sm"
                disabled={cloudReviewSaving || cloudReviewLoading || cloudReviewUnavailable || !cloudReviewStatus || !scene.text.trim()}
                onClick={() => onCloudReview(scene.id, 'approved')}
              >
                {cloudReviewSaving ? <Loader2 size={13} className="animate-spin" /> : 'Approve'}
              </Button>
            </div>
          </div>
        )}
        {scene.active ? (
          <Button
            variant="ghost"
            size="sm"
            className="w-full gap-1.5"
            onClick={() => onActiveToggle(scene.id)}
            disabled={readOnly}
          >
            <CircleSlash size={14} strokeWidth={2} />
            Deactivate
          </Button>
        ) : (
          <Button
            variant="default"
            className="w-full gap-1.5"
            onClick={() => onActiveToggle(scene.id)}
            disabled={readOnly}
          >
            <Check size={14} strokeWidth={2} />
            Activate scene
          </Button>
        )}
        {justApplied ? (
          <div
            role="status"
            aria-live="polite"
            className="flex w-full items-center justify-center gap-1.5 rounded-lg border border-success-400 bg-success-50 px-4 py-2 text-sm font-medium text-success-400"
          >
            <Check size={15} strokeWidth={2.5} />
            Changes applied
          </div>
        ) : (
          <Button
            variant={scene.active ? 'default' : 'outline'}
            className="w-full"
            disabled={readOnly || locked || !scene.active || !scene.text.trim()}
            onClick={() => onApply(scene.id)}
          >
            {/* Cloud (G7 B6): Apply persists the scene override — it must
                not imply export, which is deferred. Legacy keeps its label. */}
            {cloudDeferred ? 'Apply' : 'Apply to export'}
          </Button>
        )}
      </div>
    </aside>
  )
}
