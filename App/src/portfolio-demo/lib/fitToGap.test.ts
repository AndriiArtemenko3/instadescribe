import { describe, it, expect } from 'vitest'
import { fitToGap, canFitToGap, SECS_PER_WORD } from './fitToGap'

// Scene 5's committed draft (44 words) — the walkthrough's trim example.
const SCENE5 =
  'Filtered beams of morning light fall over a young woman and a dragon-like creature as they ' +
  'awaken in a small, dim shelter, lying close together on a patchwork of blankets. She stirs ' +
  'gently, while it nestles against her, both basking in the soft glow.'

describe('fitToGap — speed-aware budget (floor, never overshoot)', () => {
  it.each([
    [0.75, 15],
    [1, 20],
    [1.25, 25],
    [1.5, 30],
  ])('at %s× an 8.0s gap budgets %i words and the result genuinely fits', (speed, expectedWords) => {
    const r = fitToGap(SCENE5, 8, speed)
    expect(r.targetWords).toBe(expectedWords)
    expect(r.keptWords).toBe(Math.min(expectedWords, r.totalWords))
    expect(r.estimatedSecs).toBeLessThanOrEqual(8)
    // One timing model everywhere: est = words × 0.4 / speed.
    expect(r.estimatedSecs).toBeCloseTo((r.keptWords * SECS_PER_WORD) / speed, 2)
  })

  it('floors instead of rounding: gap 6.88s at 0.75× gives 12 words (6.4s), not 13 (6.93s)', () => {
    const r = fitToGap(SCENE5, 6.88, 0.75)
    expect(r.targetWords).toBe(12)
    expect(r.estimatedSecs).toBeLessThanOrEqual(6.88)
  })

  it('is idempotent per speed: re-trimming the trimmed text changes nothing', () => {
    const first = fitToGap(SCENE5, 8, 1.25)
    const second = fitToGap(first.text, 8, 1.25)
    expect(second.changed).toBe(false)
    expect(second.text).toBe(first.text)
  })

  it('trims hand-edited text the same deterministic way', () => {
    const edited = 'One two three four five six seven eight nine ten eleven twelve thirteen'
    const r = fitToGap(edited, 2, 1)
    expect(r.targetWords).toBe(5)
    expect(r.text).toBe('One two three four five.')
  })

  it('strips a trailing comma/semicolon/colon before adding the period', () => {
    expect(fitToGap('one two three four, five six', 1.6, 1).text).toBe('one two three four.')
  })

  it('never trims below 3 words', () => {
    expect(fitToGap('alpha beta gamma delta', 0.1, 1).keptWords).toBe(3)
  })

  it('leaves a short line unchanged', () => {
    const r = fitToGap('She waits quietly.', 8, 1)
    expect(r.changed).toBe(false)
    expect(r.text).toBe('She waits quietly.')
  })
})

describe('canFitToGap', () => {
  it('offers the trim only for a genuine overrun with a usable gap', () => {
    expect(canFitToGap(17.6, 8)).toBe(true) // scene 5 at 1×
    expect(canFitToGap(23.47, 8)).toBe(true) // scene 5 at 0.75×
    expect(canFitToGap(6, 8)).toBe(false) // already fits
    expect(canFitToGap(18, 1.0)).toBe(false) // gap too small to trim into
  })
})
