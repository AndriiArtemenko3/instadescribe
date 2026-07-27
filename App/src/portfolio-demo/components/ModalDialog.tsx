import { useEffect, useRef, type ReactNode } from 'react'
import { X } from 'lucide-react'

const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'

interface ModalDialogProps {
  titleId: string
  title: string
  onClose: () => void
  children: ReactNode
  maxWidth?: number
}

/**
 * Accessible modal: focus moves in on open, is trapped while open, Escape
 * closes, and focus returns to the previously focused element on close.
 * The caller marks the rest of the app `inert` while a modal is open.
 */
export function ModalDialog({ titleId, title, onClose, children, maxWidth = 640 }: ModalDialogProps) {
  const cardRef = useRef<HTMLDivElement>(null)
  const restoreRef = useRef<Element | null>(null)

  useEffect(() => {
    restoreRef.current = document.activeElement
    cardRef.current?.querySelector<HTMLElement>('[data-autofocus]')?.focus()
    return () => {
      if (restoreRef.current instanceof HTMLElement) restoreRef.current.focus()
    }
  }, [])

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onClose()
        return
      }
      if (e.key !== 'Tab') return
      const card = cardRef.current
      if (!card) return
      const focusables = Array.from(card.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (el) => el.offsetParent !== null || el === document.activeElement,
      )
      if (focusables.length === 0) return
      const first = focusables[0]
      const last = focusables[focusables.length - 1]
      const active = document.activeElement
      if (e.shiftKey && (active === first || !card.contains(active))) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && (active === last || !card.contains(active))) {
        e.preventDefault()
        first.focus()
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [onClose])

  return (
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center bg-neutral-900/60 p-4"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div
        ref={cardRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="max-h-[92vh] w-full overflow-y-auto rounded-xl bg-neutral-0 p-5 shadow-xl"
        style={{ maxWidth }}
      >
        <div className="mb-3 flex items-center justify-between gap-3">
          <h2 id={titleId} className="text-sm font-semibold text-neutral-900">
            {title}
          </h2>
          <button
            data-autofocus
            onClick={onClose}
            className="rounded p-1 text-neutral-500 transition-colors hover:bg-neutral-150 hover:text-neutral-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-400"
            aria-label={`Close ${title}`}
          >
            <X size={14} />
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}
