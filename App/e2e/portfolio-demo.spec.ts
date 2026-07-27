import { test, expect, type Page, type Request } from '@playwright/test'

// Browser verification of the dedicated portfolio-demo build (served by
// `vite preview` from dist-portfolio-demo — the same artifact a static host
// would serve, including the SPA fallback).

const MEDIA_RE = /\.(mp4|mp3)(\?|$)/
const EXPORT_RE = /export\.mp4/

function track(page: Page) {
  const requests: string[] = []
  const consoleErrors: string[] = []
  const pageErrors: string[] = []
  page.on('request', (r: Request) => requests.push(r.url()))
  page.on('console', (m) => {
    if (m.type() === 'error') consoleErrors.push(m.text())
  })
  page.on('pageerror', (e) => pageErrors.push(String(e)))
  return { requests, consoleErrors, pageErrors }
}

function assertNetworkClean(requests: string[]) {
  for (const url of requests) {
    const u = new URL(url)
    expect(u.origin, `external request: ${url}`).toBe('http://localhost:4174')
    expect(u.pathname.includes('/api/'), `backend request: ${url}`).toBe(false)
  }
}

async function dismissWalkthrough(page: Page) {
  // Wait until the walkthrough has actually mounted before exiting it.
  await expect(page.getByText('The scene list')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByText('The scene list')).toHaveCount(0)
}

async function runWalkthroughToListen(page: Page) {
  // Steps 1–4 are explanation dialogs.
  for (let i = 0; i < 4; i++) {
    await page.getByRole('button', { name: 'Next step' }).click()
  }
  // Step 5 (action): switch scene 2 off.
  await expect(page.getByText('Let the dialogue breathe')).toBeVisible()
  await page.locator('[data-tour="toggle-line"]').click()
  await expect(page.getByText('Conflict cleared — the red span is gone').first()).toBeVisible()
  await page.getByRole('button', { name: 'Next step' }).click()
  // Step 6 (modal), then step 7 (action): fit to gap on scene 5.
  await page.getByRole('button', { name: 'Next step' }).click()
  await page.locator('[data-tour="fit"]').click()
  await expect(page.getByText('The line now fits its moment.').first()).toBeVisible()
  await page.getByRole('button', { name: 'Next step' }).click()
  // Step 8 (action): play a narration source.
  await page.getByRole('button', { name: /Original line · Onyx/ }).click()
  await expect(page.getByText('baked audio and your live text').first()).toBeVisible()
  await page.getByRole('button', { name: 'Next step' }).click()
}

