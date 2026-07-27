#!/usr/bin/env node
// Captures the contract's required review states from the BUILT portfolio-demo
// (vite preview on :4174) plus the dev-only Figure 00 harness (dev server on
// :5175 when available). Screenshots land in the untracked review directory.
import { chromium } from '@playwright/test'
import { mkdirSync } from 'node:fs'

const OUT =
  process.argv[2] ??
  '../.claude/visual-reviews/instascribe-live-onboarding-v1'
mkdirSync(OUT, { recursive: true })

const BASE = 'http://localhost:4174'
const shot = (page, name) => page.screenshot({ path: `${OUT}/${name}.png` })

const browser = await chromium.launch()
const ctx = await browser.newContext({ deviceScaleFactor: 2 })
const page = await ctx.newPage()

async function at(w, h, fn) {
  await page.setViewportSize({ width: w, height: h })
  await fn()
}

const dismiss = async () => {
  await page.getByText('The scene list').waitFor()
  await page.keyboard.press('Escape')
}

// ── Intro states ─────────────────────────────────────────────────────────────
await at(1440, 900, async () => {
  await page.goto(`${BASE}/`)
  await page.getByRole('button', { name: 'Start now' }).waitFor()
  await shot(page, 'intro-1440x900')
  await page.keyboard.press('Tab') // focus Start now → visible focus state
  await shot(page, 'intro-1440x900-focus')
})
await at(1232, 693, async () => {
  await page.goto(`${BASE}/`)
  await page.getByRole('button', { name: 'Start now' }).waitFor()
  await shot(page, 'intro-1232x693-stage')
})
await at(768, 900, async () => {
  await page.goto(`${BASE}/`)
  await page.getByRole('button', { name: 'Start now' }).waitFor()
  await shot(page, 'intro-768')
})
await at(390, 844, async () => {
  await page.goto(`${BASE}/`)
  await page.getByRole('button', { name: 'Start now' }).waitFor()
  await shot(page, 'intro-390x844')
})
await at(320, 700, async () => {
  await page.goto(`${BASE}/`)
  await page.getByRole('button', { name: 'Start now' }).waitFor()
  await shot(page, 'intro-320')
})

// ── Editor states at the exact stage and eligibility ladder ─────────────────
await at(1232, 693, async () => {
  await page.goto(`${BASE}/onboarding`)
  await page.getByText('The scene list').waitFor()
  await shot(page, 'editor-1232x693-step1-orient')
  // Walk to the IDENTIFY step (4 clicks) and the REFINE action step.
  for (let i = 0; i < 3; i++) await page.getByRole('button', { name: 'Next step' }).click()
  await page.getByRole('heading', { name: 'One line fights the dialogue' }).waitFor()
  await shot(page, 'editor-1232x693-step4-identify')
  await page.getByRole('button', { name: 'Next step' }).click()
  await page.getByRole('heading', { name: 'Let the dialogue breathe' }).waitFor()
  await shot(page, 'editor-1232x693-step5-action')
  await page.locator('[data-tour="toggle-line"]').click()
  await page.waitForTimeout(250)
  await shot(page, 'editor-1232x693-step5-done')
  // Listen dialog.
  await page.keyboard.press('Escape')
  await page.locator('[data-tour="preview"] button').click()
  await page.getByRole('dialog', { name: 'Pre-rendered described example' }).waitFor()
  await shot(page, 'editor-1232x693-listen-dialog')
  await page.getByRole('button', { name: 'Done listening' }).click()
  // Checks + characters panels.
  await page.getByRole('button', { name: 'Checks' }).click()
  await page.waitForTimeout(150)
  await shot(page, 'editor-1232x693-checks')
  await page.getByRole('button', { name: 'Characters' }).click()
  await page.waitForTimeout(150)
  await shot(page, 'editor-1232x693-characters')
})

