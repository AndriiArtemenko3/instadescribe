import { defineConfig, devices } from '@playwright/test'

const externalPort = 3217
const nextPort = 3218

export default defineConfig({
  testDir: './e2e',
  outputDir: 'test-results/playwright',
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI
    ? [['line'], ['html', { open: 'never', outputFolder: 'playwright-report' }]]
    : 'list',
  use: {
    baseURL: `https://127.0.0.1:${externalPort}`,
    ignoreHTTPSErrors: true,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command: 'node e2e/support/next-https.mjs',
    env: {
      PLAYWRIGHT_APP_PORT: String(externalPort),
      PLAYWRIGHT_NEXT_PORT: String(nextPort),
    },
    // Poll the internal HTTP listener. Tests use the HTTPS reverse proxy so
    // production Origin and __Host- cookie behaviour stays representative.
    url: `http://127.0.0.1:${nextPort}/login`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
})
