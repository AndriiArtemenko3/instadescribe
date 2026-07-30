import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { toScene, toAdGap } from '@/lib/transforms'
import type { PipelineScene, PipelineAdGap, Scene } from '@/types'
import { transcriptToDialogueEvents, type TranscriptUtterance } from './fixtures'
import { sentenceCaseStart } from './text'
import { fitToGap, canFitToGap, MIN_FIT_GAP_SECS } from './fitToGap'
import {
  adStartSecs,
  estimateSecs,
  isRawClear,
  rawDialogueOverlapSecs,
  sceneTiming,
  timeToFirstDialogueSecs,
  usableSilenceSecs,
  SECS_PER_WORD,
} from './timing'

// Pins the demo's dialogue authority and the ENTIRE fit-acceptance matrix —
// every active scene at every offered speed — to the committed fixtures.
// Acceptance is RAW: zero dialogue overlap (float epsilon only), never the
// 0.5 s display tolerance.

const FIXTURES = join(__dirname, '..', '..', '..', 'public', 'data', 'sintel-blender-cc')
const transcript = JSON.parse(
  readFileSync(join(FIXTURES, 'transcript.json'), 'utf8'),
) as TranscriptUtterance[]
// Scenes exactly as the demo loads them (display-layer sentence casing).
const scenes: Scene[] = (
  JSON.parse(readFileSync(join(FIXTURES, 'scenes.json'), 'utf8')) as PipelineScene[]
)
  .filter((s) => s.end > s.start)
  .map(toScene)
  .map((s) => ({ ...s, text: sentenceCaseStart(s.text) }))
const adGaps = (
  JSON.parse(readFileSync(join(FIXTURES, 'ad_placement_gaps.json'), 'utf8')) as PipelineAdGap[]
).map(toAdGap)
const dialogue = transcriptToDialogueEvents(transcript)

const SPEEDS = [0.75, 1, 1.25, 1.5]
const byNumber = (n: number) => scenes.find((s) => s.sceneNumber === n)!

describe('transcript is the single dialogue authority', () => {
  it('contains exactly the seven committed utterance intervals', () => {
    expect(dialogue.map((d) => [d.startSecs, d.endSecs, d.transcript])).toEqual([
      [26.22, 26.78, 'Oh'],
      [26.78, 28.86, "Hey, it's almost done"],
      [30.0, 32.12, 'Hey sit still'],
      [47.56, 49.06, "Good night's kiss"],
      [70.32, 71.72, 'Oh'],
      [73.48, 74.88, 'Skills'],
      [108.02, 109.42, 'Oh'],
    ])
  })
})

describe('the authoritative usable-silence window (blocker 1)', () => {
  it('measures from the REAL narration start (scene.start + 0.25)', () => {
    // Scene 9: gap ends at 108.02 where "Oh" begins; narration starts 105.25.
    expect(usableSilenceSecs(byNumber(9), adGaps)).toBeCloseTo(2.77, 5)
    // Scene 5: bounded by the scene's own end (68), not the gap end (70.32).
    expect(usableSilenceSecs(byNumber(5), adGaps)).toBeCloseTo(7.75, 5)
    // Scene 2: no curated gap contains its narration start — nothing usable.
    expect(usableSilenceSecs(byNumber(2), adGaps)).toBe(0)
  })

  it('kills the scene-9 @1.5× regression: the old gap∩scene budget overlapped dialogue', () => {
    const s9 = byNumber(9)
    const start = adStartSecs(s9)
    // The OLD budget (gap∩scene from scene.start = 3.02 s) allowed 11 words at
    // 1.5× → ends 108.183, a real 0.163 s overlap labelled "clear" by the
    // 0.5 s tolerance. The authoritative window forbids it.
    const oldEnd = start + (11 * SECS_PER_WORD) / 1.5
    expect(rawDialogueOverlapSecs(start, oldEnd, dialogue)).toBeGreaterThan(0.15)
    // New model: every accepted trim ends before 108.02 (asserted in the
    // exhaustive matrix below).
  })
})