await at(1440, 900, async () => {
  await page.goto(`${BASE}/onboarding`)
  await dismiss()
  await shot(page, 'editor-1440x900-explore')
})
await at(1120, 700, async () => {
  await page.goto(`${BASE}/onboarding`)
  await dismiss()
  await shot(page, 'editor-1120x700')
})
await at(1024, 693, async () => {
  await page.goto(`${BASE}/onboarding`)
  await dismiss()
  await shot(page, 'editor-1024x693-boundary')
})

// Completion step (requires REAL playback for the listen evidence).
await at(1232, 693, async () => {
  await page.goto(`${BASE}/onboarding`)
  await page.getByText('The scene list').waitFor()
  for (let i = 0; i < 4; i++) await page.getByRole('button', { name: 'Next step' }).click()
  await page.locator('[data-tour="toggle-line"]').click()
  await page.getByRole('button', { name: 'Next step' }).click()
  await page.getByRole('button', { name: 'Next step' }).click()
  await page.locator('[data-tour="fit"]').click()
  await page.getByRole('button', { name: 'Next step' }).click()
  await page.getByRole('button', { name: /Original line · Onyx/ }).click()
  await page.getByRole('button', { name: 'Next step' }).click()
  await page.locator('[data-tour="preview"] button').click()
  const dialogVideo = page.getByRole('dialog').locator('video')
  await dialogVideo.click()
  await page.waitForTimeout(600)
  await shot(page, 'editor-1232x693-listen-playing')
  await page.getByRole('button', { name: 'Done listening' }).click()
  await page.getByRole('button', { name: 'Next step' }).click()
  await page.getByRole('heading', { name: 'That’s the loop' }).waitFor()
  await shot(page, 'editor-1232x693-complete')
})

// New correction-pass states: initial transcript-authority checks, the
// speed-consistent trim at 0.75×, the genuine 404 page, and narrow exits.
await at(1232, 693, async () => {
  await page.goto(`${BASE}/onboarding`)
  await page.getByText('The scene list').waitFor()
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: 'Checks' }).click()
  await page.getByText('7 talk over dialogue').waitFor()
  await shot(page, 'editor-1232x693-checks-initial-authority')
  await page
    .locator('aside[aria-label="Timing checks panel"]')
    .getByRole('button', { name: /Scene 5/ })
    .click()
  await page.getByLabel('Playback speed').selectOption('0.75')
  await page.locator('[data-tour="fit"]').click()
  await page.getByText(/Kept the first 15 of 44 words/).waitFor()
  await shot(page, 'editor-1232x693-fit-at-0.75x')
})
await at(1232, 693, async () => {
  await page.goto(`${BASE}/dashboard`)
  await page.getByText("That page isn't part of this demo.").waitFor()
  await shot(page, 'host-404-dashboard')
})
await at(390, 844, async () => {
  await page.goto(`${BASE}/onboarding`)
  await page.getByRole('link', { name: 'Back to the intro' }).waitFor()
  await shot(page, 'fallback-390-standalone-exit')
})

// ── Fallback states ──────────────────────────────────────────────────────────
await at(768, 900, async () => {
  await page.goto(`${BASE}/onboarding`)
  await page.getByText('needs a wider display').waitFor()
  await shot(page, 'fallback-768')
})
await at(390, 844, async () => {
  await page.goto(`${BASE}/onboarding?embed=1`)
  await page.getByText('needs a wider display').waitFor()
  await shot(page, 'fallback-390x844-embed')
})

// 200% zoom equivalent: 1440 physical at zoom 2 → 720 CSS px layout.
await at(720, 450, async () => {
  await page.goto(`${BASE}/onboarding`)
  await page.getByText('needs a wider display').waitFor()
  await shot(page, 'zoom200-equivalent-720x450')
})

// ── Dev-only Figure 00 harness (requires dev server on :5175) ───────────────
try {
  await at(1512, 1000, async () => {
    await page.goto('http://localhost:5175/review/figure-00', { timeout: 4000 })
    await page.waitForTimeout(1200)
    await shot(page, 'harness-figure00-1512')
  })
} catch {
  console.log('dev server not running — harness capture skipped')
}

await browser.close()
console.log(`captures written to ${OUT}`)
