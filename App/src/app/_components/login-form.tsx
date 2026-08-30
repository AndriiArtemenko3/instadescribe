'use client'

import { useState, type FormEvent } from 'react'
import { safeReturnTo, withReturnTo } from '@/lib/returnTo'

interface ErrorEnvelope {
  error?: { message?: string }
  challenge?: { type?: string }
}

function challengePath(type: string | undefined, returnTo: string): string | null {
  if (type === 'new_password_required') return withReturnTo('/accept-invite', returnTo)
  if (type === 'software_token_mfa' || type === 'mfa_setup') return withReturnTo('/mfa', returnTo)
  return null
}

export function LoginForm({ returnTo = '/projects' }: { returnTo?: string }) {
  const destination = safeReturnTo(returnTo)
  const [message, setMessage] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSubmitting(true)
    setMessage(null)
    const data = new FormData(event.currentTarget)

    try {
      const response = await fetch('/api/bff/session', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: String(data.get('email') ?? ''),
          password: String(data.get('password') ?? ''),
        }),
      })
      const body = (await response.json().catch(() => ({}))) as ErrorEnvelope
      if (response.ok && response.status !== 202) {
        window.location.assign(destination)
        return
      }
      if (response.status === 202) {
        const path = challengePath(body.challenge?.type, destination)
        if (path) {
          window.location.assign(path)
          return
        }
      }
      setMessage(body.error?.message ?? 'Sign-in could not be completed.')
    } catch {
      setMessage('Sign-in could not be completed.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={submit} className="mt-8 space-y-5">
      <div>
        <label htmlFor="email" className="text-sm font-medium text-neutral-700">Email</label>
        <input
          id="email"
          name="email"
          type="email"
          autoComplete="email"
          required
          className="mt-2 h-10 w-full rounded-lg border border-neutral-200 bg-white px-3 text-sm outline-none focus:border-brand-400 focus:ring-2 focus:ring-brand-100"
        />
      </div>
      <div>
        <label htmlFor="password" className="text-sm font-medium text-neutral-700">Password</label>
        <input
          id="password"
          name="password"
          type="password"
          autoComplete="current-password"
          required
          className="mt-2 h-10 w-full rounded-lg border border-neutral-200 bg-white px-3 text-sm outline-none focus:border-brand-400 focus:ring-2 focus:ring-brand-100"
        />
      </div>
      {message && (
        <p role="alert" className="rounded-lg border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-500">
          {message}
        </p>
      )}
      <button
        type="submit"
        disabled={submitting}
        className="h-10 w-full rounded-lg bg-brand-400 px-4 text-sm font-semibold text-white hover:bg-brand-500 disabled:cursor-not-allowed disabled:opacity-60"
      >
        {submitting ? 'Signing in…' : 'Sign in'}
      </button>
      <p className="text-xs leading-5 text-neutral-400">Credentials go only to the same-origin BFF. Provider tokens are kept server-side.</p>
    </form>
  )
}