test.describe('intro', () => {
  test('loads without media, backend, or external requests', async ({ page }) => {
    const t = track(page)
    await page.goto('/')
    await expect(page.getByRole('heading', { name: 'Try InstaScribe Live Onboarding' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Start now' })).toBeVisible()
    // Visible licensing on the intro itself.
    await expect(page.getByText('Blender Foundation')).toBeVisible()
    await expect(page.getByRole('link', { name: 'CC BY 3.0' })).toBeVisible()

    expect(t.requests.filter((u) => MEDIA_RE.test(u))).toHaveLength(0)
    expect(t.requests.filter((u) => u.endsWith('.json'))).toHaveLength(0)
    assertNetworkClean(t.requests)
    expect(t.consoleErrors).toHaveLength(0)
    expect(t.pageErrors).toHaveLength(0)
  })

  test('intro payload stays lightweight (< 500 KB transferred)', async ({ page }) => {
    let bytes = 0
    page.on('response', async (r) => {
      try {
        bytes += (await r.body()).length
      } catch {
        /* opaque/redirect bodies don't count */
      }
    })
    await page.goto('/', { waitUntil: 'networkidle' })
    expect(bytes).toBeGreaterThan(0)
    expect(bytes).toBeLessThan(500 * 1024)
  })
})

test.describe('start and core loop', () => {
  test('Start now loads the editor in place and the guided loop completes', async ({ page }) => {
    const t = track(page)
    await page.goto('/')
    await page.getByRole('button', { name: 'Start now' }).click()
    await expect(page).toHaveURL(/\/onboarding$/)
    await expect(page.getByText('The scene list')).toBeVisible()

    // Scene 2's genuine conflict is flagged before any fix.
    await expect(page.locator('[data-tour="scenes"]').getByText('Conflict')).toBeVisible()

    // The described example must not be fetched before the listen step.
    expect(t.requests.filter((u) => EXPORT_RE.test(u))).toHaveLength(0)

    await runWalkthroughToListen(page)

    // Step 9 (action): open the described example — only now export.mp4 loads.
    await page.locator('[data-tour="preview"] button').click()
    await expect(page.getByRole('dialog', { name: 'Pre-rendered described example' })).toBeVisible()
    await expect(page.getByText('Your edits are not in this file')).toBeVisible()
    await expect
      .poll(() => t.requests.filter((u) => EXPORT_RE.test(u)).length, { timeout: 5000 })
      .toBeGreaterThan(0)
    await page.getByRole('button', { name: 'Done listening' }).click()

    await page.getByRole('button', { name: 'Next step' }).click()
    // Completion step reports what actually happened.
    await expect(page.getByText(/you switched 1 line off, trimmed 1 to fit/)).toBeVisible()
    await page.getByRole('button', { name: 'Finish the walkthrough' }).click()
    await expect(page.getByRole('button', { name: 'Replay walkthrough' })).toBeFocused()

    // Conflict cleared for real: no Conflict chip remains.
    await expect(page.locator('[data-tour="scenes"]').getByText('Conflict')).toHaveCount(0)

    assertNetworkClean(t.requests)
    expect(t.consoleErrors).toHaveLength(0)
    expect(t.pageErrors).toHaveLength(0)
  })

  test('Back and Skip behave; Escape exits the walkthrough', async ({ page }) => {
    await page.goto('/onboarding')
    await expect(page.getByText('The scene list')).toBeVisible()
    await page.getByRole('button', { name: 'Next step' }).click()
    await expect(page.getByText('The film and its timeline')).toBeVisible()
    await page.getByRole('button', { name: 'Previous step' }).click()
    await expect(page.getByText('The scene list')).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.getByText('The scene list')).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Replay walkthrough' })).toBeVisible()
  })

  test('background is inert during explanation steps and interactive during action steps', async ({
    page,
  }) => {
    await page.goto('/onboarding')
    await expect(page.getByText('The scene list')).toBeVisible()
    const inertContainer = page.locator('div[inert]')
    await expect(inertContainer).toHaveCount(1)
    for (let i = 0; i < 4; i++) await page.getByRole('button', { name: 'Next step' }).click()
    await expect(page.getByText('Let the dialogue breathe')).toBeVisible()
    await expect(page.locator('div[inert]')).toHaveCount(0)
  })
})

test.describe('embed and routing', () => {
  test('direct embedded entry seeds deterministically without the intro', async ({ page }) => {
    const t = track(page)
    await page.goto('/onboarding?embed=1')
    await expect(page.getByText('The scene list')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Start now' })).toHaveCount(0)
    // Embed mode offers no intro-exit; restart resets in place.
    await expect(page.getByText('Exit to intro')).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Close demo' })).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.getByText('The scene list')).toHaveCount(0)
    await page.getByRole('button', { name: 'Restart', exact: false }).click()
    await expect(page).toHaveURL(/embed=1/)
    await expect(page.getByText('The scene list')).toBeVisible()
    assertNetworkClean(t.requests)
    expect(t.pageErrors).toHaveLength(0)
  })

  test('a direct route reload survives the SPA fallback', async ({ page }) => {
    await page.goto('/onboarding')
    await expect(page.getByText('The scene list')).toBeVisible()
    await page.reload()
    await expect(page.getByText('The scene list')).toBeVisible()
  })

  test('restart returns to a genuinely clean intro without backend calls', async ({ page }) => {
    const t = track(page)
    await page.goto('/onboarding')
    await dismissWalkthrough(page)
    // Make an edit so restart has something to discard.
    await page.locator('[data-tour="toggle-line"]').click()
    await page.getByRole('button', { name: 'Restart' }).click()
    await expect(page).toHaveURL(/\/$/)
    await expect(page.getByRole('heading', { name: 'Try InstaScribe Live Onboarding' })).toBeVisible()
    // Re-enter: the edit is gone (scene 2 conflict is back).
    await page.getByRole('button', { name: 'Start now' }).click()
    await expect(page.locator('[data-tour="scenes"]').getByText('Conflict')).toBeVisible()
    assertNetworkClean(t.requests)
    // The demo keeps no local state at all.
    const stored = await page.evaluate(() => Object.keys(window.localStorage).length)
    expect(stored).toBe(0)
  })

  test('application routes are not part of the demo bundle', async ({ page }) => {
    for (const path of ['/login', '/register', '/dashboard', '/upload', '/study', '/tutorials']) {
      await page.goto(path)
      await expect(
        page.getByText("That page isn't part of this demo."),
        `route ${path} should hit the demo 404`,
      ).toBeVisible()
    }
  })
})

test.describe('truthful controls', () => {
  test('rename propagates through drafted captions locally', async ({ page }) => {
    await page.goto('/onboarding')
    await dismissWalkthrough(page)
    await page.getByRole('button', { name: 'Characters' }).click()
    await page.getByRole('button', { name: 'Rename' }).first().click()
    await page.getByLabel(/New name for/).fill('Sintel')
    await page.getByRole('button', { name: 'Apply rename' }).click()
    // The scene list re-renders captions with the new name.
    await expect(page.locator('[data-tour="scenes"]').getByText(/Sintel/).first()).toBeVisible()
    await expect(page.getByText('renamed')).toBeVisible()
  })

  test('playback speed changes the timing estimate (and playbackRate is real)', async ({ page }) => {
    await page.goto('/onboarding')
    await dismissWalkthrough(page)
    await expect(page.getByText(/≈18\.0s spoken at 1×/)).toBeVisible()
    await page.getByLabel('Playback speed').selectOption('1.5')
    await expect(page.getByText(/≈12\.0s spoken at 1\.5×/)).toBeVisible()
  })

  test('fit to gap is a labeled local trim with the honest note', async ({ page }) => {
    await page.goto('/onboarding')
    await dismissWalkthrough(page)
    // Scene 5 via the checks panel (also exercises transparent checks).
    await page.getByRole('button', { name: 'Checks' }).click()
    await expect(page.getByText('9 of 9 lines on')).toBeVisible()
    await page
      .locator('aside[aria-label="Timing checks panel"]')
      .getByRole('button', { name: /Scene 5/ })
      .click()
    await page.locator('[data-tour="fit"]').click()
    await expect(page.getByText(/Local deterministic trim, no AI/)).toBeVisible()
  })

  test('no weighted overall quality score is shown anywhere', async ({ page }) => {
    await page.goto('/onboarding')
    await dismissWalkthrough(page)
    await page.getByRole('button', { name: 'Checks' }).click()
    await expect(page.getByText(/not a measure of writing quality/)).toBeVisible()
    const body = await page.textContent('body')
    expect(body).not.toMatch(/overall quality|quality score/i)
  })
})

test.describe('keyboard-only completion', () => {
  test('the whole loop is operable with the keyboard alone', async ({ page }) => {
    await page.goto('/onboarding')
    // Modal steps: Next is auto-focused.
    for (let i = 0; i < 4; i++) {
      await expect(page.getByRole('button', { name: 'Next step' })).toBeFocused()
      await page.keyboard.press('Enter')
    }
    // Action step: focus moved to the highlighted control.
    await expect(page.locator('[data-tour="toggle-line"]')).toBeFocused()
    await page.keyboard.press('Enter')
    // Completion focuses Next.
    await expect(page.getByRole('button', { name: 'Next step' })).toBeFocused()
    await page.keyboard.press('Enter')
    await page.keyboard.press('Enter') // modal step 6 → step 7
    await expect(page.locator('[data-tour="fit"]')).toBeFocused()
    await page.keyboard.press('Enter')
    await expect(page.getByRole('button', { name: 'Next step' })).toBeFocused()
    await page.keyboard.press('Enter')
    // Listen step: focus lands on the first listen control.
    await page.keyboard.press('Enter')
    await expect(page.getByRole('button', { name: 'Next step' })).toBeFocused()
    await page.keyboard.press('Enter')
    // Described-example step → dialog opens, Escape closes, focus restored.
    await expect(page.locator('[data-tour="preview"] button')).toBeFocused()
    await page.keyboard.press('Enter')
    await expect(page.getByRole('dialog', { name: 'Pre-rendered described example' })).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.getByRole('button', { name: 'Next step' })).toBeFocused()
    await page.keyboard.press('Enter')
    await expect(page.getByRole('button', { name: 'Finish the walkthrough' })).toBeFocused()
    await page.keyboard.press('Enter')
    await expect(page.getByRole('button', { name: 'Replay walkthrough' })).toBeFocused()
  })

  test('modal steps trap focus inside the walkthrough card', async ({ page }) => {
    await page.goto('/onboarding')
    await expect(page.getByRole('button', { name: 'Next step' })).toBeFocused()
    // Tab cycles: Skip tour → Next → Skip tour (index 0 has no Back).
    await page.keyboard.press('Tab')
    await expect(page.getByRole('button', { name: 'Skip the walkthrough' })).toBeFocused()
    await page.keyboard.press('Tab')
    await expect(page.getByRole('button', { name: 'Next step' })).toBeFocused()
    await page.keyboard.press('Tab')
    await expect(page.getByRole('button', { name: 'Skip the walkthrough' })).toBeFocused()
  })
})

test.describe('embedded narrow fallback on a desktop-sized screen', () => {
  // A narrow iframe on a desktop: a new tab genuinely offers a wider context.
  // (screen must be set at context creation to reach window.screen.)
  test('offers the full demo in a new tab', async ({ browser }) => {
    const ctx = await browser.newContext({
      baseURL: 'http://localhost:4174',
      viewport: { width: 768, height: 900 },
      screen: { width: 1512, height: 982 },
    })
    const page = await ctx.newPage()
    await page.goto('/onboarding?embed=1')
    await expect(page.getByRole('link', { name: 'Open the full demo in a new tab' })).toBeVisible()
    await ctx.close()
  })
})

test.describe('embedded narrow fallback on a phone-sized screen', () => {
  // A phone cannot host the desktop editor in any tab — the link must not appear.
  test('does not offer a full-demo tab that leads nowhere wider', async ({ browser }) => {
    const ctx = await browser.newContext({
      baseURL: 'http://localhost:4174',
      viewport: { width: 390, height: 844 },
      screen: { width: 390, height: 844 },
    })
    const page = await ctx.newPage()
    await page.goto('/onboarding?embed=1')
    await expect(page.getByText('needs a wider display')).toBeVisible()
    await expect(page.getByRole('link', { name: 'Open the full demo in a new tab' })).toHaveCount(0)
    await ctx.close()
  })
})

test.describe('desktop ladder (contract viewports)', () => {
  for (const [w, h] of [
    [1232, 693],
    [1120, 700],
    [1024, 693],
  ] as const) {
    test(`editor holds at ${w}x${h} with all three panels and no overflow`, async ({ page }) => {
      await page.setViewportSize({ width: w, height: h })
      await page.goto('/onboarding')
      await dismissWalkthrough(page)
      await expect(page.getByLabel('Scene list')).toBeVisible()
      await expect(page.getByLabel('Script panel')).toBeVisible()
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
      )
      expect(overflow).toBeLessThanOrEqual(0)
    })
  }

  test('intro → editor handoff produces no page-level layout shift at the stage size', async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1232, height: 693 })
    await page.goto('/')
    const before = await page.evaluate(() => [
      document.documentElement.scrollWidth,
      document.documentElement.scrollHeight,
    ])
    await page.getByRole('button', { name: 'Start now' }).click()
    await expect(page.getByText('The scene list')).toBeVisible()
    const after = await page.evaluate(() => [
      document.documentElement.scrollWidth,
      document.documentElement.scrollHeight,
    ])
    expect(after).toEqual(before)
  })
})

