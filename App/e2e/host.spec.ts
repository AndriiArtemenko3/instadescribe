import { test, expect } from '@playwright/test'

// Host-behavior assertions against the host-faithful server (see
// scripts/serve-host.mjs): genuine 404 boundary, the single SPA rewrite, and
// the security headers exactly as `_headers` declares them for the target
// host. Vite preview is deliberately NOT used — it proves nothing about
// `_headers`/`_redirects`.

test.describe('response statuses', () => {
  test('/ and /onboarding are served; /onboarding survives reload via _redirects', async ({
    request,
  }) => {
    for (const path of ['/', '/onboarding', '/onboarding?embed=1']) {
      const res = await request.get(path)
      expect(res.status(), path).toBe(200)
      expect(await res.text(), path).toContain('<div id="root">')
    }
  })

  test('application routes produce genuine non-200 not-found responses', async ({ request }) => {
    for (const path of ['/login', '/register', '/dashboard', '/upload', '/study', '/tutorials']) {
      const res = await request.get(path)
      expect(res.status(), path).toBe(404)
      expect(await res.text(), path).toContain("That page isn't part of this demo")
    }
  })

  test('the dev-only review harness route does not exist on the host', async ({ request }) => {
    const res = await request.get('/review/figure-00')
    expect(res.status()).toBe(404)
  })

  test('robots.txt disallows everything', async ({ request }) => {
    const res = await request.get('/robots.txt')
    expect(res.status()).toBe(200)
    expect(await res.text()).toContain('Disallow: /')
  })

  test('pruned application assets are genuinely absent (404)', async ({ request }) => {
    for (const path of [
      '/data/sintel-blender-cc/system_info.json',
      '/data/sintel-blender-cc/audio_events.json',
      '/demo/silence.mp3',
      '/icons.svg',
    ]) {
      expect((await request.get(path)).status(), path).toBe(404)
    }
  })
})

test.describe('security headers (from _headers)', () => {
  test('the root document carries the full security header set', async ({ request }) => {
    const res = await request.get('/')
    const h = res.headers()
    expect(h['x-robots-tag']).toBe('noindex, nofollow, noarchive')
    expect(h['x-content-type-options']).toBe('nosniff')
    expect(h['referrer-policy']).toBe('no-referrer')
    expect(h['permissions-policy']).toContain('autoplay=()')
    expect(h['permissions-policy']).toContain('camera=()')
    expect(h['content-security-policy']).toContain("default-src 'none'")
    expect(h['content-security-policy']).toContain(
      'frame-ancestors https://andriiartemenko.com https://www.andriiartemenko.com',
    )
    expect(h['x-frame-options']).toBeUndefined()
  })

  test('the 404 boundary carries the security headers too', async ({ request }) => {
    const res = await request.get('/definitely-not-a-page')
    expect(res.status()).toBe(404)
    expect(res.headers()['x-robots-tag']).toBe('noindex, nofollow, noarchive')
    expect(res.headers()['x-content-type-options']).toBe('nosniff')
  })

  test('hashed assets are immutable; media and data are day-cached', async ({ request }) => {
    const html = await (await request.get('/')).text()
    const asset = /\/assets\/[^"']+\.js/.exec(html)?.[0]
    expect(asset, 'entry script present in index.html').toBeTruthy()
    const assetRes = await request.get(asset!)
    expect(assetRes.status()).toBe(200)
    expect(assetRes.headers()['cache-control']).toBe('public, max-age=31536000, immutable')
    const mediaRes = await request.get('/videos/sintel-blender-cc.mp4', {
      headers: { range: 'bytes=0-1023' },
    })
    expect([200, 206]).toContain(mediaRes.status())
    expect(mediaRes.headers()['cache-control']).toBe('public, max-age=86400')
    const dataRes = await request.get('/data/sintel-blender-cc/scenes.json')
    expect(dataRes.headers()['cache-control']).toBe('public, max-age=86400')
  })
})
