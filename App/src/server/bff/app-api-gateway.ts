import type {
  AuthenticatedSession,
  ProjectGateway,
  ProjectListResult,
  ProjectSummary,
  ProjectSummaryStatus,
  SessionPrincipal,
} from './contracts'
import type { MembershipResolver } from './ports'
import type { ProviderTokens } from './ports'
import { BROWSER_ASSERTION_HEADER, createBrowserAssertion } from './browser-assertion'

type ServerFetch = (input: string | URL | Request, init?: RequestInit) => Promise<Response>

const PROJECT_STATUSES = new Set<ProjectSummaryStatus>([
  'confirmation_pending',
  'processing',
  'ready',
  'draft',
  'failed',
])
const HUMAN_ROLES = new Set(['owner', 'editor', 'reviewer', 'viewer'] as const)

function exactKeys(record: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(record).sort()
  const expected = [...keys].sort()
  return actual.length === expected.length && actual.every((key, index) => key === expected[index])
}

function object(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function nonEmptyString(value: unknown, max = 512): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= max
}

function parsePrincipal(value: unknown): SessionPrincipal | null {
  const record = object(value)
  if (!record || !exactKeys(record, [
    'subject',
    'email',
    'displayName',
    'organizationId',
    'role',
    'mfaVerified',
  ])) return null
  if (
    !nonEmptyString(record.subject) ||
    !nonEmptyString(record.email, 254) ||
    !nonEmptyString(record.displayName) ||
    !nonEmptyString(record.organizationId) ||
    typeof record.role !== 'string' ||
    !HUMAN_ROLES.has(record.role as 'owner' | 'editor' | 'reviewer' | 'viewer') ||
    typeof record.mfaVerified !== 'boolean'
  ) return null
  // An unverified owner is returned only to SecureSessionGateway so it can
  // start server-side TOTP enrolment. That gateway remains the sole authority
  // that may create an opaque browser session, and it rejects this principal
  // until a later, freshly MFA-authenticated token resolves mfaVerified=true.
  return {
    subject: record.subject,
    email: record.email,
    displayName: record.displayName,
    organizationId: record.organizationId,
    role: record.role as SessionPrincipal['role'],
    mfaVerified: record.mfaVerified,
  }
}

function parseProject(value: unknown): ProjectSummary | null {
  const record = object(value)
  if (!record || !exactKeys(record, ['id', 'orgSlug', 'currentJobId', 'name', 'status', 'updatedAt'])) return null
  if (
    !nonEmptyString(record.id) ||
    !nonEmptyString(record.orgSlug) ||
    !(record.currentJobId === null || nonEmptyString(record.currentJobId)) ||
    !nonEmptyString(record.name, 1024) ||
    typeof record.status !== 'string' ||
    !PROJECT_STATUSES.has(record.status as ProjectSummaryStatus) ||
    !nonEmptyString(record.updatedAt, 128) ||
    !Number.isFinite(Date.parse(record.updatedAt))
  ) return null
  return {
    id: record.id,
    orgSlug: record.orgSlug,
    currentJobId: record.currentJobId,
    name: record.name,
    status: record.status as ProjectSummaryStatus,
    updatedAt: record.updatedAt,
  }
}

function normalizedApiOrigin(value: string, allowLoopbackHttp = false): string | null {
  try {
    const url = new URL(value)
    const loopback = url.hostname === 'localhost' || url.hostname === '127.0.0.1' || url.hostname === '::1'
    if (url.protocol !== 'https:' && !(allowLoopbackHttp && url.protocol === 'http:' && loopback)) return null
    if (url.username || url.password || url.search || url.hash || (url.pathname !== '/' && url.pathname !== '')) return null
    return url.origin
  } catch {
    return null
  }
}

export class AppApiGateway implements ProjectGateway, MembershipResolver {
  private readonly origin: string

  constructor(
    apiOrigin: string,
    private readonly browserAssertionSecret: Uint8Array,
    private readonly serverFetch: ServerFetch = fetch,
    allowLoopbackHttp = false,
  ) {
    const origin = normalizedApiOrigin(apiOrigin, allowLoopbackHttp)
    if (!origin) throw new Error('APP_API_ORIGIN must be an HTTPS origin')
    this.origin = origin
  }

  async resolve(tokens: ProviderTokens): Promise<SessionPrincipal | 'unavailable'> {
    const value = await this.getJson('/api/app/v1/session', tokens.accessToken, {
      email: tokens.email,
      mfaVerified: tokens.mfaVerified,
    }, 16_384)
    if (value === 'unavailable') return 'unavailable'
    return parsePrincipal(value) ?? 'unavailable'
  }

  async list(session: AuthenticatedSession): Promise<ProjectListResult> {
    const value = await this.getJson('/api/app/v1/projects', session.accessToken, session.principal, 1_048_576)
    if (value === 'unavailable') return { kind: 'unavailable' }
    const envelope = object(value)
    if (!envelope || !exactKeys(envelope, ['data']) || !Array.isArray(envelope.data) || envelope.data.length > 10_000) {
      return { kind: 'unavailable' }
    }
    const projects: ProjectSummary[] = []
    for (const candidate of envelope.data) {
      const project = parseProject(candidate)
      if (!project) return { kind: 'unavailable' }
      projects.push(project)
    }
    return { kind: 'ok', projects }
  }

  private async getJson(
    path: string,
    accessToken: string,
    identity: Pick<SessionPrincipal, 'email' | 'mfaVerified'>,
    maximumBytes: number,
  ): Promise<unknown | 'unavailable'> {
    if (!nonEmptyString(accessToken, 32_768)) return 'unavailable'
    const assertion = createBrowserAssertion(this.browserAssertionSecret, accessToken, identity)
    if (!assertion) return 'unavailable'
    let response: Response
    try {
      response = await this.serverFetch(new URL(path, this.origin), {
        method: 'GET',
        headers: {
          Accept: 'application/json',
          Authorization: `Bearer ${accessToken}`,
          [BROWSER_ASSERTION_HEADER]: assertion,
        },
        cache: 'no-store',
        credentials: 'omit',
        redirect: 'error',
        referrerPolicy: 'no-referrer',
        signal: AbortSignal.timeout(8_000),
      })
    } catch {
      return 'unavailable'
    }
    if (!response.ok || !response.headers.get('content-type')?.toLowerCase().startsWith('application/json')) {
      return 'unavailable'
    }
    try {
      const declaredLength = Number(response.headers.get('content-length') ?? 0)
      if (Number.isFinite(declaredLength) && declaredLength > maximumBytes) return 'unavailable'
      if (!response.body) return 'unavailable'
      const reader = response.body.getReader()
      const chunks: Uint8Array[] = []
      let received = 0
      while (true) {
        const { value, done } = await reader.read()
        if (done) break
        received += value.byteLength
        if (received > maximumBytes) {
          await reader.cancel()
          return 'unavailable'
        }
        chunks.push(value)
      }
      return JSON.parse(Buffer.concat(chunks, received).toString('utf8')) as unknown
    } catch {
      return 'unavailable'
    }
  }
}

export { normalizedApiOrigin }
