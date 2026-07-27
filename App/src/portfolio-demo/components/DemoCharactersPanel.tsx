import { useState } from 'react'
import { Check, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { Entity, Scene } from '@/types'
import { DemoPanelTabs, type DemoTab } from './DemoPanelTabs'

interface DemoCharactersPanelProps {
  entities: Entity[]
  scenes: Scene[]
  activeTab: DemoTab
  onTabChange: (tab: DemoTab) => void
  /** Deterministic local rename — re-renders caption templates in-browser. */
  onRename: (characterId: string, newName: string) => void
  protectedSceneCount: number
}

function sceneCountFor(entityId: string, scenes: Scene[]): number {
  return scenes.reduce((acc, s) => acc + (s.characterIds.includes(entityId) ? 1 : 0), 0)
}

/**
 * Character rename with real, local propagation: the demo re-renders each
 * scene's caption template with the new name (the same rule the pipeline
 * applies server-side), so the promise in the footer is genuinely kept.
 * Lines the visitor has edited by hand are left untouched.
 */
export function DemoCharactersPanel({
  entities,
  scenes,
  activeTab,
  onTabChange,
  onRename,
  protectedSceneCount,
}: DemoCharactersPanelProps) {
  const [editingId, setEditingId] = useState<string | null>(null)
  const [draft, setDraft] = useState('')

  function startEdit(e: Entity) {
    setEditingId(e.id)
    setDraft(e.name)
  }
  function commit() {
    const name = draft.trim()
    if (editingId && name) onRename(editingId, name)
    setEditingId(null)
  }

  return (
    <aside
      className="flex h-full w-script-panel shrink-0 flex-col border-l border-neutral-200 bg-neutral-0"
      aria-label="Characters panel"
    >
      <DemoPanelTabs active={activeTab} onChange={onTabChange} />

      <ul className="flex-1 space-y-2 overflow-y-auto p-4">
        {entities.map((entity) => (
          <li key={entity.id} className="rounded-lg border border-neutral-200 p-3">
            {editingId === entity.id ? (
              <form
                onSubmit={(e) => {
                  e.preventDefault()
                  commit()
                }}
                className="flex items-center gap-1.5"
              >
                <label htmlFor={`pd-rename-${entity.id}`} className="pd-visually-hidden">
                  New name for {entity.name}
                </label>
                <input
                  id={`pd-rename-${entity.id}`}
                  autoFocus
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Escape') setEditingId(null)
                  }}
                  className="w-full min-w-0 flex-1 rounded border border-neutral-200 bg-neutral-50 px-2 py-1 text-sm text-neutral-900 outline-none focus:border-brand-400"
                />
                <button
                  type="submit"
                  className="rounded p-1 text-success-800 hover:bg-success-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-400"
                  aria-label="Apply rename"
                >
                  <Check size={14} />
                </button>
                <button
                  type="button"
                  onClick={() => setEditingId(null)}
                  className="rounded p-1 text-neutral-500 hover:bg-neutral-150 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-400"
                  aria-label="Cancel rename"
                >
                  <X size={14} />
                </button>
              </form>
            ) : (
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-neutral-900">{entity.name}</p>
                  <p className="text-xs text-neutral-500">
                    {sceneCountFor(entity.id, scenes)} scenes · pronoun “{entity.pronoun}”
                    {entity.user_renamed && (
                      <span className={cn('ml-1 text-brand-800')}>· renamed</span>
                    )}
                  </p>
                </div>
                <button
                  onClick={() => startEdit(entity)}
                  className="shrink-0 rounded-lg border border-neutral-200 px-2.5 py-1 text-xs font-medium text-neutral-700 transition-colors hover:bg-neutral-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-400"
                >
                  Rename
                </button>
              </div>
            )}
          </li>
        ))}
      </ul>

      <p className="shrink-0 border-t border-neutral-200 p-3 text-[11px] leading-relaxed text-neutral-500">
        Renaming re-renders every still-drafted line that mentions the character — locally, in
        your browser, using the same template rule as the pipeline.
        {protectedSceneCount > 0 &&
          ` Lines you have edited or trimmed (${protectedSceneCount}) keep their current wording.`}{' '}
        Pre-generated audio still speaks the original names.
      </p>
    </aside>
  )
}
