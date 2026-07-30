import { describe, it, expect } from 'vitest'
import { fitToGap, canFitToGap, SECS_PER_WORD } from './fitToGap'
import { sentenceCaseStart } from './text'

// Scene 5 and scene 9 drafts exactly as the demo displays them (sentence-cased
// at load; the committed fixtures are unchanged).
const SCENE5 =
  'Filtered beams of morning light fall over a young woman and a dragon-like creature as they ' +
  'awaken in a small, dim shelter, lying close together on a patchwork of blankets. She stirs ' +
  'gently, while it nestles against her, both basking in the soft glow.'
const SCENE9 = sentenceCaseStart(
  'a young woman races up steep, sunlit rooftops as a dragon-like creature soars overhead, ' +
    'bathed in warm golden light. Ancient stone structures and vibrant city sprawl stretch ' +
    'beneath them, with the sun setting in radiant hues across the sky. The cinematic scene ' +
    'lingers as both figures pause at the summit, silhouetted against the glowing cityscape.',
)

// Usable silence per timing.ts: scene 5 → min(70.32, 68) − 60.25 = 7.75 s;
// scene 9 → min(108.02, 119) − 105.25 = 2.77 s.
const SCENE5_USABLE = 7.75
const SCENE9_USABLE = 2.77

describe('fitToGap — pinned scene 5 outputs at every offered speed (blockers 1+6)', () => {
  it.each([
    [0.75, 14, 'Filtered beams of morning light fall over a young woman and a dragon-like creature.'],
    [1, 17, 'Filtered beams of morning light fall over a young woman and a dragon-like creature as they awaken.'],
    [
      1.25, 24,
      'Filtered beams of morning light fall over a young woman and a dragon-like creature as they awaken in a small, dim shelter, lying close.',
    ],
    [
      1.5, 28,
      'Filtered beams of morning light fall over a young woman and a dragon-like creature as they awaken in a small, dim shelter, lying close together on a patchwork.',
    ],
  ])('at %s× keeps %i words with a clean boundary', (speed, kept, text) => {
    const r = fitToGap(SCENE5, SCENE5_USABLE, speed)
    expect(r.keptWords).toBe(kept)
    expect(r.text).toBe(text)
    expect(r.estimatedSecs).toBeLessThanOrEqual(SCENE5_USABLE)
  })
})

describe('fitToGap — pinned scene 9 outputs at every offered speed (blockers 1+6)', () => {
  it.each([
    [0.75, 4, 'A young woman races.'],
    [1, 4, 'A young woman races.'],
    [1.25, 8, 'A young woman races up steep, sunlit rooftops.'],
    [1.5, 8, 'A young woman races up steep, sunlit rooftops.'],
  ])('at %s× keeps %i words with a clean boundary', (speed, kept, text) => {
    const r = fitToGap(SCENE9, SCENE9_USABLE, speed)
    expect(r.keptWords).toBe(kept)
    expect(r.text).toBe(text)
    expect(r.estimatedSecs).toBeLessThanOrEqual(SCENE9_USABLE)
  })

  it('never publishes the malformed fragments the review flagged', () => {
    for (const speed of [0.75, 1, 1.25, 1.5]) {
      const five = fitToGap(SCENE5, SCENE5_USABLE, speed).text
      const nine = fitToGap(SCENE9, SCENE9_USABLE, speed).text
      for (const out of [five, nine]) {
        expect(out).not.toMatch(/ (as|a|an|the|of|in|on|and|or|up|to|with)\.$/)
        expect(out).not.toMatch(/,\.$/)
        expect(out).not.toMatch(/dragon-like\.$/)
      }
    }
  })
})

describe('fitToGap — boundary cleanup mechanics', () => {
  it('rolls back dangling function words', () => {
    expect(fitToGap('She sets down a bowl of', 2.4, 1).text).toBe('She sets down a bowl.')
  })

  it('rolls back comma-open phrase endings', () => {
    expect(fitToGap('A young woman races up steep, sunlit rooftops', 2.4, 1).text).toBe(
      'A young woman races.',
    )
  })

  it('never invents content — output is a prefix of the input plus a period', () => {
    const r = fitToGap(SCENE5, SCENE5_USABLE, 1)
    expect(SCENE5.startsWith(r.text.slice(0, -1))).toBe(true)
  })

  it('floors the budget so the trim always genuinely fits (0.75× regression)', () => {
    const r = fitToGap(SCENE5, 6.88, 0.75)
    expect(r.targetWords).toBe(12)
    expect(r.estimatedSecs).toBeLessThanOrEqual(6.88)
  })

  it('is idempotent per speed', () => {
    const first = fitToGap(SCENE5, SCENE5_USABLE, 1.25)
    const second = fitToGap(first.text, SCENE5_USABLE, 1.25)
    expect(second.changed).toBe(false)
    expect(second.text).toBe(first.text)
  })

  it('never trims below 3 words and uses one timing model', () => {
    const r = fitToGap('alpha beta gamma delta', 0.1, 1)
    expect(r.keptWords).toBe(3)
    expect(r.estimatedSecs).toBeCloseTo((3 * SECS_PER_WORD) / 1, 2)
  })
})

describe('canFitToGap', () => {
  it('offers the trim only for a genuine overrun with usable silence', () => {
    expect(canFitToGap(17.6, 7.75)).toBe(true) // scene 5 at 1×
    expect(canFitToGap(22.0, 2.77)).toBe(true) // scene 9 at 1×
    expect(canFitToGap(18.0, 0)).toBe(false) // scene 2: no usable silence
    expect(canFitToGap(14.8, 1.07)).toBe(false) // scene 6: below the minimum
    expect(canFitToGap(6, 7.75)).toBe(false) // already fits
  })
})
