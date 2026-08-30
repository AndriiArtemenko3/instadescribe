import type { Metadata } from 'next'
import Link from 'next/link'
import { ForgotPasswordForm } from '@/app/_components/password-recovery-form'
import { Logo } from '@/components/ui/Logo'

export const metadata: Metadata = { title: 'Reset password' }

export default function ForgotPasswordPage() {
  return (
    <main className="grid min-h-screen place-items-center bg-neutral-50 px-5 py-12">
      <section className="w-full max-w-md rounded-2xl border border-neutral-200 bg-white p-8 shadow-sm sm:p-10">
        <Link href="/" className="inline-flex items-center gap-2 text-neutral-900">
          <Logo size={28} className="text-brand-400" />
          <span className="font-semibold">InstaDescribe</span>
        </Link>
        <h1 className="mt-8 text-3xl font-semibold tracking-tight text-neutral-900">Reset password</h1>
        <p className="mt-2 text-sm text-neutral-500">Request a Cognito confirmation code. The response does not reveal whether an account exists.</p>
        <ForgotPasswordForm />
        <Link href="/login" className="mt-7 inline-flex text-sm font-medium text-brand-500 hover:text-brand-600">Back to sign in</Link>
      </section>
    </main>
  )
}
