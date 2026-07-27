import { CheckCircle2, AlertTriangle, CircleSlash } from 'lucide-react'
import type { Scene } from '@/types'
import { estimateSpeechSecs, type SceneCollision } from '@/lib/collisions'
import { DemoPanelTabs, type DemoTab } from './DemoPanelTabs'

interface ChecksPanelProps {
  scenes: Scene[]
  collisions: Record<number, SceneCollision>
  speed: number
  activeTab: DemoTab
  onTabChange: (tab: DemoTab) => void
  onSelectScene: (id: number) => void
}

/**
 * Transparent per-line timing checks. This deliberately replaces the app's
 * weighted "overall quality" score: every row is a plain, verifiable statement
 * about timing, and the footer says exactly how the numbers are computed.
 */
export function ChecksPanel({
  scenes,
  collisions,
  speed,
  activeTab,
  onTabChange,
  onSelectScene,
}: ChecksPanelProps) {
  const on = scenes.filter((s) => s.active && s.text.trim())
  const overDialogue = on.filter((s) => collisions[s.id]?.collides)
  const tooLong = on.filter(
    (s) => estimateSpeechSecs(s.text, speed) > s.durationSecs,
  )

  return (
    <aside
      className="flex h-full w-script-panel shrink-0 flex-col border-l border-neutral-200 bg-neutral-0"
      aria-label="Timing checks panel"
    >
      <DemoPanelTabs active={activeTab} onChange={onTabChange} />

      <div className="border-b border-neutral-200 px-4 py-3">
        <p className="text-xs font-medium text-neutral-900">
          {on.length} of {scenes.length} lines on
          <span className="mx-1.5 text-neutral-300">·</span>
          {overDialogue.length} talk over dialogue
          <span className="mx-1.5 text-neutral-300">·</span>
          {tooLong.length} run past their scene
        </p>
      </div>

      <ul className="flex-1 space-y-1 overflow-y-auto p-2">
        {scenes.map((s) => {
          const est = estimateSpeechSecs(s.text, speed)
          const collides = collisions[s.id]?.collides ?? false
          const overrun = s.active && s.text.trim() !== '' && est > s.durationSecs
          return (
            <li key={s.id}>
              <button
                onClick={() => onSelectScene(s.id)}
                className="w-full rounded-lg border border-neutral-200 p-2.5 text-left transition-colors hover:border-brand-400 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-400"
              >
                <span className="text-xs font-medium text-neutral-900">Scene {s.sceneNumber}</span>
                {!s.active || !s.text.trim() ? (
                  <span className="mt-1 flex items-center gap-1.5 text-xs text-neutral-500">
                    <CircleSlash size={12} />
                    {s.active ? 'Empty — nothing to check' : 'Switched off — not in the mix'}
                  </span>
                ) : (
                  <>
                    <span
                      className={
                        overrun
                          ? 'mt-1 flex items-center gap-1.5 text-xs text-danger-800'
                          : 'mt-1 flex items-center gap-1.5 text-xs text-success-800'
                      }
                    >
                      {overrun ? <AlertTriangle size={12} /> : <CheckCircle2 size={12} />}
                      {overrun
                        ? `Runs past its scene (≈${est.toFixed(1)}s in ${s.durationSecs.toFixed(0)}s)`
                        : `Fits its moment (≈${est.toFixed(1)}s in ${s.durationSecs.toFixed(0)}s)`}
                    </span>
                    <span
                      className={
                        collides
                          ? 'mt-0.5 flex items-center gap-1.5 text-xs text-danger-800'
                          : 'mt-0.5 flex items-center gap-1.5 text-xs text-success-800'
                      }
                    >
                      {collides ? <AlertTriangle size={12} /> : <CheckCircle2 size={12} />}
                      {collides
                        ? `Talks over dialogue (≈${collisions[s.id].overlapSecs.toFixed(1)}s)`
                        : 'Clear of dialogue'}
                    </span>
                  </>
                )}
              </button>
            </li>
          )
        })}
      </ul>

      <p className="shrink-0 border-t border-neutral-200 p-3 text-[11px] leading-relaxed text-neutral-500">
        Simple local timing checks: spoken time is estimated from word count (0.4 s per word,
        adjusted for playback speed) and compared against each scene's window and the film's
        dialogue map. Computed in your browser; they are not a measure of writing quality.
      </p>
    </aside>
  )
}
