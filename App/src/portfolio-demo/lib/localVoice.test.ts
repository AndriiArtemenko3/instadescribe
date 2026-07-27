import { describe, it, expect } from 'vitest'
import { pickLocalVoice } from './localVoice'

const voice = (name: string, lang: string, localService: boolean, def = false) =>
  ({ name, lang, localService, default: def }) as unknown as SpeechSynthesisVoice

describe('pickLocalVoice — never a remote or unspecified voice', () => {
  it('returns null when every voice is remote', () => {
    expect(pickLocalVoice([voice('Cloud A', 'en-US', false), voice('Cloud B', 'en-GB', false)])).toBeNull()
  })

  it('returns null for an empty list', () => {
    expect(pickLocalVoice([])).toBeNull()
  })

  it('never selects a remote voice even when it is the default', () => {
    const picked = pickLocalVoice([
      voice('Remote Default', 'en-US', false, true),
      voice('Local DE', 'de-DE', true),
    ])
    expect(picked?.name).toBe('Local DE')
    expect(picked?.localService).toBe(true)
  })

  it('prefers a local English default, then any local English, then any local', () => {
    const localEnDefault = voice('Samantha', 'en-US', true, true)
    const localEn = voice('Daniel', 'en-GB', true)
    const localFr = voice('Thomas', 'fr-FR', true)
    expect(pickLocalVoice([localFr, localEn, localEnDefault])?.name).toBe('Samantha')
    expect(pickLocalVoice([localFr, localEn])?.name).toBe('Daniel')
    expect(pickLocalVoice([localFr])?.name).toBe('Thomas')
  })
})
