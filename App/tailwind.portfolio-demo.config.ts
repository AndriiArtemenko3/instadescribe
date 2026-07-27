import type { Config } from 'tailwindcss'
import base from './tailwind.config'

// Tailwind config for the dedicated portfolio-demo entry: same theme tokens as
// the application, with the demo's own sources added to the scan. Used only by
// vite.portfolio-demo.config.ts.
const config: Config = {
  ...base,
  content: [
    './portfolio-demo.html',
    './src/**/*.{ts,tsx}',
  ],
}

export default config
