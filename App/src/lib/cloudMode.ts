// Explicit cloud mode (G7): the React/Vite client talks to the FastAPI
// cloud-core (/api/v1) instead of the legacy Flask server. Demo and study
// builds branch BEFORE any cloud logic and keep their existing behavior.
// This is the single home for the mode flag and the API base URL — do not
// duplicate base-url logic in other modules.

import { isDemoBuild, isStudyMode } from './session'
import { isDevelopmentRuntime, publicApiBaseOverride, publicFlag } from './runtimeEnv'

export function isCloudMode(): boolean {
  return publicFlag('cloudMode')
}

/**
 * Cloud SESSION semantics (session-scoped storage, cloud boundaries) apply
 * only when no demo/study surface takes precedence — the one place the
 * "demo and study branch before cloud logic" rule is encoded for storage.
 */
export function isCloudSession(): boolean {
  return isCloudMode() && !isDemoBuild() && !isStudyMode()
}

/**
 * API origin for cloud mode.
 * - Development default: http://localhost:8000 (the local FastAPI stack).
 * - Production cloud build: an explicitly EMPTY VITE_API_BASE stays valid
 *   and means same-origin `/api/*` routing (CloudFront -> ALB in G9); no
 *   localhost origin is ever baked into a production build.
 */
export function cloudApiBase(): string {
  const configured = publicApiBaseOverride()
  if (configured !== undefined && configured !== '') return configured
  return isDevelopmentRuntime() ? 'http://localhost:8000' : ''
}
