import type { Metadata } from 'next'
import { AccountPanel } from '@/app/_components/account-panel'

export const metadata: Metadata = { title: 'Account' }

export default function AccountPage() {
  return (
    <>
      <p className="text-xs font-semibold uppercase tracking-widest text-brand-500">Workspace</p>
      <h1 className="mt-2 text-3xl font-semibold tracking-tight text-neutral-900">Account</h1>
      <p className="mt-2 text-sm text-neutral-500">Your organisation session and account details.</p>
      <AccountPanel />
    </>
  )
}
