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
    colorScheme: 'light',
    ignoreHTTPSErrors: true,
    locale: 'en-GB',
    reducedMotion: 'reduce',
    screenshot: 'only-on-failure',
    timezoneId: 'UTC',
    trace: 'retain-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'mobile-chromium',
      testMatch: 'investigation-workspace.spec.ts',
      use: { ...devices['Pixel 7'] },
    },
  ],
  webServer: {
    command: 'node e2e/support/next-https.mjs',
    env: {
      APP_API_ORIGIN: 'https://api.e2e.invalid',
      AWS_REGION: 'eu-west-2',
      BROWSER_ASSERTION_SECRET: 'AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE',
      COGNITO_APP_CLIENT_ID: 'playwright-client',
      COGNITO_APP_CLIENT_SECRET: 'playwright-client-secret',
      COGNITO_USER_POOL_ID: 'eu-west-2_playwright',
      PLAYWRIGHT_APP_PORT: String(externalPort),
      PLAYWRIGHT_NEXT_PORT: String(nextPort),
      WEB_SESSION_HMAC_SECRET: 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA',
      WEB_SESSION_KMS_KEY_ID: 'alias/playwright-session-key',
      WEB_SESSION_TABLE_NAME: 'playwright-sessions',
    },
    // Poll the internal HTTP listener. Tests use the HTTPS reverse proxy so
    // production Origin and __Host- cookie behaviour stays representative.
    url: `http://127.0.0.1:${nextPort}/login`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
})
