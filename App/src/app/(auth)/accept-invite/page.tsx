import type { Metadata } from 'next'
import Link from 'next/link'
import { ChallengeForm } from '@/app/_components/challenge-form'
import { Logo } from '@/components/ui/Logo'
import { safeReturnTo } from '@/lib/returnTo'

export const metadata: Metadata = { title: 'Accept invitation' }

export default async function AcceptInvitePage({
  searchParams,
}: {
  searchParams: Promise<{ returnTo?: string | string[] }>
}) {
  const value = (await searchParams).returnTo
  const returnTo = safeReturnTo(Array.isArray(value) ? undefined : value)
  return (
    <main className="grid min-h-screen place-items-center bg-neutral-50 px-5 py-12">
      <section className="w-full max-w-md rounded-2xl border border-neutral-200 bg-white p-8 shadow-sm sm:p-10">
        <Logo size={32} className="mx-auto text-brand-400" />
        <p className="mt-7 text-xs font-semibold uppercase tracking-widest text-brand-500">Organisation invitation</p>
        <h1 className="mt-3 text-3xl font-semibold tracking-tight text-neutral-900">Choose your permanent password</h1>
        <p className="mt-3 text-sm leading-6 text-neutral-500">This completes the protected Cognito invitation challenge. Provider challenge state stays encrypted on the server.</p>
        <ChallengeForm expected="new_password_required" returnTo={returnTo} />
        <Link href="/login" className="mt-7 inline-flex text-sm font-medium text-brand-500">Back to sign in</Link>
      </section>
    </main>
  )
}
