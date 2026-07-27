// Embed exit contract. The exit event is posted ONLY to the explicitly
// allowed parent origins — never '*'. It carries no user data. The parent
// must verify both event.origin (the demo origin) and event.source (the
// iframe's contentWindow); see docs/portfolio-demo/EMBED_CONTRACT.md.

export const EXIT_MESSAGE_TYPE = 'instascribe-live-onboarding:exit'

export const PARENT_ORIGINS: readonly string[] = [
  'https://andriiartemenko.com',
  'https://www.andriiartemenko.com',
  // Preview strategy: during local development the harness page is served
  // from the same origin as the demo, so the dev build (and only the dev
  // build) also targets its own origin.
  ...(import.meta.env.DEV && typeof window !== 'undefined' ? [window.location.origin] : []),
]

export function postExitMessage(): void {
  if (window.parent === window) return
  for (const origin of PARENT_ORIGINS) {
    try {
      window.parent.postMessage({ type: EXIT_MESSAGE_TYPE }, origin)
    } catch {
      /* a non-matching targetOrigin is dropped by the browser; best-effort */
    }
  }
}
