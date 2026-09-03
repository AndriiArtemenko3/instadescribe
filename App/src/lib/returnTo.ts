const REVIEW_ROUTE = /^\/orgs\/[A-Za-z0-9_-]{1,128}\/projects\/[A-Za-z0-9_-]{1,128}\/jobs\/[A-Za-z0-9_-]{1,128}\/review$/
const UUID = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
const INVESTIGATION_ROUTE = new RegExp(`^/investigations/${UUID}(?:/report)?$`, 'i')
const PRODUCT_ROUTES = new Set([
  '/investigations',
  '/investigations/new',
  '/legacy/audio-description',
  '/projects',
  '/upload',
  '/account',
])
const DEFAULT_RETURN_TO = '/investigations'

/** Allowlisted relative post-auth destination; never accepts an origin, query or fragment. */
export function safeReturnTo(value: unknown): string {
  if (typeof value !== 'string' || value.length > 640 || value.includes('?') || value.includes('#')) return DEFAULT_RETURN_TO
  if (PRODUCT_ROUTES.has(value) || INVESTIGATION_ROUTE.test(value) || REVIEW_ROUTE.test(value)) return value
  return DEFAULT_RETURN_TO
}

export function withReturnTo(path: string, returnTo: string): string {
  return `${path}?returnTo=${encodeURIComponent(safeReturnTo(returnTo))}`
}
