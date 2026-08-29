import type { Project } from '@/types'

export function formatDuration(secs?: number): string {
  if (!secs) return '—'
  const m = Math.floor(secs / 60)
  const s = Math.floor(secs % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

export function formatTokens(n?: number): string {
  if (!n) return '—'
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n)
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
}

// Saturated solid bg + white text + rounded-sm — see DESIGN.md Shapes
// (badges = `sm`) and Status Badges (label-caps 11px). Reference image:
// references/PillRef.png.
const PILL_BASE =
  'inline-flex items-center rounded-sm px-2 py-0.5 ' +
  'text-[11px] font-medium tracking-wide text-neutral-0'

export const STATUS_STYLES: Record<Project['status'], string> = {
  confirmation_pending: `${PILL_BASE} bg-warning-400`,
  ready:                `${PILL_BASE} bg-success-400`,
  processing:           `${PILL_BASE} bg-info-400`,
  draft:                `${PILL_BASE} bg-neutral-500`,
  failed:               `${PILL_BASE} bg-danger-400`,
}

export const STATUS_LABELS: Record<Project['status'], string> = {
  confirmation_pending: 'Upload pending',
  ready:                'Ready',
  processing:           'Processing',
  draft:                'Draft',
  failed:               'Failed',
}
