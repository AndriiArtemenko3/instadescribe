import { CircleSlash, Check, Plus, AlertTriangle } from 'lucide-react'
import { cn } from '@/lib/utils'
import { getSceneStatus } from '@/types'
import type { Scene, SceneStatus } from '@/types'
import type { SceneCollision } from '@/lib/collisions'

function formatTime(secs: number): string {
  const m = Math.floor(secs / 60)
  const s = Math.floor(secs % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

const STATUS_BORDER: Record<SceneStatus, string> = {
  ok:       'border-l-success-400',
  empty:    'border-l-warning-400',
  conflict: 'border-l-danger-400',
  inactive: 'border-l-neutral-200',
}

const STATUS_DOT: Record<SceneStatus, string> = {
  ok:       'bg-success-400',
  empty:    'bg-warning-400',
  conflict: 'bg-danger-400',
  inactive: 'bg-neutral-300',
}

interface SceneListPanelProps {
  scenes: Scene[]
  activeSceneId: number | null
  onSceneSelect: (scene: Scene) => void
  onActiveToggle: (sceneId: number) => void
  collisions: Record<number, SceneCollision>
  loading?: boolean
  readOnly?: boolean
}

export function SceneListPanel({
  scenes,
  activeSceneId,
  onSceneSelect,
  onActiveToggle,
  collisions,
  loading,
  readOnly = false,
}: SceneListPanelProps) {
  const activeCount = scenes.filter((s) => s.active).length
  const emptyCount  = scenes.filter((s) => s.active && !s.text.trim()).length

  return (
    <aside className="flex h-full w-[280px] shrink-0 flex-col border-r border-neutral-200 bg-neutral-100">
      {/* Header */}
      <div className="flex h-10 shrink-0 items-center border-b border-neutral-200 px-4">
        <span className="text-xs font-medium uppercase tracking-widest text-neutral-500">
          Scenes
        </span>
        <div className="ml-auto flex items-center gap-2">
          {emptyCount > 0 && (
            <span className="text-xs text-warning-400">{emptyCount} empty</span>
          )}
          <span className="text-xs text-neutral-400">
            {activeCount}/{scenes.length}
          </span>
        </div>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto p-2 space-y-1">
        {loading && (
          <p className="px-2 pt-4 text-center text-sm text-neutral-400">Loading scenes…</p>
        )}
        {!loading && scenes.length === 0 && (
          <p className="px-2 pt-4 text-center text-sm text-neutral-400">No scenes yet</p>
        )}

        {scenes.map((scene) => {
          const collision = collisions[scene.id]
          const status = getSceneStatus(scene, collision?.collides)
          const isActive = activeSceneId === scene.id

          return (
            <div
              key={scene.id}
              className={cn(
                'group relative flex rounded-lg border border-l-2 transition-all',
                STATUS_BORDER[status],
                isActive ? 'border-r-brand-400 border-t-brand-400 border-b-brand-400' : 'border-r-neutral-200 border-t-neutral-200 border-b-neutral-200',
                scene.active ? 'bg-neutral-0' : 'bg-neutral-100',
              )}
            >
              {/* Click area */}
              <button
                onClick={() => onSceneSelect(scene)}
                className="flex-1 p-3 text-left"
              >
                <div className="flex items-center justify-between mb-1">
                  <div className="flex items-center gap-1.5">
                    <span className={cn('h-1.5 w-1.5 rounded-full shrink-0', STATUS_DOT[status])} />
                    <span className={cn(
                      'text-xs font-medium',
                      scene.active ? 'text-neutral-900' : 'text-neutral-500',
                    )}>
                      Scene {scene.sceneNumber}
                    </span>
                    {!scene.active && (
                      <span className="inline-flex items-center gap-1 rounded-sm bg-neutral-200 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-neutral-600">
                        <CircleSlash size={10} strokeWidth={2} />
                        Inactive
                      </span>
                    )}
                  </div>
                  <span className="text-xs text-neutral-400">
                    {formatTime(scene.startSecs)} – {formatTime(scene.endSecs)}
                  </span>
                </div>

                <p className={cn(
                  'text-xs leading-relaxed line-clamp-2',
                  !scene.active
                    ? 'text-neutral-400'
                    : scene.text.trim() ? 'text-neutral-600' : 'italic text-neutral-400',
                )}>
                  {scene.text.trim() || 'No description yet'}
                </p>

                {status === 'conflict' && collision && (
                  <span
                    className="mt-2 inline-flex items-center gap-1 rounded-sm bg-danger-50 px-1.5 py-0.5 text-[10px] font-medium text-danger-400"
                    title={`This description talks over dialogue for about ${collision.overlapSecs.toFixed(1)}s.`}
                  >
                    <AlertTriangle size={10} strokeWidth={2} />
                    Conflict
                  </span>
                )}
              </button>

              {/* Active toggle — visible on hover or when inactive */}
              <button
                onClick={(e) => { e.stopPropagation(); onActiveToggle(scene.id) }}
                disabled={readOnly}
                title={scene.active ? 'Deactivate scene' : 'Activate scene'}
                aria-label={scene.active ? 'Deactivate scene' : 'Activate scene'}
                className={cn(
                  'flex shrink-0 items-start pt-3 pr-2 opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100 focus-visible:outline-none',
                  !scene.active && 'opacity-100',
                  readOnly && 'hidden',
                )}
              >
                <span className={cn(
                  'flex h-4 w-4 items-center justify-center rounded-sm border transition-colors',
                  scene.active
                    ? 'border-brand-400 bg-brand-400 text-neutral-0 hover:border-danger-400 hover:bg-danger-400'
                    : 'border-neutral-300 bg-neutral-0 text-neutral-400 hover:border-brand-400 hover:text-brand-400',
                )}>
                  {scene.active
                    ? <Check size={11} strokeWidth={2.5} />
                    : <Plus size={11} strokeWidth={2} />}
                </span>
              </button>
            </div>
          )
        })}
      </div>
    </aside>
  )
}
