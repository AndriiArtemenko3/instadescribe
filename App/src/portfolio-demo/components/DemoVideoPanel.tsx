import { useRef, useEffect } from 'react'
import { AlertTriangle } from 'lucide-react'
import type { AdGap, AudioEvent, Scene } from '@/types'
import type { SceneTiming } from '../lib/timing'
import { claimAudio, clearAudioClaim } from '../lib/audioBus'
import { CAPTIONS_SRC, POSTER_SRC } from '../lib/fixtures'

// Fork of the app's VideoPanel for the portfolio demo, with three additions:
// a dialogue captions track (derived from the committed transcript fixture),
// keyboard semantics + visible focus for the seek timeline, and deliberate
// preload behavior (the source video only starts loading once this mounts —
// i.e. after "Start now").

function formatTime(secs: number): string {
  const m = Math.floor(secs / 60)
  const s = Math.floor(secs % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

interface DemoVideoPanelProps {
  videoSrc: string
  duration: number
  scenes: Scene[]
  adGaps: AdGap[]
  audioEvents: AudioEvent[]
  timings: Record<number, SceneTiming>
  currentTime: number
  onSeek: (secs: number) => void
  onTimeUpdate: (secs: number) => void
}

export function DemoVideoPanel({
  videoSrc,
  duration,
  scenes,
  adGaps,
  audioEvents,
  timings,
  currentTime,
  onSeek,
  onTimeUpdate,
}: DemoVideoPanelProps) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const progress = duration > 0 ? (currentTime / duration) * 100 : 0
  const conflictCount = scenes.filter((s) => timings[s.id]?.talksOverDialogue).length

  useEffect(() => {
    const video = videoRef.current
    if (!video) return
    if (Math.abs(video.currentTime - currentTime) > 0.5) {
      video.currentTime = currentTime
    }
  }, [currentTime])

  function seekTo(t: number) {
    const clamped = Math.max(0, Math.min(duration, t))
    onSeek(clamped)
    if (videoRef.current) videoRef.current.currentTime = clamped
  }

  function handleTimelineClick(e: React.MouseEvent<HTMLDivElement>) {
    const rect = e.currentTarget.getBoundingClientRect()
    const ratio = (e.clientX - rect.left) / rect.width
    seekTo(ratio * duration)
  }

  function handleTimelineKey(e: React.KeyboardEvent<HTMLDivElement>) {
    const step = e.shiftKey ? 1 : 5
    if (e.key === 'ArrowRight') {
      e.preventDefault()
      seekTo(currentTime + step)
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault()
      seekTo(currentTime - step)
    } else if (e.key === 'Home') {
      e.preventDefault()
      seekTo(0)
    } else if (e.key === 'End') {
      e.preventDefault()
      seekTo(duration)
    }
  }

  return (
    <div className="flex h-full flex-1 flex-col overflow-hidden">
      <div className="flex flex-1 items-center justify-center bg-neutral-950" data-tour="video-player">
        <video
          ref={videoRef}
          className="h-full w-full object-contain"
          controls
          preload="metadata"
          poster={POSTER_SRC}
          onTimeUpdate={(e) => onTimeUpdate(e.currentTarget.currentTime)}
          onPlay={() => claimAudio('source-video', () => videoRef.current?.pause())}
          onPause={() => clearAudioClaim('source-video')}
          aria-label="Sintel excerpt — original clip"
        >
          <source src={videoSrc} type="video/mp4" />
          <track kind="captions" src={CAPTIONS_SRC} srcLang="en" label="Dialogue (English)" />
        </video>
      </div>

      <div className="flex h-10 shrink-0 items-center gap-4 border-t border-neutral-200 bg-neutral-0 px-4">
        <span className="font-mono text-xs tabular-nums text-neutral-500">
          {formatTime(currentTime)} / {formatTime(duration)}
        </span>
        <div className="flex-1" />
        <span className="flex items-center gap-1 text-xs text-neutral-500">
          {adGaps.length} recommended gaps
          {conflictCount > 0 && (
            <span className="ml-1 flex items-center gap-1 font-medium text-danger-800">
              <AlertTriangle size={13} strokeWidth={2} />
              {conflictCount} {conflictCount === 1 ? 'conflict' : 'conflicts'}
            </span>
          )}
        </span>
      </div>

      <div className="shrink-0 border-t border-neutral-200 bg-neutral-0 px-4 py-2" data-tour="video-timeline">
        {/* Dialogue / silence strip (not interactive). */}
        <div className="relative mb-1 h-[18px] w-full overflow-hidden rounded-sm bg-neutral-150" aria-hidden="true">
          {audioEvents.map((ev) => {
            const left = duration > 0 ? (ev.startSecs / duration) * 100 : 0
            const width = duration > 0 ? (ev.durationSecs / duration) * 100 : 0
            return (
              <div
                key={ev.id}
                className={
                  ev.type === 'dialogue'
                    ? 'absolute top-0 h-full bg-info-400 opacity-40'
                    : ev.type === 'music'
                      ? 'absolute top-0 h-full bg-warning-400 opacity-30'
                      : 'absolute top-0 h-full bg-neutral-300 opacity-40'
                }
                style={{ left: `${left}%`, width: `${width}%` }}
              />
            )
          })}
          {scenes.map((scene) => {
            const timing = timings[scene.id]
            if (!timing?.talksOverDialogue || duration <= 0) return null
            if (timing.overlapStart === null || timing.overlapEnd === null) return null
            const left = (timing.overlapStart / duration) * 100
            const width = ((timing.overlapEnd - timing.overlapStart) / duration) * 100
            if (width <= 0) return null
            return (
              <div
                key={`overrun-${scene.id}`}
                className="absolute top-0 h-full bg-danger-400 opacity-50"
                style={{ left: `${left}%`, width: `${width}%` }}
                title={`Scene ${scene.sceneNumber} narration talks over dialogue for ~${timing.rawOverlapSecs.toFixed(1)}s.`}
              />
            )
          })}
        </div>

        {/* Seek strip — clickable and keyboard-operable. */}
        <div
          role="slider"
          tabIndex={0}
          aria-label="Seek through the clip"
          aria-valuemin={0}
          aria-valuemax={Math.round(duration)}
          aria-valuenow={Math.round(currentTime)}
          aria-valuetext={`${formatTime(currentTime)} of ${formatTime(duration)}`}
          onKeyDown={handleTimelineKey}
          className="relative h-[18px] w-full cursor-pointer overflow-hidden rounded-sm bg-neutral-150 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-400"
          onClick={handleTimelineClick}
        >
          {adGaps.map((gap) => {
            const left = duration > 0 ? (gap.startSecs / duration) * 100 : 0
            const width = duration > 0 ? (gap.durationSecs / duration) * 100 : 0
            return (
              <div
                key={gap.id}
                className={
                  gap.isRecommended
                    ? 'absolute top-0 h-full bg-brand-400 opacity-60'
                    : 'absolute top-0 h-full bg-brand-200 opacity-40'
                }
                style={{ left: `${left}%`, width: `${width}%` }}
              />
            )
          })}
          <div
            className="absolute top-0 h-full w-0.5 bg-neutral-900 opacity-70"
            style={{ left: `${progress}%` }}
          />
        </div>

        <div className="mt-1 flex items-center gap-4">
          <span className="flex items-center gap-1 text-xs text-neutral-500">
            <span className="inline-block h-2 w-3 rounded-sm bg-info-400 opacity-60" />
            Dialogue
          </span>
          <span className="flex items-center gap-1 text-xs text-neutral-500">
            <span className="inline-block h-2 w-3 rounded-sm bg-brand-400 opacity-70" />
            Recommended silence
          </span>
          {conflictCount > 0 && (
            <span className="flex items-center gap-1 text-xs text-neutral-500">
              <span className="inline-block h-2 w-3 rounded-sm bg-danger-400 opacity-50" />
              Talks over dialogue
            </span>
          )}
        </div>
      </div>
    </div>
  )
}
