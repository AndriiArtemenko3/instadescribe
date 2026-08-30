'use client'

export default function GlobalError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <main className="grid min-h-screen place-items-center bg-neutral-50 px-6">
      <section className="w-full max-w-md rounded-xl border border-neutral-200 bg-white p-8 text-center shadow-sm">
        <p className="text-xs font-semibold uppercase tracking-widest text-danger-500">Something went wrong</p>
        <h1 className="mt-3 text-2xl font-semibold text-neutral-900">This view could not be loaded.</h1>
        <p className="mt-2 text-sm text-neutral-500">No changes were made. You can safely try the request again.</p>
        <button
          type="button"
          onClick={reset}
          className="mt-6 rounded-lg bg-brand-400 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500"
        >
          Try again
        </button>
      </section>
    </main>
  )
}
