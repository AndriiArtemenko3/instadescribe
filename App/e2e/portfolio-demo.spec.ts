import { test, expect, observePage, assertObservationClean } from './fixtures'
import type { Page, Request } from '@playwright/test'

// Browser verification of the dedicated portfolio-demo build, served by the
// host-faithful server (scripts/serve-host.mjs) — the same statuses/headers
// semantics the target static host applies. Global network/console/pageerror
// observation comes from ./fixtures on every test.

const MEDIA_RE = /\.(mp4|mp3)(\?|$)/
const EXPORT_RE = /export\.mp4/

function trackRequests(page: Page) {
  const requests: string[] = []
  page.on('request', (r: Request) => requests.push(r.url()))
  return requests
}

async function dismissWalkthrough(page: Page) {
  await expect(page.getByText('The scene list')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByText('The scene list')).toHaveCount(0)
}

/** Steps 1–8: orientation, retire scene 2, trim scene 5, hear a line. */
async function runWalkthroughToListen(page: Page) {
  for (let i = 0; i < 4; i++) {
    await page.getByRole('button', { name: 'Next step' }).click()
  }
  await expect(page.getByRole('heading', { name: 'Let the dialogue breathe' })).toBeVisible()
  await page.locator('[data-tour="toggle-line"]').click()
  await expect(page.getByText('Scene 2’s conflict is cleared').first()).toBeVisible()
  await page.getByRole('button', { name: 'Next step' }).click()
  await page.getByRole('button', { name: 'Next step' }).click()
  await page.locator('[data-tour="fit"]').click()
  await expect(page.getByText('The line fits its silence').first()).toBeVisible()
  await page.getByRole('button', { name: 'Next step' }).click()
  // LISTEN: completion requires ACTUAL playback ('playing'), not a click.
  await page.getByRole('button', { name: /Original line · Onyx/ }).click()
  await expect(page.getByText('baked audio and your live text').first()).toBeVisible()
  await page.getByRole('button', { name: 'Next step' }).click()
}

