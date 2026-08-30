'use client'

import Link from 'next/link'
import { useState, type FormEvent } from 'react'

export function ForgotPasswordForm() {
  const [message, setMessage] = useState<string | null>(null)
  const [sent, setSent] = useState(false)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    setMessage(null)
    try {
      const response = await fetch('/api/bff/auth/forgot-password', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: String(data.get('email') ?? '') }),
      })
      const body = await response.json().catch(() => ({})) as { error?: { message?: string } }
      if (response.ok) {
        setSent(true)
        return
      }
      setMessage(body.error?.message ?? 'Recovery could not be started.')
    } catch {
      setMessage('Recovery could not be started.')
    }
  }

  if (sent) {
    return (
      <div className="mt-8 rounded-lg border border-success-200 bg-success-50 p-4 text-sm text-neutral-700">
        If that account exists, Cognito sent a confirmation code. <Link href="/reset-password" className="font-medium text-brand-500">Enter the code</Link>.
      </div>
    )
  }

  return (
    <form onSubmit={submit} className="mt-8 space-y-5">
      <div>
        <label htmlFor="recovery-email" className="text-sm font-medium text-neutral-700">Email</label>
        <input id="recovery-email" name="email" type="email" autoComplete="email" required className="mt-2 h-10 w-full rounded-lg border border-neutral-200 px-3 text-sm outline-none focus:border-brand-400 focus:ring-2 focus:ring-brand-100" />
      </div>
      {message && <p role="alert" className="text-sm text-danger-500">{message}</p>}
      <button type="submit" className="h-10 w-full rounded-lg bg-brand-400 px-4 text-sm font-semibold text-white">Send confirmation code</button>
    </form>
  )
}

export function ResetPasswordForm() {
  const [message, setMessage] = useState<string | null>(null)
  const [reset, setReset] = useState(false)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    setMessage(null)
    try {
      const response = await fetch('/api/bff/auth/reset-password', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: String(data.get('email') ?? ''),
          code: String(data.get('code') ?? ''),
          newPassword: String(data.get('newPassword') ?? ''),
        }),
      })
      const body = await response.json().catch(() => ({})) as { error?: { message?: string } }
      if (response.ok) {
        setReset(true)
        return
      }
      setMessage(body.error?.message ?? 'Password could not be reset.')
    } catch {
      setMessage('Password could not be reset.')
    }
  }

  if (reset) return <p className="mt-8 text-sm text-neutral-700">Password reset. <Link href="/login" className="font-medium text-brand-500">Sign in</Link>.</p>

  return (
    <form onSubmit={submit} className="mt-8 space-y-5">
      <div><label htmlFor="reset-email" className="text-sm font-medium text-neutral-700">Email</label><input id="reset-email" name="email" type="email" autoComplete="email" required className="mt-2 h-10 w-full rounded-lg border border-neutral-200 px-3 text-sm" /></div>
      <div><label htmlFor="reset-code" className="text-sm font-medium text-neutral-700">Confirmation code</label><input id="reset-code" name="code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" required className="mt-2 h-10 w-full rounded-lg border border-neutral-200 px-3 text-sm" /></div>
      <div><label htmlFor="reset-password" className="text-sm font-medium text-neutral-700">New password</label><input id="reset-password" name="newPassword" type="password" autoComplete="new-password" minLength={14} required className="mt-2 h-10 w-full rounded-lg border border-neutral-200 px-3 text-sm" /></div>
      {message && <p role="alert" className="text-sm text-danger-500">{message}</p>}
      <button type="submit" className="h-10 w-full rounded-lg bg-brand-400 px-4 text-sm font-semibold text-white">Reset password</button>
    </form>
  )
}
