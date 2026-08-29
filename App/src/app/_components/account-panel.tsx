'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import {
  BrowserIntegrationError,
  browserCsrfToken,
  inviteOrganizationMember,
} from '@/lib/browserIntegration'

interface UserView {
  email: string
  displayName: string
  organizationId: string
  role: 'owner' | 'editor' | 'reviewer' | 'viewer'
}

type AccountState =
  | { kind: 'loading' }
  | { kind: 'ready'; user: UserView }
  | { kind: 'signed_out'; message: string }
  | { kind: 'unavailable'; message: string }

export function AccountPanel() {
  const [state, setState] = useState<AccountState>({ kind: 'loading' })
  const [signingOut, setSigningOut] = useState(false)
  const [inviteEmail, setInviteEmail] = useState('')
  const [inviteRole, setInviteRole] = useState<'editor' | 'reviewer' | 'viewer'>('editor')
  const [inviting, setInviting] = useState(false)
  const [inviteMessage, setInviteMessage] = useState<string | null>(null)
  const [enrollingMfa, setEnrollingMfa] = useState(false)
  const [mfaMessage, setMfaMessage] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()

    async function load() {
      try {
        const response = await fetch('/api/bff/session', {
          credentials: 'same-origin',
          cache: 'no-store',
          signal: controller.signal,
        })
        const body = await response.json().catch(() => ({})) as {
          user?: UserView
          error?: { message?: string }
        }
        if (response.ok && body.user) {
          setState({ kind: 'ready', user: body.user })
        } else if (response.status === 401) {
          setState({ kind: 'signed_out', message: body.error?.message ?? 'You are signed out.' })
        } else {
          setState({ kind: 'unavailable', message: body.error?.message ?? 'Account service is unavailable.' })
        }
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') return
        setState({ kind: 'unavailable', message: 'Account service is unavailable.' })
      }
    }

    void load()
    return () => controller.abort()
  }, [])

  async function signOut() {
    setSigningOut(true)
    try {
      const csrf = document.cookie
        .split(';')
        .map((value) => value.trim())
        .find((value) => value.startsWith('__Host-instadescribe_csrf='))
        ?.slice('__Host-instadescribe_csrf='.length)
      await fetch('/api/bff/session', {
        method: 'DELETE',
        credentials: 'same-origin',
        headers: csrf ? { 'X-CSRF-Token': decodeURIComponent(csrf) } : undefined,
      })
    } finally {
      window.location.assign('/login')
    }
  }

  async function inviteMember(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setInviting(true)
    setInviteMessage(null)
    try {
      const invitation = await inviteOrganizationMember(inviteEmail, inviteRole)
      setInviteEmail('')
      setInviteMessage(`Invitation sent to ${invitation.email}.`)
    } catch (error) {
      setInviteMessage(
        error instanceof BrowserIntegrationError && error.code === 'invitation_conflict'
          ? 'That invitation could not be completed.'
          : 'Invitation is temporarily unavailable. Please try again.',
      )
    } finally {
      setInviting(false)
    }
  }

  async function enableMfa() {
    setEnrollingMfa(true)
    setMfaMessage(null)
    try {
      const response = await fetch('/api/bff/auth/mfa/enroll', {
        method: 'POST',
        credentials: 'same-origin',
        cache: 'no-store',
        redirect: 'error',
        headers: { 'X-CSRF-Token': browserCsrfToken() },
      })
      if (response.status === 202) {
        window.location.assign('/mfa?returnTo=%2Faccount')
        return
      }
      if (response.ok) {
        setMfaMessage('Authenticator MFA is already enabled for this account.')
        return
      }
      setMfaMessage('MFA enrollment is temporarily unavailable. Sign in again before retrying.')
    } catch {
      setMfaMessage('MFA enrollment is temporarily unavailable. Sign in again before retrying.')
    } finally {
      setEnrollingMfa(false)
    }
  }

  if (state.kind === 'loading') {
    return <div className="mt-6 h-48 animate-pulse rounded-xl border border-neutral-200 bg-white" aria-label="Loading account" />
  }

  if (state.kind === 'signed_out') {
    return (
      <section className="mt-6 rounded-xl border border-neutral-200 bg-white p-8">
        <p className="text-sm text-neutral-600">{state.message}</p>
        <Link href="/login" className="mt-5 inline-flex rounded-lg bg-brand-400 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500">Sign in</Link>
      </section>
    )
  }

  if (state.kind === 'unavailable') {
    return (
      <section className="mt-6 rounded-xl border border-warning-200 bg-warning-50 p-6">
        <h2 className="text-sm font-semibold text-neutral-900">Account service not connected</h2>
        <p className="mt-2 text-sm text-neutral-600">{state.message}</p>
      </section>
    )
  }

  return (
    <section className="mt-6 max-w-2xl rounded-xl border border-neutral-200 bg-white p-6 shadow-sm">
      <dl className="grid gap-5 sm:grid-cols-2">
        <div>
          <dt className="text-xs font-medium uppercase tracking-wide text-neutral-400">Name</dt>
          <dd className="mt-1 text-sm font-medium text-neutral-900">{state.user.displayName}</dd>
        </div>
        <div>
          <dt className="text-xs font-medium uppercase tracking-wide text-neutral-400">Email</dt>
          <dd className="mt-1 text-sm text-neutral-700">{state.user.email}</dd>
        </div>
        <div>
          <dt className="text-xs font-medium uppercase tracking-wide text-neutral-400">Organisation</dt>
          <dd className="mt-1 text-sm text-neutral-700">{state.user.organizationId}</dd>
        </div>
      </dl>
      <button
        type="button"
        disabled={signingOut}
        onClick={signOut}
        className="mt-7 rounded-lg border border-neutral-200 px-4 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-60"
      >
        {signingOut ? 'Signing out…' : 'Sign out'}
      </button>
      {state.user.role !== 'owner' ? (
        <div className="mt-8 border-t border-neutral-200 pt-6">
          <h2 className="text-sm font-semibold text-neutral-900">Authenticator MFA</h2>
          <p className="mt-1 text-sm text-neutral-500">
            Add a software authenticator voluntarily. Starting enrollment signs out this web session; after verification, sign in again with MFA.
          </p>
          <button
            type="button"
            disabled={enrollingMfa}
            onClick={enableMfa}
            className="mt-4 rounded-lg border border-neutral-200 px-4 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-60"
          >
            {enrollingMfa ? 'Starting MFA…' : 'Enable authenticator MFA'}
          </button>
          {mfaMessage ? <p className="mt-3 text-sm text-neutral-600" role="status">{mfaMessage}</p> : null}
        </div>
      ) : null}
      {state.user.role === 'owner' ? (
        <form className="mt-8 border-t border-neutral-200 pt-6" onSubmit={inviteMember}>
          <h2 className="text-sm font-semibold text-neutral-900">Invite a participant</h2>
          <p className="mt-1 text-sm text-neutral-500">
            The invited person receives a temporary-password email. Owner access and service accounts are not available here.
          </p>
          <div className="mt-4 grid gap-3 sm:grid-cols-[1fr_10rem_auto] sm:items-end">
            <label className="text-xs font-medium text-neutral-600">
              Email
              <input
                required
                type="email"
                autoComplete="email"
                maxLength={254}
                value={inviteEmail}
                onChange={(event) => setInviteEmail(event.target.value)}
                className="mt-1 block w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm text-neutral-900"
              />
            </label>
            <label className="text-xs font-medium text-neutral-600">
              Role
              <select
                value={inviteRole}
                onChange={(event) => setInviteRole(event.target.value as typeof inviteRole)}
                className="mt-1 block w-full rounded-lg border border-neutral-200 px-3 py-2 text-sm text-neutral-900"
              >
                <option value="editor">Editor</option>
                <option value="reviewer">Reviewer</option>
                <option value="viewer">Viewer</option>
              </select>
            </label>
            <button
              type="submit"
              disabled={inviting}
              className="rounded-lg bg-brand-400 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500 disabled:opacity-60"
            >
              {inviting ? 'Inviting…' : 'Invite'}
            </button>
          </div>
          {inviteMessage ? <p className="mt-3 text-sm text-neutral-600" role="status">{inviteMessage}</p> : null}
        </form>
      ) : null}
    </section>
  )
}
