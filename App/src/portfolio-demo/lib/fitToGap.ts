// "Fit to gap (local)" — a deterministic, in-browser trim. NOT semantic
// rewriting and NOT a model call. It keeps leading words up to a word budget
// derived from the USABLE silence (measured from the narration's real start —
// see timing.ts) and the selected playback speed, then applies a small
// deterministic boundary cleanup: dangling function words and comma-open
// phrase endings are rolled back (never new content invented) so the trim
// doesn't publish fragments like "…a dragon-like creature as."
//
// The budget floors (never rounds up), and the cleanup only shortens, so an
// accepted trim always genuinely fits its target at the trimming speed.

export const SECS_PER_WORD = 0.4
export const MIN_FIT_GAP_SECS = 1.5

// Deterministic, documented boundary-cleanup stoplist: articles, conjunctions,
// prepositions, pronouns and auxiliaries that cannot honestly end a sentence.
const DANGLING_WORDS = new Set([
  'a', 'an', 'the', 'and', 'or', 'but', 'as', 'of', 'in', 'on', 'at', 'to', 'with',
  'for', 'from', 'by', 'over', 'into', 'onto', 'through', 'while', 'both', 'their',
  'her', 'his', 'its', 'they', 'she', 'he', 'it', 'is', 'are', 'was', 'were', 'be',
  'been', 'being', 'that', 'this', 'these', 'those', 'which', 'then', 'than', 'so',
  'up', 'down', 'out', 'off', 'near', 'against', 'between', 'amid', 'atop', 'upon',
  'without', 'within',
])

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

  // Boundary cleanup: roll back dangling function words and words that carry
  // a trailing comma/semicolon/colon (an open phrase boundary).
  while (kept.length > 3) {
    const last = kept[kept.length - 1]
    const bare = last.toLowerCase().replace(/[^a-z-]/g, '')
    if (DANGLING_WORDS.has(bare) || /[,;:]$/.test(last)) {
      kept.pop()
      continue
    }
    break
  }

  let out = kept.join(' ').replace(/[,;:]$/, '')
  const changed = kept.length < words.length
  if (changed && !/[.!?]$/.test(out)) out += '.'
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
 * Eligibility: a usable silence exists and the line currently exceeds it.
 * The editor additionally requires the trial trim to be RAW-clear of dialogue
 * (timing.ts) before offering the control.
 */
export function canFitToGap(estSecs: number, usableSecs: number): boolean {
  return usableSecs >= MIN_FIT_GAP_SECS && estSecs > usableSecs
}
