// THE authoritative timing model for the demo (acceptance blocker 1).
//
// Narration for a scene is muxed at `scene.start + AD_START_OFFSET`
// (export_service.py:167). The silence usable for a trimmed line is therefore
// measured FROM that narration start, inside the curated gap that contains it,
// and never past the scene's own end:
//
//   usable = min(gap.end, scene.end) − (scene.start + 0.25)   (0 if no gap)
//
// "Clear", "Placed" and fit-acceptance use RAW transcript overlap with only a
// floating-point epsilon — never the 0.5 s display tolerance the shared app
// engine uses for its warning threshold.
import type { AdGap, AudioEvent, Scene } from '@/types'

export const AD_START_OFFSET = 0.25
export const SECS_PER_WORD = 0.4
export const OVERLAP_EPSILON = 1e-6

export function adStartSecs(scene: Scene): number {
  return scene.startSecs + AD_START_OFFSET
}

export function estimateSecs(text: string, speed = 1): number {
  const words = text.trim().split(/\s+/).filter(Boolean).length
  const safeSpeed = speed > 0 ? speed : 1
  return (words * SECS_PER_WORD) / safeSpeed
}

/** Silence genuinely available to this scene's narration, from its real start. */
export function usableSilenceSecs(scene: Scene, adGaps: AdGap[]): number {
  const start = adStartSecs(scene)
  let best = 0
  for (const gap of adGaps) {
    if (start >= gap.startSecs - OVERLAP_EPSILON && start < gap.endSecs) {
      best = Math.max(best, Math.min(gap.endSecs, scene.endSecs) - start)
    }
  }
  return Math.max(0, best)
}

/** Raw spoken-dialogue overlap of [startSecs, endSecs] — no tolerance. */
export function rawDialogueOverlapSecs(
  startSecs: number,
  endSecs: number,
  dialogue: AudioEvent[],
): number {
  let total = 0
  for (const ev of dialogue) {
    if (ev.type !== 'dialogue') continue
    const s = Math.max(startSecs, ev.startSecs)
    const e = Math.min(endSecs, ev.endSecs)
    if (e > s) total += e - s
  }
  return total
}

/** Seconds from `fromSecs` until spoken dialogue (0 if already inside it). */
export function timeToFirstDialogueSecs(fromSecs: number, dialogue: AudioEvent[]): number {
  let min = Infinity
  for (const ev of dialogue) {
    if (ev.type !== 'dialogue') continue
    if (ev.endSecs > fromSecs) min = Math.min(min, Math.max(0, ev.startSecs - fromSecs))
  }
  return min
}

export function isRawClear(overlapSecs: number): boolean {
  return overlapSecs <= OVERLAP_EPSILON
}

export interface SceneTiming {
  adStart: number
  estSecs: number
  usableSecs: number
  /** Raw transcript overlap of the estimated narration window (active scenes). */
  rawOverlapSecs: number
  overlapStart: number | null
  overlapEnd: number | null
  /** Raw overlap > epsilon (the demo's truth for Conflict/clear). */
  talksOverDialogue: boolean
  /** Estimated speech exceeds the scene's own window. */
  overrunsScene: boolean
  /** Dialogue is already speaking at the narration's first beat. */
  headOnDialogue: boolean
}

export function sceneTiming(
  scene: Scene,
  dialogue: AudioEvent[],
  adGaps: AdGap[],
  speed: number,
): SceneTiming {
  const adStart = adStartSecs(scene)
  const estSecs = scene.active && scene.text.trim() ? estimateSecs(scene.text, speed) : 0
  const end = adStart + estSecs
  let rawOverlapSecs = 0
  let overlapStart: number | null = null
  let overlapEnd: number | null = null
  if (estSecs > 0) {
    for (const ev of dialogue) {
      if (ev.type !== 'dialogue') continue
      const s = Math.max(adStart, ev.startSecs)
      const e = Math.min(end, ev.endSecs)
      if (e > s) {
        rawOverlapSecs += e - s
        overlapStart = overlapStart === null ? s : Math.min(overlapStart, s)
        overlapEnd = overlapEnd === null ? e : Math.max(overlapEnd, e)
      }
    }
  }
  return {
    adStart,
    estSecs,
    usableSecs: usableSilenceSecs(scene, adGaps),
    rawOverlapSecs,
    overlapStart,
    overlapEnd,
    talksOverDialogue: !isRawClear(rawOverlapSecs),
    overrunsScene: estSecs > scene.durationSecs + OVERLAP_EPSILON,
    headOnDialogue: timeToFirstDialogueSecs(adStart, dialogue) <= OVERLAP_EPSILON,
  }
}
