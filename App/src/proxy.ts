import { NextResponse } from 'next/server'
import type { NextRequest } from 'next/server'
import { safeReturnTo } from '@/lib/returnTo'

const SESSION_COOKIE = '__Host-instadescribe_session'
const OPAQUE_SESSION = /^[A-Za-z0-9_-]{43}$/

/**
 * Optimistic navigation gate only. A cookie with the right opaque shape avoids
 * a needless login render; every BFF call still validates the encrypted
 * server-side session and organization membership before returning data.
 */
export function proxy(request: NextRequest) {
  const session = request.cookies.get(SESSION_COOKIE)?.value
  if (session && OPAQUE_SESSION.test(session)) return NextResponse.next()

  const login = request.nextUrl.clone()
  login.pathname = '/login'
  login.search = ''
  login.searchParams.set('returnTo', safeReturnTo(request.nextUrl.pathname))
  const response = NextResponse.redirect(login, 307)
  if (session) response.cookies.delete(SESSION_COOKIE)
  return response
}

export const config = {
  matcher: [
    '/investigations/:path*',
    '/legacy/:path*',
    '/projects',
    '/upload',
    '/account',
    '/orgs/:orgSlug/projects/:projectId/jobs/:jobId/review',
  ],
}
