import { describe, expect, it } from 'vitest'
import { safeReturnTo, withReturnTo } from './returnTo'

describe('post-auth returnTo allowlist', () => {
  it('keeps investigation, legacy and canonical review routes', () => {
    const investigation = '/investigations/11111111-1111-4111-8111-111111111111'
    const review = '/orgs/acme/projects/111/jobs/222/review'
    expect(safeReturnTo('/investigations')).toBe('/investigations')
    expect(safeReturnTo('/investigations/new')).toBe('/investigations/new')
    expect(safeReturnTo(investigation)).toBe(investigation)
    expect(safeReturnTo(`${investigation}/report`)).toBe(`${investigation}/report`)
    expect(safeReturnTo('/legacy/audio-description')).toBe('/legacy/audio-description')
    expect(safeReturnTo('/upload')).toBe('/upload')
    expect(safeReturnTo(review)).toBe(review)
    expect(withReturnTo('/login', review)).toBe(`/login?returnTo=${encodeURIComponent(review)}`)
  })

  it('defaults to investigations for origins, malformed IDs, queries and unrelated routes', () => {
    for (const value of [
      undefined,
      'https://attacker.example',
      '//attacker.example',
      '/projects?next=x',
      '/investigations/not-a-uuid',
      '/investigations/11111111-1111-4111-8111-111111111111/raw',
      '/admin',
      `/${'a'.repeat(641)}`,
    ]) {
      expect(safeReturnTo(value)).toBe('/investigations')
    }
  })
})
