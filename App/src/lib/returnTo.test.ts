import { describe, expect, it } from 'vitest'
import { safeReturnTo, withReturnTo } from './returnTo'

describe('post-auth returnTo allowlist', () => {
  it('keeps product and canonical review routes', () => {
    const review = '/orgs/acme/projects/111/jobs/222/review'
    expect(safeReturnTo('/upload')).toBe('/upload')
    expect(safeReturnTo(review)).toBe(review)
    expect(withReturnTo('/login', review)).toBe(`/login?returnTo=${encodeURIComponent(review)}`)
  })

  it('rejects origins, protocol-relative paths, queries and unrelated routes', () => {
    for (const value of ['https://attacker.example', '//attacker.example', '/projects?next=x', '/admin']) {
      expect(safeReturnTo(value)).toBe('/projects')
    }
  })
})
