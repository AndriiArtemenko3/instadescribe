// Demo entry. Auto-provisions a fresh clip and drops the visitor straight into the
// editor, where the onboarding walkthrough plays. No consent gate, since this is a
// product demo rather than a research study. Still mounted at /study so the existing
// build flag (VITE_STUDY_MODE) and Fly deploy stay untouched.
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Loader2 } from 'lucide-react'
import { Logo } from '@/components/ui/Logo'
import { useAppStore } from '@/store/appStore'
import { provisionStudySession, logEvent } from '@/lib/session'

export default function StudyIntro() {
  const navigate = useNavigate()
  const addProject = useAppStore((s) => s.addProject)
  const [error, setError] = useState<string | null>(null)
  const startedRef = useRef(false)

  async function start() {
    setError(null)
    try {
      const project = await provisionStudySession()
      addProject(project)
      logEvent('session_start', { projectId: project.id })
      navigate(`/editor/${project.id}`, { replace: true })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not start. Please try again.')
    }
  }

  // Provision once on load (the ref guards against React StrictMode's double-run).
  useEffect(() => {
    if (startedRef.current) return
    startedRef.current = true
    void start()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="min-h-svh bg-neutral-50 flex flex-col items-center justify-center px-4">
      <div className="flex items-center gap-2 mb-6">
        <Logo size={24} className="text-brand-400" />
        <span className="text-base font-semibold text-neutral-900">InstaDescribe</span>
      </div>

      {error ? (
        <div className="w-full max-w-[360px] text-center">
          <p role="alert" className="rounded-md bg-danger-50 p-3 text-sm text-danger-400">
            {error}
          </p>
          <button
            onClick={() => { startedRef.current = true; void start() }}
            className="mt-4 inline-flex items-center justify-center gap-2 rounded-md bg-brand-400 px-4 h-btn text-sm font-medium text-neutral-0 transition-colors hover:bg-brand-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-400"
          >
            Try again
          </button>
        </div>
      ) : (
        <div className="flex items-center gap-2 text-sm text-neutral-500">
          <Loader2 size={16} className="animate-spin" aria-hidden />
          Preparing your InstaDescribe demo…
        </div>
      )}
    </div>
  )
}
