import type { Metadata } from 'next'
import Link from 'next/link'
import { ProjectsPanel } from '@/app/_components/projects-panel'

export const metadata: Metadata = { title: 'Legacy audio description' }

export default function LegacyAudioDescriptionPage() {
  return (
    <>
      <div className="flex flex-col gap-5 border-b border-neutral-200 pb-6 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-widest text-neutral-400">Legacy workflow</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-tight text-neutral-900">Audio description</h1>
          <p className="mt-2 max-w-2xl text-sm text-neutral-500">
            Existing audio-description projects remain available during the product transition.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link
            href="/upload"
            className="rounded-lg border border-neutral-300 bg-white px-4 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50"
          >
            Upload legacy project
          </Link>
          <Link
            href="/investigations"
            className="rounded-lg bg-brand-400 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500"
          >
            Return to investigations
          </Link>
        </div>
      </div>
      <ProjectsPanel />
    </>
  )
}
