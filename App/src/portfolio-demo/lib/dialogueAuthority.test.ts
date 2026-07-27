import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { getSceneCollision, estimateSpeechSecs, sceneGapSecs } from '@/lib/collisions'
import { toScene, toAdGap } from '@/lib/transforms'
import type { PipelineScene, PipelineAdGap } from '@/types'
import { transcriptToDialogueEvents, type TranscriptUtterance } from './fixtures'
import { fitToGap } from './fitToGap'

// Pins the demo's dialogue authority and every number the walkthrough quotes
// to the committed fixtures. If a fixture changes, these fail loudly instead
// of letting the copy drift from the data.

const FIXTURES = join(__dirname, '..', '..', '..', 'public', 'data', 'sintel-blender-cc')
const transcript = JSON.parse(
  readFileSync(join(FIXTURES, 'transcript.json'), 'utf8'),
) as TranscriptUtterance[]
const scenes = (JSON.parse(readFileSync(join(FIXTURES, 'scenes.json'), 'utf8')) as PipelineScene[])
  .filter((s) => s.end > s.start)
  .map(toScene)
const adGaps = (
  JSON.parse(readFileSync(join(FIXTURES, 'ad_placement_gaps.json'), 'utf8')) as PipelineAdGap[]
).map(toAdGap)
const dialogue = transcriptToDialogueEvents(transcript)

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

  it('covers the late dialogue the coarse audio_events file labels as silence', () => {
    expect(dialogue.filter((d) => d.startSecs >= 32).length).toBe(4)
  })
})

describe('walkthrough arithmetic (1×, 0.4 s/word, 0.25 s mux offset, 0.5 s tolerance)', () => {
  const byNumber = (n: number) => scenes.find((s) => s.sceneNumber === n)!

  it('scene 2: 45 words ≈ 18.0 s; film speaks first (26.22 < 26.25); overlap ≈ 4.7 s', () => {
    const s2 = byNumber(2)
    expect(s2.text.trim().split(/\s+/)).toHaveLength(45)
    expect(estimateSpeechSecs(s2.text, 1)).toBeCloseTo(18.0, 1)
    expect(dialogue[0].startSecs).toBeLessThan(s2.startSecs + 0.25) // 26.22 < 26.25
    const c = getSceneCollision(s2, dialogue, adGaps)
    expect(c.collides).toBe(true)
    expect(c.overlapSecs).toBeCloseTo(4.73, 1)
  })

  it('scene 2: no trim at any offered speed can clear its collision', () => {
    const s2 = byNumber(2)
    const gap = sceneGapSecs(s2, adGaps) // 6.88 s
    expect(gap).toBeCloseTo(6.88, 2)
    for (const speed of [0.75, 1, 1.25, 1.5]) {
      const trimmed = fitToGap(s2.text, gap, speed)
      const after = getSceneCollision({ ...s2, text: trimmed.text, voiceSpeed: speed }, dialogue, adGaps)
      expect(after.collides).toBe(true) // the head-on overlap survives every trim
    }
  })

  it('scene 5: 44 words ≈ 17.6 s overruns its 8.0 s gap into "Oh"/"Skills" (≈2.8 s overlap)', () => {
    const s5 = byNumber(5)
    expect(s5.text.trim().split(/\s+/)).toHaveLength(44)
    expect(estimateSpeechSecs(s5.text, 1)).toBeCloseTo(17.6, 1)
    expect(sceneGapSecs(s5, adGaps)).toBeCloseTo(8.0, 2)
    const c = getSceneCollision(s5, dialogue, adGaps)
    expect(c.collides).toBe(true)
    expect(c.overlapSecs).toBeCloseTo(2.8, 1)
  })

  it('scene 5: the trim clears BOTH the overrun and the collision at every offered speed', () => {
    const s5 = byNumber(5)
    const gap = sceneGapSecs(s5, adGaps)
    for (const speed of [0.75, 1, 1.25, 1.5]) {
      const trimmed = fitToGap(s5.text, gap, speed)
      expect(trimmed.estimatedSecs).toBeLessThanOrEqual(gap)
      const after = getSceneCollision({ ...s5, text: trimmed.text, voiceSpeed: speed }, dialogue, adGaps)
      expect(after.collides).toBe(false)
    }
  })

  it('at 1× exactly scenes 2,3,4,5,6,8,9 collide and scenes 1,7 are clear', () => {
    const colliding = scenes
      .filter((s) => getSceneCollision(s, dialogue, adGaps).collides)
      .map((s) => s.sceneNumber)
    expect(colliding).toEqual([2, 3, 4, 5, 6, 8, 9])
  })
})
