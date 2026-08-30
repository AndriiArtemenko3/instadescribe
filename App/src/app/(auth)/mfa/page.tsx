import type { Metadata } from 'next'
import Link from 'next/link'
import { ChallengeForm } from '@/app/_components/challenge-form'
import { Logo } from '@/components/ui/Logo'
import { safeReturnTo } from '@/lib/returnTo'

export const metadata: Metadata = { title: 'Verify authenticator' }

export default async function MfaPage({
  searchParams,
}: {
  searchParams: Promise<{ returnTo?: string | string[] }>
}) {
  const value = (await searchParams).returnTo
  const returnTo = safeReturnTo(Array.isArray(value) ? undefined : value)
  return (
    <main className="grid min-h-screen place-items-center bg-neutral-50 px-5 py-12">
      <section className="w-full max-w-md rounded-2xl border border-neutral-200 bg-white p-8 shadow-sm sm:p-10">
        <Link href="/" className="inline-flex items-center gap-2 text-neutral-900"><Logo size={28} className="text-brand-400" /><span className="font-semibold">InstaDescribe</span></Link>
        <h1 className="mt-8 text-3xl font-semibold tracking-tight text-neutral-900">Verify authenticator</h1>
        <p className="mt-2 text-sm text-neutral-500">Complete the software-token challenge to continue.</p>
        <ChallengeForm expected="mfa" returnTo={returnTo} />
        <Link href="/login" className="mt-7 inline-flex text-sm font-medium text-brand-500">Start over</Link>
      </section>
    </main>
  )
}
