import { useEffect, useState } from 'react'
import { Loader2, Check, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { Entity, Scene } from '@/types'
import { RightPanelTabs, type RightPanelTab } from './RightPanelTabs'

interface CharactersPanelProps {
  entities: Entity[]
  scenes: Scene[]
  activeTab: RightPanelTab
  onTabChange: (tab: RightPanelTab) => void
  onRename: (characterId: string, newName: string) => Promise<void>
  /** Cloud mode: entity rename is deferred to the Portfolio Strong release. */
  cloudDeferred?: boolean
}

function sceneCountFor(entityId: string, scenes: Scene[]): number {
  return scenes.reduce((acc, s) => acc + (s.characterIds.includes(entityId) ? 1 : 0), 0)
}

export function CharactersPanel({
  entities, scenes, activeTab, onTabChange, onRename, cloudDeferred = false,
}: CharactersPanelProps) {
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [savingId, setSavingId] = useState<string | null>(null)
  const [errorId, setErrorId] = useState<{ id: string; msg: string } | null>(null)

  useEffect(() => {
    setDrafts({})
    setErrorId(null)
  }, [entities])

  async function commit(entity: Entity) {
    if (cloudDeferred) return // the input is disabled; nothing to report
    const next = (drafts[entity.id] ?? entity.name).trim()
    if (!next || next === entity.name) {
      setDrafts((d) => { const { [entity.id]: _, ...rest } = d; return rest })
      return
    }
    setSavingId(entity.id)
    setErrorId(null)
    try {
      await onRename(entity.id, next)
      setDrafts((d) => { const { [entity.id]: _, ...rest } = d; return rest })
    } catch (err) {
      setErrorId({ id: entity.id, msg: err instanceof Error ? err.message : String(err) })
    } finally {
      setSavingId(null)
    }
  }

  function cancel(entityId: string) {
    setDrafts((d) => { const { [entityId]: _, ...rest } = d; return rest })
    setErrorId(null)
  }

  return (
    <aside className="flex h-full w-script-panel shrink-0 flex-col border-l border-neutral-200 bg-neutral-0">
      <RightPanelTabs active={activeTab} onChange={onTabChange} />

      {entities.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 p-6 text-center">
          {cloudDeferred && (
            <p id="characters-deferred-note" className="text-xs text-neutral-400">
              Character rename — coming in v0.2.
            </p>
          )}
          <p className="text-sm text-neutral-400">
            No characters detected yet. The pipeline populates this list during analysis.
          </p>
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          {cloudDeferred && (
            <p id="characters-deferred-note" className="text-xs text-neutral-400">
              Character rename — coming in v0.2.
            </p>
          )}
          {entities.map((e) => {
            const count = sceneCountFor(e.id, scenes)
            const draft = drafts[e.id]
            const isDirty = draft !== undefined && draft.trim() !== e.name
            const isSaving = savingId === e.id
            const err = errorId?.id === e.id ? errorId.msg : null

            return (
              <div
                key={e.id}
                className="rounded-lg border border-neutral-200 bg-neutral-50 p-3 space-y-2"
              >
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-[10px] font-mono uppercase tracking-wider text-neutral-400">
                    {e.id}
                  </span>
                  <span className="text-xs text-neutral-400">
                    {count} {count === 1 ? 'scene' : 'scenes'}
                  </span>
                </div>

                <div className="flex items-center gap-2">
                  <input
                    type="text"
                    aria-label={`Rename ${e.name}`}
                    className={cn(
                      'flex-1 rounded border border-neutral-200 bg-neutral-0 px-2 py-1.5 text-sm text-neutral-900 outline-none transition-colors',
                      isDirty ? 'border-brand-400' : 'focus:border-brand-400',
                    )}
                    value={draft ?? e.name}
                    onChange={(ev) =>
                      setDrafts((d) => ({ ...d, [e.id]: ev.target.value }))
                    }
                    onKeyDown={(ev) => {
                      if (ev.key === 'Enter') { ev.preventDefault(); commit(e) }
                      else if (ev.key === 'Escape') { ev.preventDefault(); cancel(e.id) }
                    }}
                    disabled={isSaving || cloudDeferred}
                    aria-describedby={cloudDeferred ? 'characters-deferred-note' : undefined}
                  />
                  {isDirty && !isSaving && (
                    <>
                      <button
                        onClick={() => commit(e)}
                        className="rounded p-1 text-success-400 hover:bg-success-50 transition-colors"
                        title="Save (Enter)"
                      >
                        <Check size={14} />
                      </button>
                      <button
                        onClick={() => cancel(e.id)}
                        className="rounded p-1 text-neutral-400 hover:bg-neutral-150 hover:text-neutral-700 transition-colors"
                        title="Cancel (Esc)"
                      >
                        <X size={14} />
                      </button>
                    </>
                  )}
                  {isSaving && <Loader2 size={14} className="animate-spin text-neutral-500" />}
                </div>

                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-neutral-500">
                  {e.first_mention_label && e.first_mention_label !== e.name && (
                    <span>First seen as: <span className="text-neutral-700">{e.first_mention_label}</span></span>
                  )}
                  {e.pronoun && <span>Pronoun: <span className="text-neutral-700">{e.pronoun}</span></span>}
                  {e.user_renamed && <span className="rounded-full bg-brand-50 px-1.5 py-0.5 text-brand-500">renamed</span>}
                </div>

                {err && <p className="text-xs text-danger-500">{err}</p>}
              </div>
            )
          })}
        </div>
      )}

      <div className="shrink-0 border-t border-neutral-200 p-4">
        <p className="text-[11px] leading-relaxed text-neutral-400">
          Renaming a character rewrites every scene that references it. Locked
          scenes keep their existing text.
        </p>
      </div>
    </aside>
  )
}