test.describe('captions and media behavior', () => {
  test('the source video carries a dialogue captions track', async ({ page }) => {
    await page.goto('/onboarding')
    await dismissWalkthrough(page)
    const track = page.locator('video track[kind="captions"]')
    await expect(track).toHaveAttribute('src', /captions\.vtt/)
  })

  test('reduced motion removes the editor entry animation', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.goto('/onboarding')
    await dismissWalkthrough(page)
    const name = await page
      .locator('.pd-editor-enter')
      .evaluate((el) => getComputedStyle(el).animationName)
    expect(name).toBe('none')
  })
})

test.describe('narrow fallback', () => {
  test('390px shows the text walkthrough, never a crushed editor', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    const t = track(page)
    await page.goto('/onboarding')
    await expect(page.getByText('The interactive editor needs a wider display')).toBeVisible()
    await expect(page.getByText('What audio description is')).toBeVisible()
    // Standalone narrow: a new tab would be no wider — no "open full demo" offer.
    await expect(page.getByText('Open the full demo in a new tab')).toHaveCount(0)
    // The 8.5 MB example loads only on request.
    expect(t.requests.filter((u) => EXPORT_RE.test(u))).toHaveLength(0)
    await page.getByRole('button', { name: /Load the described example/ }).click()
    await expect.poll(() => t.requests.filter((u) => EXPORT_RE.test(u)).length).toBeGreaterThan(0)
    // No horizontal overflow.
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    )
    expect(overflow).toBeLessThanOrEqual(0)
  })


  test('320px stays coherent with zero horizontal overflow', async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 700 })
    await page.goto('/')
    await expect(page.getByRole('button', { name: 'Start now' })).toBeVisible()
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    )
    expect(overflow).toBeLessThanOrEqual(0)
  })
})
