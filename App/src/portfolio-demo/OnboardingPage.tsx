import { useEffect, useState, useSyncExternalStore } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { loadDemoData, type DemoData } from './lib/fixtures'
import { DemoEditor } from './components/DemoEditor'
import TextWalkthrough from './TextWalkthrough'

// Initial embed-eligibility boundary per the contract; verified empirically in
// the review evidence (the three-panel editor keeps a usable center workspace
// at 1024px; below it the demo presents the full text fallback instead).
export const ELIGIBLE_WIDTH = 1024

const wideQuery =
  typeof window !== 'undefined' ? window.matchMedia(`(min-width: ${ELIGIBLE_WIDTH}px)`) : null

function subscribeWide(cb: () => void) {
  wideQuery?.addEventListener('change', cb)
  return () => wideQuery?.removeEventListener('change', cb)
}

export default function OnboardingPage() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const embed = params.get('embed') === '1'
  const textView = params.get('view') === 'text'
  const isWide = useSyncExternalStore(
    subscribeWide,
    () => wideQuery?.matches ?? true,
  )

  const [data, setData] = useState<DemoData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)
  // Bumping retryNonce re-runs the fetch effect — "Try again" genuinely
  // retries (previously the effect's deps never changed after a failure).
  const [retryNonce, setRetryNonce] = useState(0)

  const needsData = !textView && isWide
  useEffect(() => {
    if (!needsData || data) return
    let cancelled = false
    loadDemoData()
      .then((d) => {
        if (!cancelled) setData(d)
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      })
    return () => {
      cancelled = true
    }
  }, [needsData, data, retryNonce])

  if (textView) return <TextWalkthrough narrow={false} embed={embed} eligibleWidth={ELIGIBLE_WIDTH} />
  if (!isWide) return <TextWalkthrough narrow embed={embed} eligibleWidth={ELIGIBLE_WIDTH} />

  if (error) {
    return (
      <main className="pd-intro" aria-labelledby="pd-err-title">
        <div className="pd-intro-inner">
          <p className="pd-kicker">INSTASCRIBE · LIVE ONBOARDING</p>
          <h1 id="pd-err-title" className="pd-title" style={{ fontSize: '1.5rem' }}>
            The demo's local files didn't load.
          </h1>
          <p className="pd-support">
            This walkthrough runs entirely from static files bundled with the page; one of
            them failed to load ({error}). Nothing was sent anywhere.
          </p>
          <div className="pd-actions">
            <button
              type="button"
              className="pd-start"
              onClick={() => {
                setError(null)
                setData(null)
                setRetryNonce((n) => n + 1)
              }}
            >
              Try again
            </button>
          </div>
        </div>
      </main>
    )
  }

  if (!data) {
    return (
      <main className="pd-intro">
        <p className="pd-kicker" role="status" style={{ padding: 48 }}>
          LOADING THE EDITOR…
        </p>
      </main>
    )
  }

  return (
    <DemoEditor
      key={nonce}
      data={data}
      embed={embed}
      onRestart={() => {
        if (embed) setNonce((n) => n + 1)
        else navigate('/')
      }}
    />
  )
}
