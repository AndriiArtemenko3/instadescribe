import { lazy, Suspense } from 'react'
import { createBrowserRouter, Link, RouterProvider } from 'react-router-dom'
import { IntroStage } from './IntroStage'

// The onboarding editor is code-split so the intro payload stays light: nothing
// editor-sized (or any media) loads before "Start now".
const OnboardingPage = lazy(() => import('./OnboardingPage'))
const FigureReviewHarness = import.meta.env.DEV
  ? lazy(() => import('./FigureReviewHarness'))
  : null

function NotFound() {
  return (
    <main className="pd-intro" aria-labelledby="pd-nf-title">
      <div className="pd-intro-inner">
        <p className="pd-kicker">INSTASCRIBE · LIVE ONBOARDING</p>
        <h1 id="pd-nf-title" className="pd-title" style={{ fontSize: '1.5rem' }}>
          That page isn't part of this demo.
        </h1>
        <p className="pd-support">
          <Link to="/" className="pd-link">
            Back to the walkthrough intro
          </Link>
        </p>
      </div>
    </main>
  )
}

function Loading() {
  return (
    <main className="pd-intro">
      <p className="pd-kicker" role="status">
        LOADING…
      </p>
    </main>
  )
}

const router = createBrowserRouter([
  { path: '/', element: <IntroStage /> },
  {
    path: '/onboarding',
    element: (
      <Suspense fallback={<Loading />}>
        <OnboardingPage />
      </Suspense>
    ),
  },
  ...(FigureReviewHarness
    ? [
        {
          path: '/review/figure-00',
          element: (
            <Suspense fallback={<Loading />}>
              <FigureReviewHarness />
            </Suspense>
          ),
        },
      ]
    : []),
  { path: '*', element: <NotFound /> },
])

export function DemoRoot() {
  return <RouterProvider router={router} />
}
