// Single-audio-owner bus. Exactly one sound source may play at a time:
// the source video, a baked narration line, browser speech, or the described
// example. Claiming stops the previous owner; modals, scene changes, restart,
// exit, tour completion and unmount call stopAllAudio().

type StopFn = () => void

let current: { owner: string; stop: StopFn } | null = null

/** Register `owner` as the active source, stopping any different owner first. */
export function claimAudio(owner: string, stop: StopFn): void {
  if (current && current.owner !== owner) {
    const previous = current
    current = null
    previous.stop()
  }
  current = { owner, stop }
}

/** Stop whatever is playing (no-op when silent). */
export function stopAllAudio(): void {
  if (!current) return
  const active = current
  current = null
  active.stop()
}

/**
 * Drop `owner`'s claim without invoking its stop callback — for sources that
 * already ended naturally (audio 'ended', dialog unmount).
 */
export function clearAudioClaim(owner: string): void {
  if (current?.owner === owner) current = null
}

export function currentAudioOwner(): string | null {
  return current?.owner ?? null
}
