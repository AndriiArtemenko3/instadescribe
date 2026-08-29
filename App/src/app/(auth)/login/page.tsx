import type { Metadata } from 'next'
import Link from 'next/link'
import { LoginForm } from '@/app/_components/login-form'
import { Logo } from '@/components/ui/Logo'
import { safeReturnTo } from '@/lib/returnTo'

export const metadata: Metadata = { title: 'Sign in' }

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ returnTo?: string | string[]; mfa?: string | string[] }>
}) {
  const parameters = await searchParams
  const value = parameters.returnTo
  const returnTo = safeReturnTo(Array.isArray(value) ? undefined : value)
  const enrolled = parameters.mfa === 'enrolled'
  return (
    <main className="grid min-h-screen place-items-center bg-neutral-50 px-5 py-12">
      <section className="w-full max-w-md rounded-2xl border border-neutral-200 bg-white p-8 shadow-sm sm:p-10">
        <Link href="/" className="inline-flex items-center gap-2 text-neutral-900">
          <Logo size={28} className="text-brand-400" />
          <span className="font-semibold">InstaDescribe</span>
        </Link>
        <h1 className="mt-8 text-3xl font-semibold tracking-tight text-neutral-900">Sign in</h1>
        <p className="mt-2 text-sm text-neutral-500">Use your organisation account to continue.</p>
        {enrolled && (
          <p role="status" className="mt-5 rounded-lg border border-success-200 bg-success-50 px-3 py-2 text-sm text-neutral-700">
            Authenticator enrolled. Sign in again and enter a fresh code to open your session.
          </p>
        )}
        <LoginForm returnTo={returnTo} />
        <Link href="/forgot-password" className="mt-6 inline-flex text-sm font-medium text-brand-500 hover:text-brand-600">
          Forgot password?
        </Link>
      </section>
    </main>
  )
}
