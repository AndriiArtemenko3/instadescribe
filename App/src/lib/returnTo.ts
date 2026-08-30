const REVIEW_ROUTE = /^\/orgs\/[A-Za-z0-9_-]{1,128}\/projects\/[A-Za-z0-9_-]{1,128}\/jobs\/[A-Za-z0-9_-]{1,128}\/review$/
const PRODUCT_ROUTES = new Set(['/projects', '/upload', '/account'])

/** Allowlisted relative post-auth destination; never accepts an origin, query or fragment. */
export function safeReturnTo(value: unknown): string {
  if (typeof value !== 'string' || value.length > 640 || value.includes('?') || value.includes('#')) return '/projects'
  if (PRODUCT_ROUTES.has(value) || REVIEW_ROUTE.test(value)) return value
  return '/projects'
}

export function withReturnTo(path: string, returnTo: string): string {
  return `${path}?returnTo=${encodeURIComponent(safeReturnTo(returnTo))}`
}
