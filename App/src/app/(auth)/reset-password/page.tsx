import type { Metadata } from 'next'
import Link from 'next/link'
import { ResetPasswordForm } from '@/app/_components/password-recovery-form'
import { Logo } from '@/components/ui/Logo'

export const metadata: Metadata = { title: 'Confirm password reset' }

export default function ResetPasswordPage() {
  return (
    <main className="grid min-h-screen place-items-center bg-neutral-50 px-5 py-12">
      <section className="w-full max-w-md rounded-2xl border border-neutral-200 bg-white p-8 shadow-sm sm:p-10">
        <Link href="/" className="inline-flex items-center gap-2 text-neutral-900"><Logo size={28} className="text-brand-400" /><span className="font-semibold">InstaDescribe</span></Link>
        <h1 className="mt-8 text-3xl font-semibold tracking-tight text-neutral-900">Enter confirmation code</h1>
        <p className="mt-2 text-sm text-neutral-500">Use the code sent by the organisation identity provider.</p>
        <ResetPasswordForm />
      </section>
    </main>
  )
}
