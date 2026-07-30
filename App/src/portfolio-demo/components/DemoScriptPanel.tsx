import { useEffect, useRef, useState } from 'react'
import { Play, Square, Scissors, Volume2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { getSceneStatus } from '@/types'
import type { Scene, SceneStatus } from '@/types'
import { fitToGap } from '../lib/fitToGap'
import type { SceneTiming } from '../lib/timing'
import { playBakedLine, speakWithBrowserVoice, type PlaybackHandle } from '../lib/narration'
import { resolveLocalVoice, type LocalVoiceState } from '../lib/localVoice'
import { claimAudio, clearAudioClaim } from '../lib/audioBus'
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
// AA text tokens on the tinted chip backgrounds.
const STATUS_STYLE: Record<SceneStatus, string> = {
  ok: 'bg-success-50 text-success-800',
  empty: 'bg-warning-50 text-warning-800',
  conflict: 'bg-danger-50 text-danger-800',
  inactive: 'bg-neutral-150 text-neutral-700',
}

const SPEEDS = [0.75, 1.0, 1.25, 1.5]

interface DemoScriptPanelProps {
  scene: Scene | null
  /** Authoritative per-scene timing (lib/timing.ts) for the selected scene. */
  timing: SceneTiming | null
  activeTab: DemoTab
  onTabChange: (tab: DemoTab) => void
  onTextChange: (sceneId: number, text: string) => void
  onToggleActive: (sceneId: number) => void
  speed: number
  onSpeedChange: (speed: number) => void
  sceneEdited: boolean
  /** Editor-computed: genuine overrun with usable silence AND (if colliding)
   *  a trial trim at the current speed clears the collision. */
  fittable: boolean
  onFitText: (sceneId: number, text: string) => void
  /** Fired only when playback has ACTUALLY started (media 'playing' /
   *  speech 'onstart') — the LISTEN step's evidence. */
  onNarrationStarted: () => void
}

/**
 * The demo's script panel. Every control does exactly what its label says;
 * playback participates in the single-audio-owner bus, and browser speech
 * uses only an explicitly selected on-device (localService) voice.
 */
export function DemoScriptPanel({
  scene,
  timing,
  activeTab,
  onTabChange,
  onTextChange,
  onToggleActive,
  speed,
  onSpeedChange,
  sceneEdited,
  fittable,
  onFitText,
  onNarrationStarted,
}: DemoScriptPanelProps) {
  const [playing, setPlaying] = useState<'baked' | 'speech' | null>(null)
  const [audioNote, setAudioNote] = useState<string | null>(null)
  const [fitNote, setFitNote] = useState<string | null>(null)
  const [voiceState, setVoiceState] = useState<LocalVoiceState>({ status: 'pending', voice: null })
  const handleRef = useRef<PlaybackHandle | null>(null)

  useEffect(() => resolveLocalVoice(setVoiceState), [])

  function stopPlayback() {
    handleRef.current?.stop()
    handleRef.current = null
    clearAudioClaim('baked-line')
    clearAudioClaim('speech')
    setPlaying(null)
  }

  // Scene change stops this panel's playback and clears transient notes.
  const sceneKey = scene?.id ?? -1
  useEffect(() => {
    return () => {
      handleRef.current?.stop()
      handleRef.current = null
      clearAudioClaim('baked-line')
      clearAudioClaim('speech')
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
          <p className="text-sm text-neutral-500">Select a scene to edit</p>
        </div>
      </aside>
    )
  }

  const status = getSceneStatus(scene, timing?.talksOverDialogue)
  const estSecs = timing?.estSecs ?? 0
  const usableSecs = timing?.usableSecs ?? 0
  const baked = BAKED_ONYX_SCENES.has(scene.sceneNumber)

  function handleFit() {
    if (!scene || !fittable) return
    const r = fitToGap(scene.text, usableSecs, speed)
    onFitText(scene.id, r.text)
    setFitNote(
      `Kept the first ${r.keptWords} of ${r.totalWords} words — ≈${r.estimatedSecs.toFixed(1)}s ` +
        `at ${speed.toFixed(2).replace(/\.?0+$/, '')}× for the ${r.targetSecs.toFixed(1)}s of usable ` +
        'silence. Local deterministic trim, no AI.',
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
    const handle = playBakedLine(bakedLineUrl(scene.sceneNumber), speed, {
      onStarted: onNarrationStarted,
      onEnded: () => {
        clearAudioClaim('baked-line')
        setPlaying(null)
      },
      onError: (msg) => {
        clearAudioClaim('baked-line')
        setPlaying(null)
        setAudioNote(msg)
      },
    })
    handleRef.current = handle
    claimAudio('baked-line', () => {
      handle.stop()
      handleRef.current = null
      setPlaying((current) => (current === 'baked' ? null : current))
    })
    setPlaying('baked')
  }

  function handleSpeakCurrent() {
    if (!scene || voiceState.status !== 'available' || !voiceState.voice) return
    if (playing === 'speech') {
      stopPlayback()
      return
    }
    stopPlayback()
    setAudioNote(null)
    const handle = speakWithBrowserVoice(scene.text, speed, voiceState.voice, {
      onStarted: onNarrationStarted,
      onEnded: () => {
        clearAudioClaim('speech')
        setPlaying(null)
      },
      onError: (msg) => {
        clearAudioClaim('speech')
        setPlaying(null)
        setAudioNote(msg)
      },
    })
    handleRef.current = handle
    claimAudio('speech', () => {
      handle.stop()
      handleRef.current = null
      setPlaying((current) => (current === 'speech' ? null : current))
    })
    setPlaying('speech')
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
              className="inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-medium text-brand-800 transition-colors hover:bg-brand-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-400 disabled:opacity-40 disabled:hover:bg-transparent"
              title={
                fittable
                  ? `Trim the line to the ${usableSecs.toFixed(1)}s of usable silence at ${speed
                      .toFixed(2)
                      .replace(/\.?0+$/, '')}× — a local, deterministic cut (no AI)`
                  : !scene.active
                    ? 'Switch the line on to edit it'
                    : timing?.talksOverDialogue
                      ? timing.headOnDialogue
                        ? 'Trimming cannot clear this overlap — the film is speaking from the line’s first beat'
                        : 'This deterministic trim cannot fully clear the conflict here — edit the line or switch it off'
                      : 'Offered when the line runs longer than the silence usable from where its narration starts'
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

          <p className="text-xs leading-relaxed text-neutral-500" aria-live="off">
            ≈{estSecs.toFixed(1)}s spoken at {speed.toFixed(2).replace(/\.?0+$/, '')}× ·{' '}
            {usableSecs.toFixed(1)}s usable silence (from where the narration starts) · scene
            window {scene.durationSecs.toFixed(0)}s
          </p>
          {timing?.talksOverDialogue && (
            <p className="text-xs leading-relaxed text-danger-800">
              {fittable
                ? `Talks over dialogue for ≈${timing.rawOverlapSecs.toFixed(1)}s — the line spills past
                   its usable silence into the film's later lines. Trimming fixes this.`
                : timing.headOnDialogue
                  ? `Talks over dialogue for ≈${timing.rawOverlapSecs.toFixed(1)}s — the film is speaking
                     from the line's first beat, so trimming alone cannot clear this.`
                  : `Talks over dialogue for ≈${timing.rawOverlapSecs.toFixed(1)}s — this deterministic
                     trim cannot fully clear the conflict; edit the line or switch it off.`}
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
            {voiceState.status === 'available' ? (
              <button
                onClick={handleSpeakCurrent}
                disabled={!scene.active || !scene.text.trim()}
                className="inline-flex items-center gap-1.5 rounded-lg border border-neutral-200 px-3 py-1.5 text-left text-xs font-medium text-neutral-700 transition-colors hover:bg-neutral-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-400 disabled:opacity-40"
              >
                {playing === 'speech' ? <Square size={12} /> : <Volume2 size={12} />}
                {playing === 'speech' ? 'Stop' : 'Read my text · on-device voice'}
                {playing !== 'speech' && (
                  <span className="font-normal text-neutral-500">— your current words, locally</span>
                )}
              </button>
            ) : voiceState.status === 'unavailable' ? (
              <p className="text-xs leading-relaxed text-neutral-500">
                Your browser offers no on-device voice, so the current text can't be read aloud
                here — this demo never sends text to a speech service. In the full app,
                narration re-renders per edit.
              </p>
            ) : (
              <p className="text-xs text-neutral-500" role="status">
                Checking for an on-device voice…
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
            Applies to both listen options, the timing estimate and the trim budget. Narrator
            voice here is Onyx — the one narrator bundled with this demo.
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
