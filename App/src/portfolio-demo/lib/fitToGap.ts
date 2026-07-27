// "Fit to gap (local)" — a deterministic, in-browser trim. NOT semantic
// rewriting and NOT a model call: it keeps the leading words of the draft and
// cuts the rest to a word budget derived from the available silence AND the
// selected playback speed, using the demo's single timing model
// (0.4 s per word at 1×; spoken seconds = words × 0.4 / speed).
//
// The budget floors (never rounds up) so the trimmed line always genuinely
// fits the target at the speed it was trimmed for — round() overshoots at
// e.g. 0.75× (gap 6.88 s → 13 words → 6.93 s spoken).

export const SECS_PER_WORD = 0.4
export const MIN_FIT_GAP_SECS = 1.5

export interface FitResult {
  text: string
  targetSecs: number
  speed: number
  targetWords: number
  keptWords: number
  totalWords: number
  estimatedSecs: number
  changed: boolean
}

export function fitToGap(text: string, targetSecs: number, speed = 1): FitResult {
  const safeSpeed = speed > 0 ? speed : 1
  const words = text.trim().split(/\s+/).filter(Boolean)
  const budget = Math.max(3, Math.floor((targetSecs / SECS_PER_WORD) * safeSpeed))
  const kept = words.slice(0, budget)
  let out = kept.join(' ')
  const changed = kept.length < words.length
  if (changed) out = out.replace(/[,;:]?$/, '') + '.'
  return {
    text: out,
    targetSecs,
    speed: safeSpeed,
    targetWords: budget,
    keptWords: kept.length,
    totalWords: words.length,
    estimatedSecs: Math.round((kept.length * SECS_PER_WORD * 100) / safeSpeed) / 100,
    changed,
  }
}

/**
 * The trim is offered when the line's estimated speech (at the current speed)
 * genuinely exceeds the silence available to its scene and there is a usable
 * gap to trim into. The editor additionally requires that a trial trim clears
 * any dialogue collision before offering the control.
 */
export function canFitToGap(estSecs: number, gapSecs: number): boolean {
  return gapSecs >= MIN_FIT_GAP_SECS && estSecs > gapSecs
}
