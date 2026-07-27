import { useEffect, useRef, useState } from 'react'
import { Play, Square, Scissors, Volume2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { getSceneStatus } from '@/types'
import type { Scene, SceneStatus } from '@/types'
import { estimateSpeechSecs, type SceneCollision } from '@/lib/collisions'
import { fitToGap } from '../lib/fitToGap'
import {
  playBakedLine,
  speakWithBrowserVoice,
  speechSynthesisAvailable,
  type PlaybackHandle,
} from '../lib/narration'
import { bakedLineUrl, BAKED_ONYX_SCENES } from '../lib/fixtures'
import { DemoPanelTabs, type DemoTab } from './DemoPanelTabs'

function formatTime(secs: number): string {
  const m = Math.floor(secs / 60)
  const s = Math.floor(secs % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

const STATUS_LABEL: Record<SceneStatus, string> = {
  ok: 'Placed',
  empty: 'Empty',
  conflict: 'Conflict',
  inactive: 'Off',
}
const STATUS_STYLE: Record<SceneStatus, string> = {
  ok: 'bg-success-50 text-success-400',
  empty: 'bg-warning-50 text-warning-400',
  conflict: 'bg-danger-50 text-danger-400',
  inactive: 'bg-neutral-150 text-neutral-400',
}

const SPEEDS = [0.75, 1.0, 1.25, 1.5]

interface DemoScriptPanelProps {
  scene: Scene | null
  availableGapSecs: number
  collision: SceneCollision | null
  activeTab: DemoTab
  onTabChange: (tab: DemoTab) => void
  onTextChange: (sceneId: number, text: string) => void
  onToggleActive: (sceneId: number) => void
  speed: number
  onSpeedChange: (speed: number) => void
  sceneEdited: boolean
  /** Whether the local trim is offered (computed by the editor: genuine
   *  overrun with usable silence, and no head-on dialogue collision that a
   *  trim provably cannot clear). */
  fittable: boolean
  onFitText: (sceneId: number, text: string) => void
  onPlayedOriginal: () => void
  onPlayedBrowserVoice: () => void
}

/**
 * The demo's script panel. Every control does exactly what its label says:
 *  - "Fit to gap (local)" is a deterministic in-browser trim (never called AI);
 *  - "Original line · Onyx" plays the committed pre-generated narration of the
 *    ORIGINAL draft (stated inline, since it never reflects edits);
 *  - "Read my text" uses the browser's own speech synthesis on the current
 *    textarea content (feature-detected);
 *  - playback speed genuinely changes playback (playbackRate / utterance.rate)
 *    and the timing estimates.
 */
export function DemoScriptPanel({
  scene,
  availableGapSecs,
  collision,
  activeTab,
  onTabChange,
  onTextChange,
  onToggleActive,
  speed,
  onSpeedChange,
  sceneEdited,
  fittable,
  onFitText,
  onPlayedOriginal,
  onPlayedBrowserVoice,
}: DemoScriptPanelProps) {
  const [playing, setPlaying] = useState<'baked' | 'speech' | null>(null)
  const [audioNote, setAudioNote] = useState<string | null>(null)
  const [fitNote, setFitNote] = useState<string | null>(null)
  const handleRef = useRef<PlaybackHandle | null>(null)
  const speechOk = speechSynthesisAvailable()

  function stopPlayback() {
    handleRef.current?.stop()
    handleRef.current = null
    setPlaying(null)
  }

  // Stop any playback when the scene changes or the panel unmounts.
  const sceneKey = scene?.id ?? -1
  useEffect(() => {
    return () => {
      handleRef.current?.stop()
      handleRef.current = null
    }
  }, [sceneKey])
  useEffect(() => {
    setAudioNote(null)
    setFitNote(null)
    setPlaying(null)
  }, [sceneKey])

  if (!scene) {
    return (
      <aside
        className="flex h-full w-script-panel shrink-0 flex-col border-l border-neutral-200 bg-neutral-0"
        aria-label="Script panel"
      >
        <DemoPanelTabs active={activeTab} onChange={onTabChange} />
        <div className="flex flex-1 items-center justify-center">
          <p className="text-sm text-neutral-400">Select a scene to edit</p>
        </div>
      </aside>
    )
  }

  const status = getSceneStatus(scene, collision?.collides)
  const estSecs = estimateSpeechSecs(scene.text, speed)
  const baked = BAKED_ONYX_SCENES.has(scene.sceneNumber)

  function handleFit() {
    if (!scene || !fittable) return
    const r = fitToGap(scene.text, availableGapSecs)
    onFitText(scene.id, r.text)
    setFitNote(
      `Kept the first ${r.keptWords} of ${r.totalWords} words — ≈${r.estimatedSecs.toFixed(1)}s ` +
        `for the ${r.targetSecs.toFixed(1)}s of clear silence. Local deterministic trim, no AI.`,
    )
    stopPlayback()
  }

  function handlePlayOriginal() {
    if (!scene) return
    if (playing === 'baked') {
      stopPlayback()
      return
    }
    stopPlayback()
    setAudioNote(null)
    handleRef.current = playBakedLine(
      bakedLineUrl(scene.sceneNumber),
      speed,
      () => setPlaying(null),
      (msg) => {
        setPlaying(null)
        setAudioNote(msg)
      },
    )
    setPlaying('baked')
    onPlayedOriginal()
  }

  function handleSpeakCurrent() {
    if (!scene) return
    if (playing === 'speech') {
      stopPlayback()
      return
    }
    stopPlayback()
    setAudioNote(null)
    handleRef.current = speakWithBrowserVoice(
      scene.text,
      speed,
      () => setPlaying(null),
      (msg) => {
        setPlaying(null)
        setAudioNote(msg)
      },
    )
    setPlaying('speech')
    onPlayedBrowserVoice()
  }

  return (
    <aside
      className="flex h-full w-script-panel shrink-0 flex-col border-l border-neutral-200 bg-neutral-0"
      aria-label="Script panel"
    >
      <DemoPanelTabs active={activeTab} onChange={onTabChange} />

      <div className="flex h-10 shrink-0 items-center gap-2 border-b border-neutral-200 px-4">
        <span className="text-xs font-medium text-neutral-500">
          Scene {scene.sceneNumber}
          <span className="mx-1 text-neutral-300">·</span>
          {formatTime(scene.startSecs)} – {formatTime(scene.endSecs)}
        </span>
        <span
          className={cn('ml-auto rounded-full px-2 py-0.5 text-xs font-medium', STATUS_STYLE[status])}
        >
          {STATUS_LABEL[status]}
        </span>
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        <div className="space-y-1.5" data-tour="script-edit">
          <div className="flex items-center justify-between gap-1">
            <label htmlFor="pd-ad-text" className="text-xs font-medium text-neutral-500">
              Audio description
            </label>
            <button
              data-tour="fit"
              onClick={handleFit}
              disabled={!fittable || !scene.active}
              className="inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-medium text-brand-500 transition-colors hover:bg-brand-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-400 disabled:opacity-40 disabled:hover:bg-transparent"
              title={
                fittable
                  ? `Trim the line to the ${availableGapSecs.toFixed(1)}s of clear silence — a local, deterministic cut (no AI)`
                  : !scene.active
                    ? 'Switch the line on to edit it'
                    : collision?.collides
                      ? 'Trimming cannot clear this overlap — the dialogue starts almost the moment the line begins'
                      : 'Offered when the line runs longer than the clear silence available to this scene'
              }
            >
              <Scissors size={12} />
              Fit to gap (local)
            </button>
          </div>
          <textarea
            id="pd-ad-text"
            name="pd-ad-text"
            className="w-full resize-y rounded-lg border border-neutral-200 bg-neutral-50 p-3 text-sm leading-relaxed text-neutral-900 outline-none transition-colors focus:border-brand-400 disabled:opacity-50"
            rows={7}
            value={scene.text}
            disabled={!scene.active}
            onChange={(e) => onTextChange(scene.id, e.target.value)}
            placeholder="Write the audio description for this scene…"
          />
          {fitNote && <p className="text-xs text-neutral-500">{fitNote}</p>}

          {/* The honest time budget — always visible, updates as you type. */}
          <p className="text-xs leading-relaxed text-neutral-500" aria-live="off">
            ≈{estSecs.toFixed(1)}s spoken at {speed.toFixed(2).replace(/\.?0+$/, '')}× ·{' '}
            {availableGapSecs.toFixed(1)}s clear silence available · scene window{' '}
            {scene.durationSecs.toFixed(0)}s
          </p>
          {collision?.collides && (
            <p className="text-xs leading-relaxed text-danger-800">
              Talks over dialogue for ≈{collision.overlapSecs.toFixed(1)}s — the dialogue starts
              almost the moment the line begins, so trimming alone cannot clear this.
            </p>
          )}
        </div>

        <div className="space-y-1.5" data-tour="listen">
          <p className="text-xs font-medium text-neutral-500" id="pd-listen-label">
            Listen
          </p>
          <div className="flex flex-col gap-1.5" role="group" aria-labelledby="pd-listen-label">
            {baked && (
              <button
                onClick={handlePlayOriginal}
                disabled={!scene.active}
                className="inline-flex items-center gap-1.5 rounded-lg border border-neutral-200 px-3 py-1.5 text-left text-xs font-medium text-neutral-700 transition-colors hover:bg-neutral-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-400 disabled:opacity-40"
              >
                {playing === 'baked' ? <Square size={12} /> : <Play size={12} />}
                {playing === 'baked' ? 'Stop' : 'Original line · Onyx (pre-generated)'}
                {playing !== 'baked' && (
                  <span className="font-normal text-neutral-500">
                    — reads the original draft{sceneEdited ? ', not your edits' : ''}
                  </span>
                )}
              </button>
            )}
            {speechOk ? (
              <button
                onClick={handleSpeakCurrent}
                disabled={!scene.active || !scene.text.trim()}
                className="inline-flex items-center gap-1.5 rounded-lg border border-neutral-200 px-3 py-1.5 text-left text-xs font-medium text-neutral-700 transition-colors hover:bg-neutral-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-400 disabled:opacity-40"
              >
                {playing === 'speech' ? <Square size={12} /> : <Volume2 size={12} />}
                {playing === 'speech' ? 'Stop' : 'Read my text · browser voice'}
                {playing !== 'speech' && (
                  <span className="font-normal text-neutral-500">— your current words</span>
                )}
              </button>
            ) : (
              <p className="text-xs text-neutral-500">
                Your browser doesn't offer speech synthesis, so the current text can't be read
                aloud here. In the full app, narration is re-generated per edit.
              </p>
            )}
            {audioNote && <p className="text-xs text-danger-800">{audioNote}</p>}
          </div>
        </div>

        <div className="space-y-1.5">
          <label htmlFor="pd-speed" className="text-xs font-medium text-neutral-500">
            Playback speed
          </label>
          <select
            id="pd-speed"
            name="pd-speed"
            className="w-24 rounded-lg border border-neutral-200 bg-neutral-0 px-2 py-1.5 text-sm text-neutral-700 outline-none focus:border-brand-400"
            value={speed}
            onChange={(e) => onSpeedChange(parseFloat(e.target.value))}
          >
            {SPEEDS.map((s) => (
              <option key={s} value={s}>
                {s.toFixed(2).replace(/\.?0+$/, '')}×
              </option>
            ))}
          </select>
          <p className="text-xs text-neutral-500">
            Applies to both listen options and to the timing estimate. Narrator voice here is
            Onyx — the one narrator bundled with this demo.
          </p>
        </div>
      </div>

      <div className="shrink-0 space-y-2 border-t border-neutral-200 p-4">
        {scene.active ? (
          <Button
            variant="outline"
            size="sm"
            className="w-full gap-1.5"
            data-tour="toggle-line"
            onClick={() => onToggleActive(scene.id)}
          >
            Switch this line off
          </Button>
        ) : (
          <Button
            variant="default"
            className="w-full gap-1.5"
            data-tour="toggle-line"
            onClick={() => onToggleActive(scene.id)}
          >
            Switch this line on
          </Button>
        )}
        <p className="text-center text-[11px] leading-relaxed text-neutral-500">
          Edits live in this tab only — nothing is uploaded or saved.
        </p>
      </div>
    </aside>
  )
}
