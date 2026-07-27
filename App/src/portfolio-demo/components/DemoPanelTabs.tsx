import { cn } from '@/lib/utils'

// Deliberately ordinary buttons rather than an ARIA tab composite: three
// controls that swap the right-hand panel, marked with aria-current.
export type DemoTab = 'script' | 'characters' | 'checks'

const TABS: { id: DemoTab; label: string }[] = [
  { id: 'script', label: 'Script' },
  { id: 'characters', label: 'Characters' },
  { id: 'checks', label: 'Checks' },
]

interface Props {
  active: DemoTab
  onChange: (tab: DemoTab) => void
}

export function DemoPanelTabs({ active, onChange }: Props) {
  return (
    <div className="flex h-10 shrink-0 items-stretch border-b border-neutral-200 bg-neutral-0">
      {TABS.map((t) => (
        <button
          key={t.id}
          onClick={() => onChange(t.id)}
          aria-current={active === t.id ? 'true' : undefined}
          className={cn(
            'flex-1 border-b-2 text-xs font-medium transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-brand-400',
            active === t.id
              ? 'border-brand-400 text-neutral-900'
              : 'border-transparent text-neutral-500 hover:text-neutral-900',
          )}
        >
          {t.label}
        </button>
      ))}
    </div>
  )
}
