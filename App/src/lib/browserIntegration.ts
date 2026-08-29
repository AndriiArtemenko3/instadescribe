const CSRF_COOKIE = '__Host-instadescribe_csrf'
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

export interface BrowserUploadContract {
  method: 'POST'
  url: string
  fields: Record<string, string>
  expiresAt: string
}

export interface BrowserCreatedJob {
  job: { id: string; projectId: string; state: string }
  uploads: { video: BrowserUploadContract; transcript?: BrowserUploadContract }
}

export interface BrowserOrganizationInvitation {
  invitationId: string
  email: string
  role: 'editor' | 'reviewer' | 'viewer'
  state: 'active'
}

export class BrowserIntegrationError extends Error {
  constructor(readonly code: string, readonly jobId?: string) {
    super(jobId ? `${code} (job ${jobId})` : code)
    this.name = 'BrowserIntegrationError'
  }
}

export function browserCsrfToken(cookie = typeof document === 'undefined' ? '' : document.cookie): string {
  const value = cookie
    .split(';')
    .map((item) => item.trim())
    .find((item) => item.startsWith(`${CSRF_COOKIE}=`))
    ?.slice(CSRF_COOKIE.length + 1)
  if (!value || !/^[A-Za-z0-9_-]{43}$/.test(value)) throw new BrowserIntegrationError('session_expired')
  return value
}

async function problemCode(response: Response): Promise<string> {
  try {
    const value = await response.json() as { code?: unknown; error?: { code?: unknown } }
    const code = value.code ?? value.error?.code
    return typeof code === 'string' && /^[a-z0-9_]{1,80}$/.test(code) ? code : 'request_failed'
  } catch {
    return 'request_failed'
  }
}

async function jsonWrite(path: string, body: unknown, idempotencyKey: string): Promise<unknown> {
  const response = await fetch(`/api/bff/cloud/${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': idempotencyKey,
      'X-CSRF-Token': browserCsrfToken(),
    },
    body: JSON.stringify(body),
    cache: 'no-store',
    credentials: 'same-origin',
    redirect: 'error',
    referrerPolicy: 'no-referrer',
  })
  if (!response.ok) throw new BrowserIntegrationError(await problemCode(response))
  try {
    return await response.json()
  } catch {
    throw new BrowserIntegrationError('invalid_response')
  }
}

function object(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

export async function inviteOrganizationMember(
  email: string,
  role: 'editor' | 'reviewer' | 'viewer',
): Promise<BrowserOrganizationInvitation> {
  const value = object(await jsonWrite(
    'organization/invitations',
    { email, role },
    crypto.randomUUID(),
  ))
  if (
    !value || !UUID.test(String(value.invitationId)) || typeof value.email !== 'string' ||
    value.role !== role || value.state !== 'active'
  ) throw new BrowserIntegrationError('invalid_response')
  return {
    invitationId: String(value.invitationId),
    email: value.email,
    role,
    state: 'active',
  }
}

function uploadContract(value: unknown): BrowserUploadContract | null {
  const item = object(value)
  const fields = object(item?.fields)
  if (item?.method !== 'POST' || typeof item.url !== 'string' || typeof item.expiresAt !== 'string' || !fields) return null
  let url: URL
  try {
    url = new URL(item.url)
  } catch {
    return null
  }
  const loopback = url.hostname === 'localhost' || url.hostname === '127.0.0.1' || url.hostname === '::1'
  if ((url.protocol !== 'https:' && !(url.protocol === 'http:' && loopback)) || url.username || url.password) return null
  const normalized: Record<string, string> = {}
  for (const [key, field] of Object.entries(fields)) {
    if (typeof field !== 'string') return null
    normalized[key] = field
  }
  return { method: 'POST', url: url.href, fields: normalized, expiresAt: item.expiresAt }
}

export async function createBrowserJob(body: unknown): Promise<BrowserCreatedJob> {
  const value = object(await jsonWrite('jobs', body, crypto.randomUUID()))
  const job = object(value?.job)
  const uploads = object(value?.uploads)
  const video = uploadContract(uploads?.video)
  const transcript = uploads?.transcript == null ? undefined : uploadContract(uploads.transcript)
  if (
    !job || !UUID.test(String(job.id)) || !UUID.test(String(job.projectId)) ||
    typeof job.state !== 'string' || !uploads || !video || (uploads.transcript != null && !transcript)
  ) throw new BrowserIntegrationError('invalid_response')
  return {
    job: { id: String(job.id), projectId: String(job.projectId), state: job.state },
    uploads: { video, ...(transcript ? { transcript } : {}) },
  }
}

export async function uploadBrowserFile(
  contract: BrowserUploadContract,
  file: File,
): Promise<void> {
  const form = new FormData()
  for (const [name, value] of Object.entries(contract.fields)) form.append(name, value)
  form.append('file', file, file.name)
  let response: Response
  try {
    response = await fetch(contract.url, {
      method: 'POST',
      body: form,
      credentials: 'omit',
      redirect: 'error',
      referrerPolicy: 'no-referrer',
    })
  } catch {
    throw new BrowserIntegrationError('upload_failed')
  }
  if (!response.ok) throw new BrowserIntegrationError('upload_failed')
}

export async function completeBrowserUpload(jobId: string): Promise<void> {
  if (!UUID.test(jobId)) throw new BrowserIntegrationError('invalid_job_id')
  try {
    await jsonWrite(`jobs/${jobId}/uploads/complete`, {}, crypto.randomUUID())
  } catch (error) {
    if (error instanceof BrowserIntegrationError) throw new BrowserIntegrationError(error.code, jobId)
    throw error
  }
}
