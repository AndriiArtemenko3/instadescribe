// G7 B5/B7: the EXACT canonical pipeline scene_id survives the transform —
// shuffled and non-contiguous inputs (scene_2, scene_10) keep their raw IDs
// while numeric ids remain UI ordering only.

import { describe, expect, it } from 'vitest'
import { toScene } from './transforms'
import type { PipelineScene } from '@/types'

function raw(sceneId: string, start: number, end: number): PipelineScene {
  return {
    scene_id: sceneId,
    start,
    end,
    frame_indices: [],
    character_ids: [],
    caption_template: '',
    caption: 'text',
    render_mode: 'auto',
    locked: false,
    needs_review: false,
  }
}

describe('toScene canonical identity', () => {
  it('retains the exact pipeline scene_id as sceneKey', () => {
    const scene = toScene(raw('scene_7', 0, 2), 0)
    expect(scene.sceneKey).toBe('scene_7')
    expect(scene.id).toBe(1) // ordering only
    expect(scene.sceneNumber).toBe(1)
  })

  it('keeps raw IDs for shuffled and non-contiguous inputs', () => {
    const inputs = [raw('scene_10', 20, 24), raw('scene_2', 4, 8), raw('scene_5', 10, 12)]
    const scenes = inputs.map((r, i) => toScene(r, i))
    expect(scenes.map((s) => s.sceneKey)).toEqual(['scene_10', 'scene_2', 'scene_5'])
    expect(scenes.map((s) => s.id)).toEqual([1, 2, 3])
    // A positional PATCH would have hit scene_1/scene_2/scene_3 — the
    // canonical keys prove that mistake is now impossible.
    expect(scenes.map((s) => s.sceneKey)).not.toContain('scene_1')
  })
})
