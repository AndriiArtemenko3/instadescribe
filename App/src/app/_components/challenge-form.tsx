'use client'

import { useEffect, useState, type FormEvent } from 'react'
import { safeReturnTo, withReturnTo } from '@/lib/returnTo'

type Challenge =
  | { type: 'new_password_required'; requiredAttributes: string[] }
  | { type: 'software_token_mfa' }
  | { type: 'mfa_setup'; totpSecret: string }

function nextChallenge(response: Response, challenge: Challenge | undefined, returnTo: string): boolean {
  if (response.status !== 202 || !challenge) return false
  window.location.assign(withReturnTo(
    challenge.type === 'new_password_required' ? '/accept-invite' : '/mfa',
    returnTo,
  ))
  return true
}

export function ChallengeForm({
  expected,
  returnTo = '/projects',
}: {
  expected: 'new_password_required' | 'mfa'
  returnTo?: string
}) {
  const destination = safeReturnTo(returnTo)
  const [challenge, setChallenge] = useState<Challenge | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    fetch('/api/bff/auth/challenge', {
      credentials: 'same-origin',
      cache: 'no-store',
      signal: controller.signal,
    }).then(async (response) => {
      const body = await response.json().catch(() => ({})) as { challenge?: Challenge; error?: { message?: string } }
      if (!response.ok || !body.challenge) throw new Error(body.error?.message ?? 'No active sign-in challenge.')
      const matches = expected === 'mfa'
        ? body.challenge.type === 'software_token_mfa' || body.challenge.type === 'mfa_setup'
        : body.challenge.type === expected
      if (!matches) {
        window.location.assign(withReturnTo(
          body.challenge.type === 'new_password_required' ? '/accept-invite' : '/mfa',
          destination,
        ))
        return
      }
      setChallenge(body.challenge)
    }).catch((error: unknown) => {
      if (error instanceof DOMException && error.name === 'AbortError') return
      setMessage(error instanceof Error ? error.message : 'No active sign-in challenge.')
    })
    return () => controller.abort()
  }, [destination, expected])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!challenge) return
    setSubmitting(true)
    setMessage(null)
    const data = new FormData(event.currentTarget)
    const payload = challenge.type === 'new_password_required'
      ? { type: challenge.type, newPassword: String(data.get('newPassword') ?? '') }
      : { type: challenge.type, code: String(data.get('code') ?? '') }
    try {
      const response = await fetch('/api/bff/auth/challenge', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      const body = await response.json().catch(() => ({})) as {
        challenge?: Challenge
        error?: { message?: string }
        reauthenticationRequired?: boolean
      }
      if (response.ok && body.reauthenticationRequired === true) {
        const query = new URLSearchParams({ mfa: 'enrolled', returnTo: destination })
        window.location.assign(`/login?${query.toString()}`)
        return
      }
      if (response.ok && response.status !== 202) {
        window.location.assign(destination)
        return
      }
      if (nextChallenge(response, body.challenge, destination)) return
      setMessage(body.error?.message ?? 'The challenge could not be completed.')
    } catch {
      setMessage('The challenge could not be completed.')
    } finally {
      setSubmitting(false)
    }
  }

  if (!challenge && !message) {
    return <div className="mt-8 h-32 animate-pulse rounded-xl bg-neutral-50" aria-label="Loading sign-in challenge" />
  }

  return (
    <form onSubmit={submit} className="mt-8 space-y-5">
      {challenge?.type === 'new_password_required' ? (
        <>
          {challenge.requiredAttributes.length > 0 && (
            <p className="rounded-lg border border-warning-200 bg-warning-50 px-3 py-2 text-xs text-neutral-600">
              This invitation also requires: {challenge.requiredAttributes.join(', ')}. Ask an administrator to pre-populate these attributes before retrying.
            </p>
          )}
          <div>
            <label htmlFor="new-password" className="text-sm font-medium text-neutral-700">New password</label>
            <input id="new-password" name="newPassword" type="password" autoComplete="new-password" required minLength={14} className="mt-2 h-10 w-full rounded-lg border border-neutral-200 px-3 text-sm outline-none focus:border-brand-400 focus:ring-2 focus:ring-brand-100" />
          </div>
        </>
      ) : challenge ? (
        <>
          {challenge.type === 'mfa_setup' && (
            <div className="rounded-lg border border-neutral-200 bg-neutral-50 p-4">
              <p className="text-xs font-medium text-neutral-600">Add this setup key to your authenticator app:</p>
              <code className="mt-2 block break-all text-sm text-neutral-900">{challenge.totpSecret}</code>
            </div>
          )}
          <div>
            <label htmlFor="mfa-code" className="text-sm font-medium text-neutral-700">Six-digit authenticator code</label>
            <input id="mfa-code" name="code" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} required className="mt-2 h-10 w-full rounded-lg border border-neutral-200 px-3 text-sm outline-none focus:border-brand-400 focus:ring-2 focus:ring-brand-100" />
          </div>
        </>
      ) : null}
      {message && <p role="alert" className="rounded-lg border border-danger-200 bg-danger-50 px-3 py-2 text-sm text-danger-500">{message}</p>}
      <button type="submit" disabled={!challenge || submitting} className="h-10 w-full rounded-lg bg-brand-400 px-4 text-sm font-semibold text-white disabled:opacity-50">
        {submitting ? 'Verifying…' : 'Continue'}
      </button>
    </form>
  )
}
