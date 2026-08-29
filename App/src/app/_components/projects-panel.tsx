'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'

interface ProjectSummary {
  id: string
  orgSlug: string
  currentJobId: string | null
  name: string
  status: 'confirmation_pending' | 'processing' | 'ready' | 'draft' | 'failed'
  updatedAt: string
}

type ViewState =
  | { kind: 'loading' }
  | { kind: 'ready'; projects: ProjectSummary[] }
  | { kind: 'signed_out'; message: string }
  | { kind: 'unavailable'; message: string }

export function ProjectsPanel() {
  const [state, setState] = useState<ViewState>({ kind: 'loading' })

  useEffect(() => {
    const controller = new AbortController()

    async function load() {
      try {
        const response = await fetch('/api/bff/projects', {
          credentials: 'same-origin',
          cache: 'no-store',
          signal: controller.signal,
        })
        const body = await response.json().catch(() => ({})) as {
          projects?: ProjectSummary[]
          error?: { message?: string }
        }
        if (response.ok && Array.isArray(body.projects)) {
          setState({ kind: 'ready', projects: body.projects })
        } else if (response.status === 401) {
          setState({ kind: 'signed_out', message: body.error?.message ?? 'Sign in to view projects.' })
        } else {
          setState({ kind: 'unavailable', message: body.error?.message ?? 'Projects are unavailable.' })
        }
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') return
        setState({ kind: 'unavailable', message: 'Projects are unavailable.' })
      }
    }

    void load()
    return () => controller.abort()
  }, [])

  if (state.kind === 'loading') {
    return <div className="mt-6 h-36 animate-pulse rounded-xl border border-neutral-200 bg-white" aria-label="Loading projects" />
  }

  if (state.kind === 'signed_out') {
    return (
      <section className="mt-6 rounded-xl border border-neutral-200 bg-white p-8 text-center">
        <p className="text-sm text-neutral-600">{state.message}</p>
        <Link href="/login" className="mt-4 inline-flex rounded-lg bg-brand-400 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500">
          Sign in
        </Link>
      </section>
    )
  }

  if (state.kind === 'unavailable') {
    return (
      <section className="mt-6 rounded-xl border border-warning-200 bg-warning-50 p-6">
        <h2 className="text-sm font-semibold text-neutral-900">Project service not connected</h2>
        <p className="mt-2 text-sm text-neutral-600">{state.message}</p>
        <p className="mt-2 text-xs text-neutral-500">The App Router shell stays closed until its server-side session and project adapters are configured.</p>
      </section>
    )
  }

  if (state.projects.length === 0) {
    return (
      <section className="mt-6 rounded-xl border border-dashed border-neutral-300 bg-white p-10 text-center">
        <h2 className="font-semibold text-neutral-900">No projects yet</h2>
        <p className="mt-2 text-sm text-neutral-500">Upload a video to start an audio-description project.</p>
        <Link href="/upload" className="mt-5 inline-flex rounded-lg bg-brand-400 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500">
          Upload video
        </Link>
      </section>
    )
  }

  return (
    <ul className="mt-6 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      {state.projects.map((project) => (
        <li key={project.id} className="rounded-xl border border-neutral-200 bg-white p-5 shadow-sm">
          <div className="flex items-start justify-between gap-4">
            <h2 className="font-semibold text-neutral-900">{project.name}</h2>
            <span className="rounded-full bg-neutral-100 px-2 py-1 text-xs text-neutral-600">{project.status}</span>
          </div>
          <p className="mt-3 text-xs text-neutral-400">Updated {new Date(project.updatedAt).toLocaleString()}</p>
          {project.status === 'ready' && project.currentJobId && (
            <Link
              href={`/orgs/${encodeURIComponent(project.orgSlug)}/projects/${encodeURIComponent(project.id)}/jobs/${encodeURIComponent(project.currentJobId)}/review`}
              className="mt-5 inline-flex text-sm font-medium text-brand-500 hover:text-brand-600"
            >
              Open review →
            </Link>
          )}
        </li>
      ))}
    </ul>
  )
}
