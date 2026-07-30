import { CircleSlash, Check, Plus, AlertTriangle } from 'lucide-react'
import { cn } from '@/lib/utils'
import { getSceneStatus } from '@/types'
import type { Scene, SceneStatus } from '@/types'
import type { SceneTiming } from '../lib/timing'

// Fork of the app's SceneListPanel for the demo: identical visual language,
// with always-visible focus indicators (the app version hides the toggle's
// focus outline) and aria-current on the selected scene.

function formatTime(secs: number): string {
  const m = Math.floor(secs / 60)
  const s = Math.floor(secs % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

const STATUS_BORDER: Record<SceneStatus, string> = {
  ok: 'border-l-success-400',
  empty: 'border-l-warning-400',
  conflict: 'border-l-danger-400',
  inactive: 'border-l-neutral-200',
}
const STATUS_NAME: Record<SceneStatus, string> = {
  ok: 'placed',
  empty: 'empty',
  conflict: 'conflict',
  inactive: 'switched off',
}
const STATUS_DOT: Record<SceneStatus, string> = {
  ok: 'bg-success-400',
  empty: 'bg-warning-400',
  conflict: 'bg-danger-400',
  inactive: 'bg-neutral-300',
}

interface DemoSceneListProps {
  scenes: Scene[]
  activeSceneId: number | null
  onSceneSelect: (scene: Scene) => void
  onActiveToggle: (sceneId: number) => void
  timings: Record<number, SceneTiming>
}

export function DemoSceneList({
  scenes,
  activeSceneId,
  onSceneSelect,
  onActiveToggle,
  timings,
}: DemoSceneListProps) {
  return (
    <aside
      className="flex h-full w-56 shrink-0 flex-col border-r border-neutral-200 bg-neutral-0"
      aria-label="Scene list"
    >
      <div className="flex h-10 shrink-0 items-center border-b border-neutral-200 px-4">
        <span className="text-xs font-medium text-neutral-500">
          Scenes
          <span className="mx-1 text-neutral-300">·</span>
          {scenes.filter((s) => s.active).length} of {scenes.length} on
        </span>
      </div>

      <ul className="flex-1 space-y-1 overflow-y-auto p-2">
        {scenes.map((scene) => {
          const timing = timings[scene.id]
          const status = getSceneStatus(scene, timing?.talksOverDialogue)
          const isSelected = activeSceneId === scene.id

          return (
            <li
              key={scene.id}
              className={cn(
                'group relative flex rounded-lg border border-l-2 transition-all',
                STATUS_BORDER[status],
                isSelected
                  ? 'border-b-brand-400 border-r-brand-400 border-t-brand-400'
                  : 'border-b-neutral-200 border-r-neutral-200 border-t-neutral-200',
                scene.active ? 'bg-neutral-0' : 'bg-neutral-100',
              )}
            >
              <button
                onClick={() => onSceneSelect(scene)}
                aria-current={isSelected ? 'true' : undefined}
                className="flex-1 rounded-lg p-2.5 text-left focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-brand-400"
              >
                <span className="mb-1 flex items-center justify-between">
                  <span className="flex items-center gap-1.5">
                    <span className={cn('h-1.5 w-1.5 shrink-0 rounded-full', STATUS_DOT[status])} />
                    <span
                      className={cn(
                        'text-xs font-medium',
                        scene.active ? 'text-neutral-900' : 'text-neutral-600',
                      )}
                    >
                      Scene {scene.sceneNumber}
                      {/* Status is never colour-only: chips cover conflict/off,
                          and every row names its status for assistive tech. */}
                      <span className="pd-visually-hidden">, {STATUS_NAME[status]}</span>
                    </span>
                  </span>
                  <span
                    className={cn(
                      'text-xs',
                      scene.active ? 'text-neutral-500' : 'text-neutral-600',
                    )}
                  >
                    {formatTime(scene.startSecs)}–{formatTime(scene.endSecs)}
                  </span>
                </span>

                <span
                  className={cn(
                    'line-clamp-2 block text-xs leading-relaxed',
                    !scene.active
                      ? 'text-neutral-600'
                      : scene.text.trim()
                        ? 'text-neutral-600'
                        : 'italic text-neutral-600',
                  )}
                >
                  {scene.text.trim() || 'No description yet'}
                </span>

                {status === 'conflict' && timing && (
                  <span
                    className="mt-1.5 inline-flex items-center gap-1 rounded-sm bg-danger-50 px-1.5 py-0.5 text-[10px] font-medium text-danger-800"
                    title={`This description talks over dialogue for about ${timing.rawOverlapSecs.toFixed(1)}s.`}
                  >
                    <AlertTriangle size={10} strokeWidth={2} />
                    Conflict
                  </span>
                )}
                {!scene.active && (
                  <span className="mt-1.5 inline-flex items-center gap-1 rounded-sm bg-neutral-200 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-neutral-600">
                    <CircleSlash size={10} strokeWidth={2} />
                    Off — not in the mix
                  </span>
                )}
              </button>

              <button
                onClick={(e) => {
                  e.stopPropagation()
                  onActiveToggle(scene.id)
                }}
                title={scene.active ? 'Switch this line off' : 'Switch this line on'}
                aria-label={
                  scene.active
                    ? `Switch scene ${scene.sceneNumber} off`
                    : `Switch scene ${scene.sceneNumber} on`
                }
                className={cn(
                  'flex shrink-0 items-start rounded pr-2 pt-2.5 opacity-0 transition-opacity focus-visible:opacity-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-400 group-hover:opacity-100',
                  !scene.active && 'opacity-100',
                )}
              >
                <span
                  className={cn(
                    'flex h-4 w-4 items-center justify-center rounded-sm border transition-colors',
                    scene.active
                      ? 'border-brand-600 bg-brand-600 text-neutral-0 hover:border-danger-400 hover:bg-danger-400'
                      : 'border-neutral-300 bg-neutral-0 text-neutral-500 hover:border-brand-600 hover:text-brand-600',
                  )}
                >
                  {scene.active ? (
                    <Check size={11} strokeWidth={2.5} />
                  ) : (
                    <Plus size={11} strokeWidth={2} />
                  )}
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </aside>
  )
}
