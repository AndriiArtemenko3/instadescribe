import { describe, it, expect } from 'vitest'
import { fitToGap, canFitToGap, FIT_WORDS_PER_SEC } from './fitToGap'

describe('fitToGap', () => {
  const longDraft =
    'Filtered beams of morning light fall over a young woman and a dragon-like creature as they ' +
    'awaken in a small, dim shelter, lying close together on a patchwork of blankets. She stirs ' +
    'gently, while it nestles against her, both basking in the soft glow.'

  it('trims to round(targetSecs * 2.5) words and ends with a period', () => {
    const r = fitToGap(longDraft, 8)
    expect(r.targetWords).toBe(Math.round(8 * FIT_WORDS_PER_SEC)) // 20
    expect(r.keptWords).toBe(20)
    expect(r.changed).toBe(true)
    expect(r.text.endsWith('.')).toBe(true)
    expect(r.text.split(/\s+/)).toHaveLength(20)
    // Leading clause preserved verbatim (modulo the trailing period).
    expect(longDraft.replace(/,/g, '').startsWith(r.text.slice(0, -1).replace(/,/g, ''))).toBe(
      true,
    )
    // The estimate uses the same 0.4 s/word model as the editor's checks.
    expect(r.estimatedSecs).toBe(8)
  })

  it('strips a trailing comma/semicolon/colon before adding the period', () => {
    const r = fitToGap('one two three four, five six', 4 / FIT_WORDS_PER_SEC)
    expect(r.text).toBe('one two three four.')
  })

  it('never trims below 3 words', () => {
    const r = fitToGap('alpha beta gamma delta', 0.1)
    expect(r.keptWords).toBe(3)
  })

  it('leaves a short line unchanged', () => {
    const r = fitToGap('She waits quietly.', 8)
    expect(r.changed).toBe(false)
    expect(r.text).toBe('She waits quietly.')
  })

  it('is deterministic', () => {
    expect(fitToGap(longDraft, 8)).toEqual(fitToGap(longDraft, 8))
  })
})

describe('canFitToGap', () => {
  it('offers the trim only for a genuine overrun with a usable gap', () => {
    expect(canFitToGap(18.4, 8)).toBe(true) // scene 5's real situation
    expect(canFitToGap(6, 8)).toBe(false) // already fits
    expect(canFitToGap(18.4, 1.0)).toBe(false) // gap too small to trim into
  })
})
