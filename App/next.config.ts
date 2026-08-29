import type { NextConfig } from 'next'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const appDirectory = path.dirname(fileURLToPath(import.meta.url))

const nextConfig: NextConfig = {
  // Keep framework-generated agent instruction files out of this retained
  // Vite/Next dual-entry workspace.
  agentRules: false,
  // The Vite scripts and entry point remain the deployable rollback path.
  // This output shape is suitable for the future self-hosted App Router image.
  output: 'standalone',
  // Docker builds from the npm workspace root. Preserve the monorepo path in
  // standalone output so the runtime entry is App/server.js with its traced
  // root dependencies beside it.
  outputFileTracingRoot: path.resolve(appDirectory, '..'),
  reactStrictMode: true,
  // These are immutable build identities, not deploy-time configuration or
  // secrets. They keep the retained Vite build on its legacy adapter while
  // the App Router uses the authenticated same-origin BFF.
  env: {
    NEXT_PUBLIC_APP_ROUTER: '1',
    NEXT_PUBLIC_CLOUD_MODE: '1',
    NEXT_PUBLIC_API_BASE: '',
  },
  typescript: {
    tsconfigPath: 'tsconfig.next.json',
  },
}

export default nextConfig
