import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'
import type { AudioEvent, Scene } from '@/types'
import { evaluateAd } from './evaluation'

// Load the SAME fixture the Python tests use, so both implementations are pinned
// to one expected score (drift guard across the language boundary).
interface RawScene {
  scene_id: string
  start: number
  end: number
  text: string
  active: boolean
  speed: number
  character_ids: string[]
}
interface Fixture {
  durationSecs: number
  entities: { id: string }[]
  audioEvents: { start: number; end: number; type: string }[]
  scenes: RawScene[]
}

const here = dirname(fileURLToPath(import.meta.url))
const fixture = JSON.parse(
  readFileSync(join(here, '../../../tests/fixtures/eval_sample.json'), 'utf8'),
) as Fixture

const scenes: Scene[] = fixture.scenes.map((s, i) => ({
  id: i + 1,
  sceneNumber: i + 1,
  sceneKey: `scene_${i + 1}`,
  startSecs: s.start,
  endSecs: s.end,
  durationSecs: s.end - s.start,
  text: s.text,
  template: '',
  characterIds: s.character_ids,
  locked: false,
  needsReview: false,
  active: s.active,
  voiceSpeed: s.speed,
}))

const audioEvents: AudioEvent[] = fixture.audioEvents.map((e, i) => ({
  id: i,
  type: e.type as AudioEvent['type'],
  startSecs: e.start,
  endSecs: e.end,
  durationSecs: e.end - e.start,
}))

describe('evaluateAd', () => {
  const report = evaluateAd(scenes, audioEvents, fixture.entities, fixture.durationSecs)

  it('matches the shared expected score (cross-language drift guard)', () => {
    expect(report.activeCount).toBe(4)
    expect(report.overall).toBeCloseTo(0.72, 5)
    expect(report.dimensions.timing).toBeCloseTo(0.75, 5)
    expect(report.dimensions.dialogue_safety).toBeCloseTo(0.75, 5)
    expect(report.dimensions.coverage).toBeCloseTo(0.8, 5)
    expect(report.dimensions.character_consistency).toBeCloseTo(0.75, 5)
    expect(report.dimensions.grounding).toBeCloseTo(0.5, 5)
  })

  it('flags the right scenes with the right issues', () => {
    const byId = new Map(report.flags.map((f) => [f.sceneId, new Set(f.issues)]))
    expect(byId.get('1')).toEqual(new Set(['dialogue_collision', 'duplicate_text']))
    expect(byId.get('2')).toEqual(new Set(['narration_too_long']))
    expect(byId.get('3')).toEqual(new Set(['orphan_character', 'duplicate_text']))
    expect(byId.has('4')).toBe(false)
  })

  it('scores empty input as zero', () => {
    const empty = evaluateAd([], [], [], 10)
    expect(empty.overall).toBe(0)
    expect(empty.activeCount).toBe(0)
  })
})
