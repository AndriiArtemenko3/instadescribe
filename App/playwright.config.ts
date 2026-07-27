import { defineConfig } from '@playwright/test'

// Browser tests for the dedicated portfolio-demo build. They run against the
// real production bundle (build → preview) so route reloads exercise the SPA
// fallback exactly as a static host would serve it.
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: 'http://localhost:4174',
    viewport: { width: 1440, height: 900 },
  },
  webServer: {
    command: 'npm run build:portfolio-demo && npm run preview:portfolio-demo',
    port: 4174,
    reuseExistingServer: true,
    timeout: 120_000,
  },
})
