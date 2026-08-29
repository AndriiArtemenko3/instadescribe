// Explicitly allowlisted public configuration shared by the retained Vite
// build and the App Router build. Server secrets must never be added here.

type PublicSetting = 'apiBase' | 'cloudMode' | 'studyMode' | 'demoMode' | 'appRouter'

interface VitePublicEnv {
  VITE_API_BASE?: string
  VITE_CLOUD_MODE?: string
  VITE_STUDY_MODE?: string
  VITE_DEMO_MODE?: string
  DEV?: boolean
}

// Vite intentionally compiles without Node globals. Declaring only the
// allowlisted public shape avoids broadening that client type boundary.
declare const process: {
  env: {
    NEXT_PUBLIC_API_BASE?: string
    NEXT_PUBLIC_CLOUD_MODE?: string
    NEXT_PUBLIC_STUDY_MODE?: string
    NEXT_PUBLIC_DEMO_MODE?: string
    NEXT_PUBLIC_APP_ROUTER?: string
    NODE_ENV?: string
  }
}

function viteEnv(): VitePublicEnv {
  // Next supports import.meta syntax, but not Vite's env bag. The fallback
  // keeps this client-side adapter safe in both bundles.
  return (import.meta.env as VitePublicEnv | undefined) ?? {}
}

function nextValue(setting: PublicSetting): string | undefined {
  if (typeof process === 'undefined') return undefined

  // Direct references let Next inline only these deliberately public values.
  switch (setting) {
    case 'apiBase':
      return process.env.NEXT_PUBLIC_API_BASE
    case 'cloudMode':
      return process.env.NEXT_PUBLIC_CLOUD_MODE
    case 'studyMode':
      return process.env.NEXT_PUBLIC_STUDY_MODE
    case 'demoMode':
      return process.env.NEXT_PUBLIC_DEMO_MODE
    case 'appRouter':
      return process.env.NEXT_PUBLIC_APP_ROUTER
  }
}

function viteValue(setting: PublicSetting): string | undefined {
  const env = viteEnv()
  switch (setting) {
    case 'apiBase':
      return env.VITE_API_BASE
    case 'cloudMode':
      return env.VITE_CLOUD_MODE
    case 'studyMode':
      return env.VITE_STUDY_MODE
    case 'demoMode':
      return env.VITE_DEMO_MODE
    case 'appRouter':
      return undefined
  }
}

function publicValue(setting: PublicSetting): string | undefined {
  return nextValue(setting) ?? viteValue(setting)
}

/** An empty base deliberately means same-origin and must be preserved. */
export function publicApiBaseOverride(): string | undefined {
  return publicValue('apiBase')
}

export function legacyApiBase(): string {
  return publicApiBaseOverride() ?? 'http://localhost:8765'
}

export function publicFlag(setting: Exclude<PublicSetting, 'apiBase'>): boolean {
  return publicValue(setting) === '1'
}

/** True only in the authenticated App Router build, never in Vite rollback. */
export function isAppRouterRuntime(): boolean {
  return publicFlag('appRouter')
}

export function isDevelopmentRuntime(): boolean {
  const viteDevelopment = viteEnv().DEV
  if (typeof viteDevelopment === 'boolean') return viteDevelopment
  return typeof process !== 'undefined' && process.env.NODE_ENV === 'development'
}
