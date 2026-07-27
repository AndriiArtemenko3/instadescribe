// Honest narration playback for the demo. Two clearly distinct sources:
//
//  1. Pre-generated line (Onyx): a committed mp3 rendered earlier by the
//     project's TTS pipeline for the ORIGINAL draft of a scene. It never
//     reflects edits; the UI says so. Speed uses HTMLMediaElement.playbackRate.
//  2. Browser voice: the visitor's browser reads the CURRENT text via the Web
//     Speech API — but ONLY through an explicitly selected voice whose
//     `localService` flag is true (see localVoice.ts). The demo never speaks
//     through an unspecified default voice, because that voice may be a
//     remote service and the page promises no live model or API calls.
//
// Completion evidence: callers receive `onStarted` only when the media
// actually enters playback ('playing' for audio, 'onstart' for speech) — an
// invoked or failed play() is not listening.

export interface PlaybackCallbacks {
  onStarted: () => void
  onEnded: () => void
  onError: (message: string) => void
}

export interface PlaybackHandle {
  stop: () => void
}

export function playBakedLine(
  url: string,
  rate: number,
  { onStarted, onEnded, onError }: PlaybackCallbacks,
): PlaybackHandle {
  const audio = new Audio(url)
  audio.preload = 'auto'
  audio.playbackRate = rate
  let started = false
  audio.addEventListener('playing', () => {
    if (!started) {
      started = true
      onStarted()
    }
  })
  audio.onended = onEnded
  audio.onerror = () => onError('Could not play the pre-generated line.')
  void audio.play().catch(() => onError('Could not play the pre-generated line.'))
  return {
    stop: () => {
      audio.onended = null
      audio.onerror = null
      audio.pause()
      audio.src = ''
    },
  }
}

export function speakWithBrowserVoice(
  text: string,
  rate: number,
  voice: SpeechSynthesisVoice,
  { onStarted, onEnded, onError }: PlaybackCallbacks,
): PlaybackHandle {
  const utterance = new SpeechSynthesisUtterance(text)
  utterance.voice = voice
  utterance.rate = rate
  utterance.onstart = onStarted
  utterance.onend = onEnded
  utterance.onerror = (e) => {
    // "interrupted"/"canceled" fire on our own stop(); not an error state.
    if (e.error === 'interrupted' || e.error === 'canceled') return
    onError('Your browser could not read the text aloud.')
  }
  window.speechSynthesis.cancel()
  window.speechSynthesis.speak(utterance)
  return {
    stop: () => {
      utterance.onstart = null
      utterance.onend = null
      window.speechSynthesis.cancel()
    },
  }
}
