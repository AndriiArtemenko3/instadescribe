// Local-only Web Speech voice selection (privacy guarantee).
//
// An unset SpeechSynthesisUtterance voice may resolve to a REMOTE speech
// service, which would send the visitor's text off the page. The demo
// therefore speaks only through a voice whose `localService` is true, chosen
// explicitly; when no such voice exists the control is withheld with honest
// copy. getVoices() populates asynchronously (`voiceschanged`), so resolution
// is stateful with a bounded wait.

export type LocalVoiceStatus = 'pending' | 'available' | 'unavailable'

export interface LocalVoiceState {
  status: LocalVoiceStatus
  voice: SpeechSynthesisVoice | null
}

/** Pure selection rule (unit-tested): local voices only, preferring English. */
export function pickLocalVoice(voices: readonly SpeechSynthesisVoice[]): SpeechSynthesisVoice | null {
  const local = voices.filter((v) => v.localService === true)
  if (local.length === 0) return null
  return (
    local.find((v) => v.lang?.toLowerCase().startsWith('en') && v.default) ??
    local.find((v) => v.lang?.toLowerCase().startsWith('en')) ??
    local[0]
  )
}

const WAIT_MS = 2000

/**
 * Resolve the local voice, handling the async voiceschanged lifecycle.
 * Calls `onChange` with 'pending' → then exactly one terminal state.
 */
export function resolveLocalVoice(onChange: (state: LocalVoiceState) => void): () => void {
  if (typeof window === 'undefined' || !('speechSynthesis' in window)) {
    onChange({ status: 'unavailable', voice: null })
    return () => {}
  }
  const synth = window.speechSynthesis
  let settled = false
  let timer: number | null = null

  const settle = (state: LocalVoiceState) => {
    if (settled) return
    settled = true
    if (timer !== null) window.clearTimeout(timer)
    synth.removeEventListener?.('voiceschanged', tryResolve)
    onChange(state)
  }

  const tryResolve = () => {
    const voices = synth.getVoices()
    if (voices.length === 0) return // not populated yet — wait for voiceschanged
    const voice = pickLocalVoice(voices)
    settle(voice ? { status: 'available', voice } : { status: 'unavailable', voice: null })
  }

  onChange({ status: 'pending', voice: null })
  synth.addEventListener?.('voiceschanged', tryResolve)
  tryResolve()
  if (!settled) {
    timer = window.setTimeout(() => {
      // Voices never arrived: treat as no local voice rather than guessing.
      settle({ status: 'unavailable', voice: null })
    }, WAIT_MS)
  }
  return () => {
    settled = true
    if (timer !== null) window.clearTimeout(timer)
    synth.removeEventListener?.('voiceschanged', tryResolve)
  }
}
