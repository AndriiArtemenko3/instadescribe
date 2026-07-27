import AxeBuilder from '@axe-core/playwright'
import { test, expect } from './fixtures'

// Automated accessibility scan (WCAG 2.x A + AA rulesets) over the retained
// routes and states. Any violation fails — and this suite runs in CI.

async function scan(page: import('@playwright/test').Page, state: string) {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze()
  expect(
    results.violations.map((v) => ({
      id: v.id,
      impact: v.impact,
      nodes: v.nodes.map((n) => n.target.join(' ')).slice(0, 5),
    })),
    `axe violations in state: ${state}`,
  ).toEqual([])
}

test('intro', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Start now' }).waitFor()
  await scan(page, 'intro 1440x900')
})

test('editor — first walkthrough step (modal)', async ({ page }) => {
  await page.goto('/onboarding')
  await page.getByText('The scene list').waitFor()
  await scan(page, 'editor step 1')
})

test('editor — free exploration, script panel', async ({ page }) => {
  await page.goto('/onboarding')
  await page.getByText('The scene list').waitFor()
  await page.keyboard.press('Escape')
  await scan(page, 'editor explore/script')
})

test('editor — checks and characters panels', async ({ page }) => {
  await page.goto('/onboarding')
  await page.getByText('The scene list').waitFor()
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: 'Checks' }).click()
  await scan(page, 'checks panel')
  await page.getByRole('button', { name: 'Characters' }).click()
  await scan(page, 'characters panel')
})

test('listen dialog', async ({ page }) => {
  await page.goto('/onboarding')
  await page.getByText('The scene list').waitFor()
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: 'Play described example' }).click()
  await page.getByRole('dialog', { name: 'Pre-rendered described example' }).waitFor()
  await scan(page, 'listen dialog')
})

test('narrow text fallback', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/onboarding')
  await page.getByText('needs a wider display').waitFor()
  await scan(page, 'narrow fallback 390')
})