describe('exhaustive fit-acceptance matrix — every scene × every speed (blocker 1)', () => {
  for (const scene of scenes) {
    for (const speed of SPEEDS) {
      const label = `scene ${scene.sceneNumber} @ ${speed}×`
      it(label, () => {
        const t = sceneTiming(scene, dialogue, adGaps, speed)
        const eligible = canFitToGap(t.estSecs, t.usableSecs)

        if (scene.sceneNumber === 2) {
          // Head-on: dialogue speaks at the narration's first beat; no usable
          // silence; the trim is never offered at ANY speed.
          expect(t.headOnDialogue).toBe(true)
          expect(eligible).toBe(false)
          return
        }
        if (scene.sceneNumber === 6) {
          // Not head-on (dialogue starts 1.07 s in), but below the minimum
          // usable window: unfixable by trim, different reason than scene 2.
          expect(t.headOnDialogue).toBe(false)
          expect(timeToFirstDialogueSecs(t.adStart, dialogue)).toBeCloseTo(1.07, 2)
          expect(eligible).toBe(false)
          return
        }
        if (!eligible) {
          // Already fits (e.g. scene 1 at ≥1×): nothing to accept.
          expect(t.estSecs).toBeLessThanOrEqual(Math.max(t.usableSecs, scene.durationSecs))
          return
        }

        const r = fitToGap(scene.text, t.usableSecs, speed)
        const end = t.adStart + (r.keptWords * SECS_PER_WORD) / speed
        const rawOverlap = rawDialogueOverlapSecs(t.adStart, end, dialogue)
        // Estimated narration end inside the usable window…
        expect(end).toBeLessThanOrEqual(t.adStart + t.usableSecs + 1e-6)
        // …and inside the scene's own window…
        expect(r.estimatedSecs).toBeLessThanOrEqual(scene.durationSecs + 1e-6)
        // …with ZERO raw dialogue overlap (epsilon only).
        expect(isRawClear(rawOverlap)).toBe(true)
        expect(rawOverlap).toBeLessThanOrEqual(1e-6)
        // UI agreement: the demo's status derives from the same raw numbers.
        const after = sceneTiming({ ...scene, text: r.text }, dialogue, adGaps, speed)
        expect(after.talksOverDialogue).toBe(false)
        expect(after.overrunsScene).toBe(false)
      })
    }
  }
})

describe('walkthrough claims (blockers 1+2)', () => {
  it('scene 2: 45 words ≈ 18.0 s; film speaks first; overlap ≈ 4.7 s; head-on', () => {
    const s2 = byNumber(2)
    expect(s2.text.trim().split(/\s+/)).toHaveLength(45)
    expect(estimateSecs(s2.text, 1)).toBeCloseTo(18.0, 1)
    expect(dialogue[0].startSecs).toBeLessThan(adStartSecs(s2))
    const t = sceneTiming(s2, dialogue, adGaps, 1)
    expect(t.rawOverlapSecs).toBeCloseTo(4.73, 1)
    expect(t.headOnDialogue).toBe(true)
  })

  it('scene 5: 44 words ≈ 17.6 s vs 7.75 s usable; overlaps “Oh”/“Skills” by ≈ 2.8 s', () => {
    const s5 = byNumber(5)
    expect(s5.text.trim().split(/\s+/)).toHaveLength(44)
    expect(estimateSecs(s5.text, 1)).toBeCloseTo(17.6, 1)
    const t = sceneTiming(s5, dialogue, adGaps, 1)
    expect(t.rawOverlapSecs).toBeCloseTo(2.8, 1)
    expect(t.headOnDialogue).toBe(false)
  })

  it('the walkthrough scene-5 action completes at every speed (raw-clear fit)', () => {
    const s5 = byNumber(5)
    for (const speed of SPEEDS) {
      const t = sceneTiming(s5, dialogue, adGaps, speed)
      const r = fitToGap(s5.text, t.usableSecs, speed)
      const after = sceneTiming({ ...s5, text: r.text }, dialogue, adGaps, speed)
      expect(after.estSecs).toBeLessThanOrEqual(after.usableSecs + 1e-6)
      expect(after.talksOverDialogue).toBe(false)
    }
  })

  it('at 1× exactly scenes 2,3,4,5,6,8,9 talk over dialogue (raw)', () => {
    const colliding = scenes
      .filter((s) => sceneTiming(s, dialogue, adGaps, 1).talksOverDialogue)
      .map((s) => s.sceneNumber)
    expect(colliding).toEqual([2, 3, 4, 5, 6, 8, 9])
  })

  it('fit minimum window matches the offered control', () => {
    expect(MIN_FIT_GAP_SECS).toBe(1.5)
  })
})
