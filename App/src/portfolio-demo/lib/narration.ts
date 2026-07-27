// Honest narration playback for the demo. Two clearly distinct sources:
//
//  1. Pre-generated line (Onyx): a committed mp3 rendered by the project's TTS
//     pipeline for the ORIGINAL draft of a scene. It never reflects edits; the
//     UI says so. Playback speed is real (HTMLMediaElement.playbackRate).
//  2. Browser voice: the visitor's own browser reads the CURRENT text via the
//     Web Speech API (speechSynthesis). Feature-detected; when unavailable the
//     control is not shown and an honest note appears instead.

export function speechSynthesisAvailable(): boolean {
  return typeof window !== 'undefined' && 'speechSynthesis' in window
}

export interface PlaybackHandle {
  stop: () => void
}

export function playBakedLine(
  url: string,
  rate: number,
  onEnded: () => void,
  onError: (message: string) => void,
): PlaybackHandle {
  const audio = new Audio(url)
  audio.preload = 'auto'
  audio.playbackRate = rate
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
  onEnded: () => void,
  onError: (message: string) => void,
): PlaybackHandle {
  if (!speechSynthesisAvailable()) {
    onError('This browser does not offer speech synthesis.')
    return { stop: () => {} }
  }
  const utterance = new SpeechSynthesisUtterance(text)
  utterance.rate = rate
  utterance.onend = onEnded
  utterance.onerror = (e) => {
    // "interrupted"/"canceled" fire on our own stop(); that is not an error state.
    if (e.error === 'interrupted' || e.error === 'canceled') return
    onError('Your browser could not read the text aloud.')
  }
  window.speechSynthesis.cancel()
  window.speechSynthesis.speak(utterance)
  return {
    stop: () => {
      utterance.onend = null
      window.speechSynthesis.cancel()
    },
  }
}
