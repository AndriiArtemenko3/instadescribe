export default function Loading() {
  return (
    <main className="grid min-h-screen place-items-center bg-neutral-50" aria-busy="true">
      <div className="flex items-center gap-3 text-sm text-neutral-500">
        <span className="h-5 w-5 animate-spin rounded-full border-2 border-neutral-200 border-t-brand-400" />
        Loading InstaDescribe…
      </div>
    </main>
  )
}
