'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { EditorWorkspace } from '@/features/editor/pages/EditorPage'
import { useAppStore } from '@/store/appStore'
import { withReturnTo } from '@/lib/returnTo'
import { loadReviewAccess, type BrowserRole } from '@/lib/reviewAccess'

interface ReviewIdentity {
  orgSlug: string
  projectId: string
  jobId: string
}

type GateState = 'checking' | 'authorized' | 'signed_out' | 'not_found' | 'unavailable'

export function ReviewClient({ orgSlug, projectId, jobId }: ReviewIdentity) {
  const [state, setState] = useState<GateState>('checking')
  const [role, setRole] = useState<BrowserRole | null>(null)
  const reviewPath = `/orgs/${encodeURIComponent(orgSlug)}/projects/${encodeURIComponent(projectId)}/jobs/${encodeURIComponent(jobId)}/review`

  useEffect(() => {
    const controller = new AbortController()

    async function checkSession() {
      try {
        const access = await loadReviewAccess(
          { orgSlug, projectId, jobId },
          controller.signal,
        )
        if (access.kind === 'authorized') {
          const matched = access.project
          const store = useAppStore.getState()
          const hydrated = {
            id: matched.id,
            jobId,
            name: matched.name,
            status: 'ready' as const,
            createdAt: matched.updatedAt,
          }
          const existing = store.projects.find((project) => project.id === matched.id)
          if (existing) store.updateProject(matched.id, hydrated)
          else store.addProject(hydrated)
          setRole(access.role)
        }
        setState(access.kind)
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') return
        setState('unavailable')
      }
    }

    void checkSession()
    return () => controller.abort()
  }, [jobId, orgSlug, projectId])

  if (state === 'authorized' && role) {
    return <EditorWorkspace projectId={projectId} expectedJobId={jobId} backHref="/projects" browserRole={role} />
  }

  if (state === 'signed_out') {
    return (
      <main className="grid min-h-screen place-items-center bg-neutral-50 px-6">
        <section className="max-w-md rounded-xl border border-neutral-200 bg-white p-8 text-center shadow-sm">
          <h1 className="text-xl font-semibold text-neutral-900">Sign in to review this project.</h1>
          <Link href={withReturnTo('/login', reviewPath)} className="mt-5 inline-flex rounded-lg bg-brand-400 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500">Sign in</Link>
        </section>
      </main>
    )
  }

  if (state === 'unavailable') {
    return (
      <main className="grid min-h-screen place-items-center bg-neutral-50 px-6">
        <section className="max-w-md rounded-xl border border-warning-200 bg-warning-50 p-8 text-center">
          <h1 className="text-xl font-semibold text-neutral-900">Review is not available yet.</h1>
          <p className="mt-2 text-sm text-neutral-600">The editor remains locked until the server-side session adapter is connected.</p>
          <Link href="/projects" className="mt-5 inline-flex text-sm font-medium text-brand-500 hover:text-brand-600">Back to projects</Link>
        </section>
      </main>
    )
  }

  if (state === 'not_found') {
    return (
      <main className="grid min-h-screen place-items-center bg-neutral-50 px-6">
        <section className="max-w-md rounded-xl border border-neutral-200 bg-white p-8 text-center shadow-sm">
          <h1 className="text-xl font-semibold text-neutral-900">Review job not found.</h1>
          <p className="mt-2 text-sm text-neutral-600">This organisation, project, and job combination is not available to the current session.</p>
          <Link href="/projects" className="mt-5 inline-flex text-sm font-medium text-brand-500 hover:text-brand-600">Back to projects</Link>
        </section>
      </main>
    )
  }

  return (
    <main className="grid min-h-screen place-items-center bg-neutral-50" aria-busy="true">
      <div className="flex items-center gap-3 text-sm text-neutral-500">
        <span className="h-5 w-5 animate-spin rounded-full border-2 border-neutral-200 border-t-brand-400" />
        Verifying session…
      </div>
    </main>
  )
}
