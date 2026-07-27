// "Fit to gap (local)" — a deterministic, in-browser trim. NOT semantic
// rewriting and NOT a model call: it keeps the leading words of the draft and
// cuts the rest to a word budget derived from the available silence.
// Uses the demo's single timing model (0.4 s/word = 2.5 words/second, the
// same constant the collision/timing estimates use) so the trim note and the
// panel's estimate never disagree.

export const FIT_WORDS_PER_SEC = 2.5
export const MIN_FIT_GAP_SECS = 1.5

export interface FitResult {
  text: string
  targetSecs: number
  targetWords: number
  keptWords: number
  totalWords: number
  estimatedSecs: number
  changed: boolean
}

export function fitToGap(text: string, targetSecs: number): FitResult {
  const words = text.trim().split(/\s+/).filter(Boolean)
  const budget = Math.max(3, Math.round(targetSecs * FIT_WORDS_PER_SEC))
  const kept = words.slice(0, budget)
  let out = kept.join(' ')
  const changed = kept.length < words.length
  if (changed) out = out.replace(/[,;:]?$/, '') + '.'
  return {
    text: out,
    targetSecs,
    targetWords: budget,
    keptWords: kept.length,
    totalWords: words.length,
    estimatedSecs: Math.round((kept.length / FIT_WORDS_PER_SEC) * 100) / 100,
    changed,
  }
}

/**
 * Demo gating: the trim is offered when the line's estimated speech genuinely
 * exceeds the silence available to its scene and there is a usable gap to trim
 * into. (Unlike the app's Smart Fill this does not require a dialogue
 * collision — a pure timing overrun is exactly what a local trim can fix.)
 */
export function canFitToGap(estSecs: number, gapSecs: number): boolean {
  return gapSecs >= MIN_FIT_GAP_SECS && estSecs > gapSecs
}
