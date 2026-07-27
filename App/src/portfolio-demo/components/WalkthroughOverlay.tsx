import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Check } from 'lucide-react'
import { Button } from '@/components/ui/button'

export interface WalkStep {
  id: string
  title: string
  body: string
  /** Spotlight target; omitted → centered card (e.g. the completion step). */
  selector?: string
  /** 'modal' dims + inerts the editor; 'action' leaves it interactive. */
  mode: 'modal' | 'action'
  /** Short imperative line shown on action steps. */
  actionHint?: string
  /** Announced + shown when an action step's condition is met. */
  doneNote?: string
}

interface Rect {
  top: number
  left: number
  width: number
  height: number
}

const PAD = 8
const GAP = 12
const CARD_W = 320
const CARD_EST_H = 220

function readRect(selector: string): Rect | null {
  const el = document.querySelector(selector)
  if (!el) return null
  const r = el.getBoundingClientRect()
  if (r.width === 0 && r.height === 0) return null
  return { top: r.top, left: r.left, width: r.width, height: r.height }
}

interface WalkthroughOverlayProps {
  steps: WalkStep[]
  index: number
  actionDone: boolean
  onNext: () => void
  onBack: () => void
  onExit: () => void
  /** Completion card's single secondary action. */
  onRestartDemo?: () => void
}

/**
 * The guided walkthrough. Explanation steps behave as a modal dialog (the
 * caller sets `inert` on the editor; focus is trapped here, Escape exits,
 * focus is restored by the caller). Action steps collapse to a task card so
 * the highlighted control stays genuinely usable; step completion is
 * announced via a polite live region.
 */
