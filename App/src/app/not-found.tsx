import Link from 'next/link'

export default function NotFound() {
  return (
    <main className="grid min-h-screen place-items-center bg-neutral-50 px-6">
      <section className="w-full max-w-md rounded-xl border border-neutral-200 bg-white p-8 text-center shadow-sm">
        <p className="text-xs font-semibold uppercase tracking-widest text-brand-500">404</p>
        <h1 className="mt-3 text-2xl font-semibold text-neutral-900">That page does not exist.</h1>
        <p className="mt-2 text-sm text-neutral-500">The URL may be old, or the project may have moved.</p>
        <Link
          href="/projects"
          className="mt-6 inline-flex rounded-lg bg-brand-400 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500"
        >
          Back to projects
        </Link>
      </section>
    </main>
  )
}
