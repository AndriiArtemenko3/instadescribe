import type { Metadata } from 'next'
import Link from 'next/link'
import { ProjectsPanel } from '@/app/_components/projects-panel'

export const metadata: Metadata = { title: 'Projects' }

export default function ProjectsPage() {
  return (
    <>
      <div className="flex items-end justify-between gap-5">
        <div>
          <p className="text-xs font-semibold uppercase tracking-widest text-brand-500">Workspace</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight text-neutral-900">Projects</h1>
          <p className="mt-2 text-sm text-neutral-500">Review processing status and continue editing descriptions.</p>
        </div>
        <Link href="/upload" className="rounded-lg bg-brand-400 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500">
          Upload video
        </Link>
      </div>
      <ProjectsPanel />
    </>
  )
}
