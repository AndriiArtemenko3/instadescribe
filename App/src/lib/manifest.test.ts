// G7 B4/B7: manifest identity checking, required-reference validation,
// refresh-before-expiry timing, sanitized artifact failures (no signed URL
// in errors), and plain-fetch artifact access.

import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchArtifactJson, fetchManifest, refreshDelayMs, validateManifest } from './manifest'
import { clearPortfolioToken, setPortfolioToken } from './portfolioToken'

const REF = {
  url: 'http://localhost:4566/bucket/key?X-Amz-Signature=SIGNED',
  contentType: 'application/json',
  sizeBytes: 2,
  checksumSha256: 'a'.repeat(64),
}

function validManifest() {
  return {
    projectId: 'proj-1',
    jobId: 'job-1',
    pipelineRevision: 'dev',
    expiresAt: new Date(Date.now() + 300_000).toISOString().replace(/\.\d+Z?$/, 'Z'),
    artifacts: {
      video: { ...REF, contentType: 'video/mp4' },
      scenes: REF,
      entities: REF,
      audioEvents: REF,
      placementGaps: REF,
      transcript: REF,
      posterJpg: null,
      posterAvif: null,
    },
  }
}

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('validateManifest', () => {
  it('accepts a valid manifest with null posters', () => {
    const m = validateManifest(validManifest(), 'proj-1', 'job-1')
    expect(m.artifacts.posterJpg).toBeNull()
  })

  it('rejects identity mismatches before any reference is used', () => {
    expect(() => validateManifest(validManifest(), 'other-project', 'job-1')).toThrow()
    expect(() => validateManifest(validManifest(), 'proj-1', 'other-job')).toThrow()
  })

  it('rejects a manifest missing any required reference', () => {
    for (const key of [
      'video',
      'scenes',
      'entities',
      'audioEvents',
      'placementGaps',
      'transcript',
    ]) {
      const broken = validManifest()
      delete (broken.artifacts as Record<string, unknown>)[key]
      expect(() => validateManifest(broken, 'proj-1', 'job-1')).toThrow()
    }
  })

  it('accepts a present optional poster', () => {
    const withPoster = validManifest()
    withPoster.artifacts.posterJpg = { ...REF, contentType: 'image/jpeg' }
    const m = validateManifest(withPoster, 'proj-1', 'job-1')
    expect(m.artifacts.posterJpg?.contentType).toBe('image/jpeg')
  })
})

describe('refreshDelayMs', () => {
  it('refreshes before expiry with a safety margin and a sane floor', () => {
    const manifest = validateManifest(validManifest(), 'proj-1', 'job-1')
    const delay = refreshDelayMs(manifest)
    expect(delay).toBeGreaterThan(0)
    expect(delay).toBeLessThan(300_000) // strictly before expiresAt
    const expired = { ...manifest, expiresAt: new Date(Date.now() - 1000).toISOString() }
    expect(refreshDelayMs(expired)).toBe(15_000) // floor, never a hot loop
  })
})

describe('fetchArtifactJson', () => {
  it('uses PLAIN fetch with pinned options and no token header', async () => {
    const spy = vi.fn(async () => new Response('[1]', { status: 200 }))
    vi.stubGlobal('fetch', spy)
    const data = await fetchArtifactJson<number[]>(REF, 'scenes')
    expect(data).toEqual([1])
    const [, init] = spy.mock.calls[0] as [string, RequestInit]
    expect(init.headers).toBeUndefined() // nothing attached — never the token
    expect(init.credentials).toBe('omit')
    expect(init.referrerPolicy).toBe('no-referrer')
    expect(init.redirect).toBe('error') // fail closed on any S3 redirect
  })

  it('errors carry only the artifact category and a safe class — never the URL', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('denied', { status: 403 })))
    const err = await fetchArtifactJson(REF, 'scenes').catch((e) => e as Error)
    expect(err.message).toBe('artifact scenes: expired-or-denied')
    expect(err.message).not.toContain('X-Amz-Signature')
    expect(err.message).not.toContain('localhost:4566')
  })
})

describe('fetchManifest error interpretation (G7.1 A)', () => {
  it('maps a 409 artifacts_not_ready to a NON-retryable conflict via the allowlist', async () => {
    setPortfolioToken('manifest-test-token')
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify({ detail: { code: 'artifacts_not_ready' } }), { status: 409 }),
      ),
    )
    const err = await fetchManifest('proj-1', 'job-1').catch((e) => e)
    expect(err.category).toBe('conflict')
    expect(err.retryable).toBe(false)
    expect(err.code).toBe('artifacts_not_ready')
    clearPortfolioToken()
    vi.unstubAllGlobals()
  })
})