test.describe('intro', () => {
  test('loads without media, backend, or external requests', async ({ page }) => {
    const requests = trackRequests(page)
    await page.goto('/')
    await expect(page.getByRole('heading', { name: 'Try InstaScribe Live Onboarding' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Start now' })).toBeVisible()
    await expect(page.getByText('Blender Foundation')).toBeVisible()
    await expect(page.getByRole('link', { name: 'CC BY 3.0' })).toBeVisible()
    await expect(page.getByText('no live model or API calls')).toBeVisible()
    expect(requests.filter((u) => MEDIA_RE.test(u))).toHaveLength(0)
    expect(requests.filter((u) => u.endsWith('.json'))).toHaveLength(0)
  })

  test('intro payload stays lightweight (< 500 KB transferred)', async ({ page }) => {
    let bytes = 0
    page.on('response', async (r) => {
      try {
        bytes += (await r.body()).length
      } catch {
        /* opaque bodies don't count */
      }
    })
    await page.goto('/', { waitUntil: 'networkidle' })
    expect(bytes).toBeGreaterThan(0)
    expect(bytes).toBeLessThan(500 * 1024)
  })
})

test.describe('start and core loop', () => {
  test('Start now loads the editor in place and the guided loop completes', async ({ page }) => {
    const requests = trackRequests(page)
    await page.goto('/')
    await page.getByRole('button', { name: 'Start now' }).click()
    await expect(page).toHaveURL(/\/onboarding$/)
    await expect(page.getByText('The scene list')).toBeVisible()

    // Under the transcript authority, seven drafts genuinely collide at 1×.
    await expect(
      page.locator('[data-tour="scenes"]').getByText('Conflict', { exact: true }),
    ).toHaveCount(7)
    expect(requests.filter((u) => EXPORT_RE.test(u))).toHaveLength(0)

    await runWalkthroughToListen(page)

    // Described film: opening the dialog is NOT completion…
    await page.locator('[data-tour="preview"] button').click()
    const dialog = page.getByRole('dialog', { name: 'Pre-rendered described example' })
    await expect(dialog).toBeVisible()
    await expect(page.getByText('Your edits are not in this file')).toBeVisible()
    await expect
      .poll(() => requests.filter((u) => EXPORT_RE.test(u)).length, { timeout: 5000 })
      .toBeGreaterThan(0)
    // …actual playback is. Start it, then close.
    const video = dialog.locator('video')
    await video.click()
    await expect.poll(() => video.evaluate((v: HTMLVideoElement) => !v.paused)).toBe(true)
    await page.getByRole('button', { name: 'Done listening' }).click()

    await expect(page.getByText('Take a moment with it').first()).toBeVisible()
    await page.getByRole('button', { name: 'Next step' }).click()

    // Completion: truthful summary + evidence-based hearing claim + honest
    // acknowledgment of the remaining imperfect drafts.
    await expect(page.getByText(/you switched 1 line off, trimmed 1 to fit/)).toBeVisible()
    await expect(page.getByText('You heard a narration line and the described film')).toBeVisible()
    await expect(page.getByText(/other drafts that run long or brush later dialogue/)).toBeVisible()
    // Terminal card: primary Explore + Restart + the contracted exit; no Skip/Back.
    await expect(page.getByRole('button', { name: 'Skip the walkthrough' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Previous step' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Restart demo' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Back to the intro' })).toBeVisible()
    await page.getByRole('button', { name: 'Finish the walkthrough and explore the editor' }).click()
    await expect(page.getByRole('button', { name: 'Replay walkthrough' })).toBeFocused()

    // Scene 2 retired, scene 5 genuinely clean — verified in the Checks tab.
    await page.getByRole('button', { name: 'Checks' }).click()
    const checks = page.locator('aside[aria-label="Timing checks panel"]')
    await expect(checks.getByText('Switched off — not in the mix')).toBeVisible()
    const scene5 = checks.getByRole('button', { name: /Scene 5/ })
    await expect(scene5.getByText(/Fits its moment/)).toBeVisible()
    await expect(scene5.getByText('Clear of dialogue')).toBeVisible()
  })

  test('Back and Skip behave; Escape exits the walkthrough', async ({ page }) => {
    await page.goto('/onboarding')
    await expect(page.getByText('The scene list')).toBeVisible()
    await page.getByRole('button', { name: 'Next step' }).click()
    await expect(page.getByRole('heading', { name: 'The film and its timeline' })).toBeVisible()
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
    await expect(page.locator('main[inert], div[inert]')).toHaveCount(1)
    for (let i = 0; i < 4; i++) await page.getByRole('button', { name: 'Next step' }).click()
    await expect(page.getByRole('heading', { name: 'Let the dialogue breathe' })).toBeVisible()
    await expect(page.locator('main[inert], div[inert]')).toHaveCount(0)
  })
})

test.describe('single audio owner', () => {
  test('opening a modal stops baked playback (no inaccessible Stop state)', async ({ page }) => {
    await page.goto('/onboarding')
    await dismissWalkthrough(page)
    await page.getByRole('button', { name: /Original line · Onyx/ }).click()
    await expect(page.getByRole('button', { name: 'Stop' })).toBeVisible()
    await page.getByRole('button', { name: 'About & licensing' }).click()
    await expect(page.getByRole('dialog', { name: 'About this demo' })).toBeVisible()
    await page.keyboard.press('Escape')
    // The background control must not remain in a Stop state.
    await expect(page.getByRole('button', { name: 'Stop' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: /Original line · Onyx/ })).toBeVisible()
  })

  test('starting the source video stops the baked line (never two sources)', async ({ page }) => {
    await page.goto('/onboarding')
    await dismissWalkthrough(page)
    await page.getByRole('button', { name: /Original line · Onyx/ }).click()
    await expect(page.getByRole('button', { name: 'Stop' })).toBeVisible()
    const source = page.locator('[data-tour="video-player"] video')
    await source.evaluate((v: HTMLVideoElement) => v.play())
    await expect(page.getByRole('button', { name: 'Stop' })).toHaveCount(0)
    await expect.poll(() => source.evaluate((v: HTMLVideoElement) => !v.paused)).toBe(true)
  })

  test('opening the described example stops the source video; closing stops the example', async ({
    page,
  }) => {
    await page.goto('/onboarding')
    await dismissWalkthrough(page)
    const source = page.locator('[data-tour="video-player"] video')
    await source.evaluate((v: HTMLVideoElement) => v.play())
    await expect.poll(() => source.evaluate((v: HTMLVideoElement) => !v.paused)).toBe(true)
    await page.getByRole('button', { name: 'Play described example' }).click()
    // Opening the modal silences everything else.
    await expect.poll(() => source.evaluate((v: HTMLVideoElement) => v.paused)).toBe(true)
    const dialog = page.getByRole('dialog', { name: 'Pre-rendered described example' })
    const described = dialog.locator('video')
    await described.click()
    await expect.poll(() => described.evaluate((v: HTMLVideoElement) => !v.paused)).toBe(true)
    // Retain the exact element across close (blocker 5): it must be paused
    // and its currentTime must not advance after the dialog is dismissed.
    const handle = await described.elementHandle()
    await page.getByRole('button', { name: 'Done listening' }).click()
    await expect(dialog).toHaveCount(0)
    expect(await handle!.evaluate((v) => (v as HTMLVideoElement).paused)).toBe(true)
    const t1 = await handle!.evaluate((v) => (v as HTMLVideoElement).currentTime)
    await page.waitForTimeout(500)
    const t2 = await handle!.evaluate((v) => (v as HTMLVideoElement).currentTime)
    expect(t2).toBe(t1)
    await expect.poll(() => source.evaluate((v: HTMLVideoElement) => v.paused)).toBe(true)
  })

  test('changing scene stops the playing line', async ({ page }) => {
    await page.goto('/onboarding')
    await dismissWalkthrough(page)
    await page.getByRole('button', { name: /Original line · Onyx/ }).click()
    await expect(page.getByRole('button', { name: 'Stop' })).toBeVisible()
    await page.locator('[data-tour="scenes"]').getByRole('button', { name: /^Scene 1/ }).click()
    await expect(page.getByRole('button', { name: 'Stop' })).toHaveCount(0)
  })
})

test.describe('embed and routing', () => {
  test('direct embedded entry seeds deterministically without the intro', async ({ page }) => {
    await page.goto('/onboarding?embed=1')
    await expect(page.getByText('The scene list')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Start now' })).toHaveCount(0)
    await expect(page.getByText('Exit to intro')).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Close demo' })).toBeVisible()
    await page.keyboard.press('Escape')
    await page.getByRole('button', { name: 'Restart', exact: false }).click()
    await expect(page).toHaveURL(/embed=1/)
    await expect(page.getByText('The scene list')).toBeVisible()
  })

  // NOTE on frame-ancestors: the header's exact value on every route is
  // asserted in host.spec.ts, and the exit postMessage's origin discipline is
  // unit-tested in lib/embed.test.ts. In-browser blocked-frame observation was
  // attempted three ways (console refusal message, frame content, response
  // events from a foreign data: parent); Chromium does not surface blocked
  // subframe telemetry to Playwright deterministically, so enforcement of the
  // delivered header is left to the browser — standard behavior — and is
  // recorded as a limitation in the work order.

  test('completion card Close demo (embed) returns the frame to the intro', async ({ page }) => {
    await page.goto('/onboarding?embed=1')
    await expect(page.getByText('The scene list')).toBeVisible()
    await runWalkthroughToListen(page)
    await page.locator('[data-tour="preview"] button').click()
    const dialog = page.getByRole('dialog', { name: 'Pre-rendered described example' })
    await page.getByRole('button', { name: 'Play the example' }).click()
    await expect
      .poll(() => dialog.locator('video').evaluate((v: HTMLVideoElement) => !v.paused))
      .toBe(true)
    await page.getByRole('button', { name: 'Done listening' }).click()
    await page.getByRole('button', { name: 'Next step' }).click()
    await page.getByRole('button', { name: 'Close demo' }).last().click()
    await expect(page.getByRole('button', { name: 'Start now' })).toBeVisible()
  })

  test('Close demo in embed mode returns to the intro state', async ({ page }) => {
    await page.goto('/onboarding?embed=1')
    await dismissWalkthrough(page)
    await page.getByRole('button', { name: 'Close demo' }).click()
    await expect(page.getByRole('button', { name: 'Start now' })).toBeVisible()
    await expect(page).toHaveURL(/\/$/)
  })

  test('a direct route reload survives the host rewrite', async ({ page }) => {
    await page.goto('/onboarding')
    await expect(page.getByText('The scene list')).toBeVisible()
    await page.reload()
    await expect(page.getByText('The scene list')).toBeVisible()
  })

  test('restart returns to a genuinely clean intro without backend calls', async ({ page }) => {
    await page.goto('/onboarding')
    await dismissWalkthrough(page)
    await page.locator('[data-tour="toggle-line"]').click()
    await page.getByRole('button', { name: 'Restart' }).click()
    await expect(page).toHaveURL(/\/$/)
    await expect(page.getByRole('heading', { name: 'Try InstaScribe Live Onboarding' })).toBeVisible()
    await page.getByRole('button', { name: 'Start now' }).click()
    await expect(
      page.locator('[data-tour="scenes"]').getByText('Conflict', { exact: true }),
    ).toHaveCount(7)
    const stored = await page.evaluate(() => Object.keys(window.localStorage).length)
    expect(stored).toBe(0)
  })

  test('application routes hit the genuine 404 boundary in the browser too', async ({
    page,
    observation,
  }) => {
    observation.allow(/404/) // this test deliberately requests a 404 boundary
    const response = await page.goto('/dashboard')
    expect(response?.status()).toBe(404)
    await expect(page.getByText("That page isn't part of this demo.")).toBeVisible()
    await expect(page.getByRole('link', { name: 'Back to the start' })).toBeVisible()
  })

  test('fixture failure shows an honest error and Try again genuinely retries', async ({
    page,
    observation,
  }) => {
    observation.allow(/ERR_FAILED|Failed to load resource/) // deliberate abort below
    await page.route('**/data/sintel-blender-cc/scenes.json', (route) => route.abort())
    await page.goto('/onboarding')
    await expect(page.getByText("The demo's local files didn't load.")).toBeVisible()
    await page.unroute('**/data/sintel-blender-cc/scenes.json')
    await page.getByRole('button', { name: 'Try again' }).click()
    await expect(page.getByText('The scene list')).toBeVisible()
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
    await expect(page.locator('[data-tour="scenes"]').getByText(/Sintel/).first()).toBeVisible()
    await expect(page.getByText('renamed')).toBeVisible()
  })

  test('drafts display with clean sentence-initial casing (display layer)', async ({ page }) => {
    await page.goto('/onboarding')
    await dismissWalkthrough(page)
    // The committed fixture for scene 3 starts lowercase ("a young woman…").
    await expect(
      page.locator('[data-tour="scenes"]').getByText(/^A young woman sets down a stone bowl/),
    ).toBeVisible()
  })

  test('playback speed changes the timing estimate', async ({ page }) => {
    await page.goto('/onboarding')
    await dismissWalkthrough(page)
    await expect(page.getByText(/≈18\.0s spoken at 1×/)).toBeVisible()
    await page.getByLabel('Playback speed').selectOption('1.5')
    await expect(page.getByText(/≈12\.0s spoken at 1\.5×/)).toBeVisible()
  })

  test.describe('fit to gap is speed-consistent and raw-clear (scene 5)', () => {
    for (const [speed, kept, est] of [
      ['0.75', 14, '7.5'],
      ['1', 17, '6.8'],
      ['1.25', 24, '7.7'],
      ['1.5', 28, '7.5'],
    ] as const) {
      test(`at ${speed}× the trim fits scene 5 and completes`, async ({ page }) => {
        await page.goto('/onboarding')
        await dismissWalkthrough(page)
        await page.getByRole('button', { name: 'Checks' }).click()
        await page
          .locator('aside[aria-label="Timing checks panel"]')
          .getByRole('button', { name: /Scene 5/ })
          .click()
        await page.getByLabel('Playback speed').selectOption(speed)
        await page.locator('[data-tour="fit"]').click()
        await expect(
          page.getByText(
            new RegExp(
              `Kept the first ${kept} of 44 words — ≈${est}s at ${speed}× for the 7.8s of usable silence`,
            ),
          ),
        ).toBeVisible()
        // The result genuinely fits at this speed: no overrun, no collision.
        await expect(
          page.locator('aside[aria-label="Script panel"]').getByText(/Talks over dialogue/),
        ).toHaveCount(0)
        await expect(
          page.locator('aside[aria-label="Script panel"]').getByText('Placed', { exact: true }),
        ).toBeVisible()
        // And the trim control is no longer offered (nothing left to fix).
        await expect(page.locator('[data-tour="fit"]')).toBeDisabled()
      })
    }
  })

  test('scene 9 at 1.5× — the review regression — trims raw-clear of the 108.02s dialogue', async ({
    page,
  }) => {
    await page.goto('/onboarding')
    await dismissWalkthrough(page)
    await page.getByRole('button', { name: 'Checks' }).click()
    const checks = page.locator('aside[aria-label="Timing checks panel"]')
    await checks.getByRole('button', { name: /Scene 9/ }).click()
    await page.getByLabel('Playback speed').selectOption('1.5')
    await page.locator('[data-tour="fit"]').click()
    // Old model: 11 words ≈2.9s "clear" with 0.163s real overlap. New model:
    // 8 words ≈2.1s inside the 2.8s usable window, zero raw overlap.
    await expect(
      page.getByText(/Kept the first 8 of 55 words — ≈2\.1s at 1\.5× for the 2\.8s of usable silence/),
    ).toBeVisible()
    const scriptPanel = page.locator('aside[aria-label="Script panel"]')
    await expect(scriptPanel.getByText(/Talks over dialogue/)).toHaveCount(0)
    await expect(scriptPanel.getByText('Placed', { exact: true })).toBeVisible()
    await expect(scriptPanel.locator('textarea')).toHaveValue(
      'A young woman races up steep, sunlit rooftops.',
    )
    await page.getByRole('button', { name: 'Checks' }).click()
    const row9 = checks.getByRole('button', { name: /Scene 9/ })
    await expect(row9.getByText('Clear of dialogue')).toBeVisible()
  })

  test('unfixable collisions get reason-aware copy (scene 2 head-on vs scene 6 generic)', async ({
    page,
  }) => {
    await page.goto('/onboarding')
    await dismissWalkthrough(page)
    const scriptPanel = page.locator('aside[aria-label="Script panel"]')
    // Scene 2 (selected by default): head-on wording, 0 usable silence.
    await expect(scriptPanel.getByText(/film is speaking\s+from the line's first beat/)).toBeVisible()
    await expect(scriptPanel.getByText(/0\.0s usable silence/)).toBeVisible()
    // Scene 6: unfixable but NOT head-on — different, provable wording.
    await page.locator('[data-tour="scenes"]').getByRole('button', { name: /^Scene 6/ }).click()
    await expect(
      scriptPanel.getByText(/this deterministic\s+trim cannot fully clear the conflict/),
    ).toBeVisible()
    await expect(scriptPanel.getByText(/first beat/)).toHaveCount(0)
    await expect(page.locator('[data-tour="fit"]')).toBeDisabled()
  })

  test('scene 2 offers no trim — the overlap is untrimmable and says so', async ({ page }) => {
    await page.goto('/onboarding')
    await dismissWalkthrough(page)
    await expect(page.getByText(/Talks over dialogue for ≈4\.7s/)).toBeVisible()
    await expect(page.getByText(/the film is speaking\s+from the line's first beat/)).toBeVisible()
    await expect(page.locator('[data-tour="fit"]')).toBeDisabled()
  })

  test('checks panel reports the transcript-authority truth', async ({ page }) => {
    await page.goto('/onboarding')
    await dismissWalkthrough(page)
    await page.getByRole('button', { name: 'Checks' }).click()
    await expect(page.getByText('9 of 9 lines on')).toBeVisible()
    await expect(page.getByText('7 talk over dialogue')).toBeVisible()
    await expect(page.getByText('8 run past their scene')).toBeVisible()
    const body = await page.textContent('body')
    expect(body).not.toMatch(/overall quality|quality score/i)
  })

  test('browser speech is local-only: without an on-device voice the control is withheld', async ({
    page,
  }) => {
    // Headless Chromium exposes no speechSynthesis voices, which IS the
    // no-local-voice case: the demo must show the honest note instead of
    // falling back to an unspecified (possibly remote) default voice.
    await page.goto('/onboarding')
    await dismissWalkthrough(page)
    const hasVoices = await page.evaluate(
      () => 'speechSynthesis' in window && window.speechSynthesis.getVoices().length > 0,
    )
    if (!hasVoices) {
      await expect(page.getByText(/no on-device voice/)).toBeVisible({ timeout: 5000 })
      await expect(page.getByText(/never sends text to a speech service/)).toBeVisible()
      await expect(page.getByRole('button', { name: /Read my text/ })).toHaveCount(0)
    } else {
      await expect(page.getByRole('button', { name: /Read my text · on-device voice/ })).toBeVisible()
    }
  })
})

test.describe('keyboard-only completion', () => {
  test('the whole loop is operable with the keyboard alone', async ({ page }) => {
    await page.goto('/onboarding')
    for (let i = 0; i < 4; i++) {
      await expect(page.getByRole('button', { name: 'Next step' })).toBeFocused()
      await page.keyboard.press('Enter')
    }
    // Action steps announce first, then move focus to the target.
    await expect(page.locator('[data-tour="toggle-line"]')).toBeFocused()
    await page.keyboard.press('Enter')
    await expect(page.getByRole('button', { name: 'Next step' })).toBeFocused()
    await page.keyboard.press('Enter')
    await page.keyboard.press('Enter')
    await expect(page.locator('[data-tour="fit"]')).toBeFocused()
    await page.keyboard.press('Enter')
    await expect(page.getByRole('button', { name: 'Next step' })).toBeFocused()
    await page.keyboard.press('Enter')
    await expect(page.getByRole('button', { name: /Original line · Onyx/ })).toBeFocused()
    await page.keyboard.press('Enter') // play the baked line from its focused button
    await expect(page.getByRole('button', { name: 'Next step' })).toBeFocused()
    await page.keyboard.press('Enter')
    await expect(page.locator('[data-tour="preview"] button')).toBeFocused()
    await page.keyboard.press('Enter')
    const dialog = page.getByRole('dialog', { name: 'Pre-rendered described example' })
    await expect(dialog).toBeVisible()
    // The dialog's explicit Play control is its initial focus target.
    await expect(page.getByRole('button', { name: 'Play the example' })).toBeFocused()
    await page.keyboard.press('Enter')
    await expect
      .poll(() => dialog.locator('video').evaluate((v: HTMLVideoElement) => !v.paused))
      .toBe(true)
    await page.keyboard.press('Escape')
    await expect(page.getByRole('button', { name: 'Next step' })).toBeFocused()
    // Blocker 4 regression: the overlay's delayed target focus (450ms) must
    // NOT steal focus back from Next once the action is complete.
    await page.waitForTimeout(700)
    await expect(page.getByRole('button', { name: 'Next step' })).toBeFocused()
    await page.keyboard.press('Enter')
    await expect(
      page.getByRole('button', { name: 'Finish the walkthrough and explore the editor' }),
    ).toBeFocused()
    // Blocker 3: the contracted exit is keyboard-operable from the card.
    await page.keyboard.press('Shift+Tab')
    await expect(page.getByRole('button', { name: 'Back to the intro' })).toBeFocused()
    await page.keyboard.press('Enter')
    await expect(page.getByRole('button', { name: 'Start now' })).toBeVisible()
    await expect(page).toHaveURL(/\/$/)
  })

  test('modal steps trap focus inside the walkthrough card', async ({ page }) => {
    await page.goto('/onboarding')
    await expect(page.getByRole('button', { name: 'Next step' })).toBeFocused()
    await page.keyboard.press('Tab')
    await expect(page.getByRole('button', { name: 'Skip the walkthrough' })).toBeFocused()
    await page.keyboard.press('Tab')
    await expect(page.getByRole('button', { name: 'Next step' })).toBeFocused()
    await page.keyboard.press('Tab')
    await expect(page.getByRole('button', { name: 'Skip the walkthrough' })).toBeFocused()
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

test.describe('embedded narrow fallback on a desktop-sized screen', () => {
  test('offers the full demo in a new tab', async ({ browser }) => {
    const ctx = await browser.newContext({
      baseURL: 'http://localhost:4174',
      viewport: { width: 768, height: 900 },
      screen: { width: 1512, height: 982 },
    })
    const page = await ctx.newPage()
    const obs = observePage(page)
    await page.goto('/onboarding?embed=1')
    await expect(page.getByRole('link', { name: 'Open the full demo in a new tab' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Close demo' })).toBeVisible()
    assertObservationClean(obs)
    await ctx.close()
  })
})

test.describe('embedded narrow fallback on a phone-sized screen', () => {
  test('does not offer a full-demo tab that leads nowhere wider', async ({ browser }) => {
    const ctx = await browser.newContext({
      baseURL: 'http://localhost:4174',
      viewport: { width: 390, height: 844 },
      screen: { width: 390, height: 844 },
    })
    const page = await ctx.newPage()
    const obs = observePage(page)
    await page.goto('/onboarding?embed=1')
    await expect(page.getByText('needs a wider display')).toBeVisible()
    await expect(page.getByRole('link', { name: 'Open the full demo in a new tab' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Close demo' })).toBeVisible()
    assertObservationClean(obs)
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

test.describe('narrow fallback', () => {
  test('390px shows the text walkthrough with an explicit exit, never a crushed editor', async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    const requests = trackRequests(page)
    await page.goto('/onboarding')
    await expect(page.getByText('The interactive editor needs a wider display')).toBeVisible()
    await expect(page.getByText('What audio description is')).toBeVisible()
    await expect(page.getByRole('link', { name: 'Back to the intro' })).toBeVisible()
    await expect(page.getByText('Open the full demo in a new tab')).toHaveCount(0)
    expect(requests.filter((u) => EXPORT_RE.test(u))).toHaveLength(0)
    await page.getByRole('button', { name: /Load the described example/ }).click()
    await expect.poll(() => requests.filter((u) => EXPORT_RE.test(u)).length).toBeGreaterThan(0)
    // Explicit narrow-video control + cleanup on exit (blocker 5).
    await page.getByRole('button', { name: 'Play the example' }).click()
    const video = page.locator('video')
    await expect.poll(() => video.evaluate((v: HTMLVideoElement) => !v.paused)).toBe(true)
    const handle = await video.elementHandle()
    await page.getByRole('link', { name: 'Back to the intro' }).click()
    await expect(page.getByRole('button', { name: 'Start now' })).toBeVisible()
    expect(await handle!.evaluate((v) => (v as HTMLVideoElement).paused)).toBe(true)
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