export function WalkthroughOverlay({
  steps,
  index,
  actionDone,
  onNext,
  onBack,
  onExit,
  onRestartDemo,
}: WalkthroughOverlayProps) {
  const step = steps[index]
  const isLast = index === steps.length - 1
  const [rect, setRect] = useState<Rect | null>(null)
  const cardRef = useRef<HTMLDivElement>(null)
  const primaryRef = useRef<HTMLButtonElement>(null)

  const recompute = useCallback(() => {
    setRect(step?.selector ? readRect(step.selector) : null)
  }, [step])

  useLayoutEffect(() => {
    recompute()
    // Re-measure once more after the step's onEnter side effects settle.
    const t = window.setTimeout(recompute, 60)
    return () => window.clearTimeout(t)
  }, [recompute])

  useEffect(() => {
    window.addEventListener('resize', recompute)
    window.addEventListener('scroll', recompute, true)
    return () => {
      window.removeEventListener('resize', recompute)
      window.removeEventListener('scroll', recompute, true)
    }
  }, [recompute])

  // Focus management: modal steps take focus in the card. For action steps
  // the title, rationale and instruction are announced FIRST (polite live
  // region), then focus moves to the highlighted control, which also carries
  // the card body as its accessible description for context on focus.
  const [announcement, setAnnouncement] = useState('')
  useEffect(() => {
    if (!step) return
    if (step.mode === 'modal') {
      setAnnouncement('')
      primaryRef.current?.focus()
      return
    }
    setAnnouncement(`${step.title}. ${step.body} ${step.actionHint ?? ''}`)
    const el = step.selector ? document.querySelector<HTMLElement>(step.selector) : null
    const target = el
      ? el.matches('button, a[href], [tabindex]')
        ? el
        : el.querySelector<HTMLElement>('button, a[href], [tabindex]')
      : null
    if (target) target.setAttribute('aria-describedby', `pd-walk-body-${step.id}`)
    const t = window.setTimeout(() => target?.focus(), 450)
    return () => {
      window.clearTimeout(t)
      target?.removeAttribute('aria-describedby')
    }
  }, [index, step])
  useEffect(() => {
    if (step?.mode === 'action' && actionDone) primaryRef.current?.focus()
  }, [actionDone, step])

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.preventDefault()
        onExit()
        return
      }
      // Trap Tab inside the card during modal steps (background is inert, but
      // the trap keeps focus from escaping to the browser chrome loop edges).
      if (e.key === 'Tab' && step?.mode === 'modal' && cardRef.current) {
        const focusables = Array.from(
          cardRef.current.querySelectorAll<HTMLElement>('button:not([disabled])'),
        )
        if (focusables.length === 0) return
        const first = focusables[0]
        const last = focusables[focusables.length - 1]
        const active = document.activeElement
        if (e.shiftKey && (active === first || !cardRef.current.contains(active))) {
          e.preventDefault()
          last.focus()
        } else if (!e.shiftKey && (active === last || !cardRef.current.contains(active))) {
          e.preventDefault()
          first.focus()
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onExit, step])

  if (!step) return null

  const modal = step.mode === 'modal'

  // Ring geometry.
  const ring = rect
    ? {
        top: rect.top - PAD,
        left: rect.left - PAD,
        width: rect.width + PAD * 2,
        height: rect.height + PAD * 2,
      }
    : null

  // Card placement: near the target for modal steps (below, else above, else
  // beside); pinned bottom-left for action steps so the target stays usable.
  let cardStyle: React.CSSProperties
  if (step.mode === 'action') {
    cardStyle = { left: GAP, bottom: GAP }
  } else if (ring) {
    const vw = window.innerWidth
    const vh = window.innerHeight
    const below = vh - (ring.top + ring.height)
    if (ring.height > vh * 0.6) {
      // Full-height panel: place the card BESIDE the ring so the spotlighted
      // content stays fully visible.
      const side =
        ring.left - CARD_W - GAP >= GAP
          ? ring.left - CARD_W - GAP
          : Math.min(ring.left + ring.width + GAP, vw - CARD_W - GAP)
      cardStyle = { top: Math.max(GAP, ring.top + 16), left: Math.max(GAP, side) }
    } else {
      const top =
        below > CARD_EST_H
          ? ring.top + ring.height + GAP
          : Math.max(GAP, ring.top - GAP - CARD_EST_H)
      const left = Math.min(Math.max(GAP, ring.left), Math.max(GAP, vw - CARD_W - GAP))
      cardStyle = { top, left }
    }
  } else {
    cardStyle = { top: '50%', left: '50%', transform: 'translate(-50%, -50%)' }
  }

  const card = (
    <div
      // Remount per step: the card appears at each step's position instead of
      // animating across the editor (which briefly covered action targets).
      key={step.id}
      ref={cardRef}
      role={modal ? 'dialog' : 'group'}
      aria-modal={modal || undefined}
      aria-labelledby={`pd-walk-title-${step.id}`}
      className="pd-tour-card"
      style={cardStyle}
    >
      <p className="font-mono text-[11px] tracking-wide text-neutral-500">
        STEP {index + 1} OF {steps.length}
      </p>
      <h2 id={`pd-walk-title-${step.id}`} className="mt-1 text-sm font-semibold text-neutral-900">
        {step.title}
      </h2>
      <p id={`pd-walk-body-${step.id}`} className="mt-1.5 text-sm leading-relaxed text-neutral-600">
        {step.body}
      </p>

      {step.mode === 'action' && (
        <p
          className={
            actionDone
              ? 'mt-2 flex items-center gap-1.5 text-sm font-medium text-success-800'
              : 'mt-2 text-sm font-medium text-neutral-900'
          }
        >
          {actionDone ? (
            <>
              <Check size={14} strokeWidth={2.5} />
              {step.doneNote ?? 'Done.'}
            </>
          ) : (
            <>→ {step.actionHint}</>
          )}
        </p>
      )}
      {/* Live regions: action-step announcement (before focus moves) and
          completion note for screen-reader users. */}
      <span className="pd-visually-hidden" aria-live="polite">
        {announcement}
      </span>
      <span className="pd-visually-hidden" role="status">
        {step.mode === 'action' && actionDone ? (step.doneNote ?? 'Step complete.') : ''}
      </span>

      {isLast ? (
        <div className="mt-4 flex items-center justify-end gap-2">
          {onRestartDemo && (
            <Button variant="outline" size="sm" onClick={onRestartDemo}>
              Restart demo
            </Button>
          )}
          <Button
            ref={primaryRef}
            size="sm"
            onClick={onNext}
            aria-label="Finish the walkthrough and explore the editor"
          >
            Explore the editor
          </Button>
        </div>
      ) : (
        <div className="mt-4 flex items-center justify-between gap-2">
          <Button variant="ghost" size="sm" onClick={onExit} aria-label="Skip the walkthrough">
            Skip tour
          </Button>
          <div className="flex items-center gap-2">
            {index > 0 && (
              <Button variant="outline" size="sm" onClick={onBack} aria-label="Previous step">
                Back
              </Button>
            )}
            <Button
              ref={primaryRef}
              size="sm"
              onClick={onNext}
              disabled={step.mode === 'action' && !actionDone}
              aria-label="Next step"
            >
              Next
            </Button>
          </div>
        </div>
      )}
    </div>
  )

  return (
    <div className="fixed inset-0 z-[60]" style={{ pointerEvents: modal ? 'auto' : 'none' }}>
      {ring && (
        <div
          aria-hidden="true"
          className={modal ? 'pd-tour-ring pd-tour-ring--dim' : 'pd-tour-ring'}
          style={ring}
        />
      )}
      {!ring && modal && (
        <div aria-hidden="true" className="fixed inset-0 bg-neutral-900/60" />
      )}
      <div style={{ pointerEvents: 'auto' }}>{card}</div>
    </div>
  )
}
