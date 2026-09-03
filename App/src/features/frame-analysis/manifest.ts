/**
 * The frame analysis manifest produced by
 * services/worker/scripts/frame_analysis_demo.py.
 *
 * The vector metrics are cosine similarities in [-1, 1]: they say how closely
 * two embeddings point the same way. They are not confidences, probabilities or
 * accuracies, and this module never converts one into a percentage — a cosine
 * rendered as "84%" reads as a certainty it does not carry.
 */

export const MANIFEST_URL = '/frame-analysis/manifest.json'
export const FRAME_IMAGE_BASE = '/frame-analysis/frames/'

export interface VectorMetrics {
  clipCentroidSimilarity: number
  previousFrameSimilarity: number | null
  nearestSelectedSimilarity: number | null
  semanticNovelty: number
  mostSimilarFrameId: string | null
}

export interface QualityMetrics {
  sharpness: number
  exposureQuality: number
  novelty: number
  ocrDensity: number
  motionStability: number
}

export interface KeyframeOutcome {
  selected: boolean
  rank: number | null
  informationScore: number | null
  qualityScore: number | null
  selectionNearestSimilarity: number | null
  selectionSemanticNovelty: number | null
  rejectionReason: string | null
  duplicateOf: string | null
}

export interface ManifestFrame {
  frameId: string
  index: number
  timeMs: number
  shotIndex: number
  image: string | null
  width: number
  height: number
  perceptualHash: string | null
  vectorMetrics: VectorMetrics
  qualityMetrics: QualityMetrics
  keyframe: KeyframeOutcome
}

export interface FrameAnalysisManifest {
  schemaVersion: string
  media: { name: string; durationSeconds: number; sourceSha256: string }
  embeddingModel: { name: string; runtime: string; version: string; digest: string; dimension: number }
  selector: {
    version: string
    semanticEnabled: boolean
    candidateCount: number
    selectedCount: number
    maxKeyframes: number
    noveltyWeight: number
    similarityThreshold: number | null
  }
  metricDefinitions: Record<string, string>
  frames: ManifestFrame[]
}

/** Reject anything that is not a manifest before the view assumes its shape. */
export function parseManifest(payload: unknown): FrameAnalysisManifest {
  if (typeof payload !== 'object' || payload === null) {
    throw new Error('the manifest must be a JSON object')
  }
  const candidate = payload as Partial<FrameAnalysisManifest>
  if (!Array.isArray(candidate.frames) || candidate.frames.length === 0) {
    throw new Error('the manifest contains no frames')
  }
  if (typeof candidate.schemaVersion !== 'string') {
    throw new Error('the manifest has no schemaVersion')
  }
  return candidate as FrameAnalysisManifest
}

/** `mm:ss.mmm` — the frame's position in the source, not a duration. */
export function formatTimestamp(timeMs: number): string {
  const totalSeconds = Math.floor(timeMs / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  const millis = timeMs % 1000
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}.${String(millis).padStart(3, '0')}`
}

/**
 * Cosines are shown to three decimals and signed, never as a percentage.
 * A null value is a real outcome (nothing to compare against), so it is
 * rendered as an em dash rather than silently coerced to zero.
 */
export function formatSimilarity(value: number | null): string {
  return value === null ? '—' : value.toFixed(3)
}

/** Position of a value in [-1, 1] as a 0-100 bar width. */
export function similarityBarPercent(value: number): number {
  return ((Math.max(-1, Math.min(1, value)) + 1) / 2) * 100
}

/** `1st`, `2nd`, `3rd`, … for the selector's pick order. */
export function ordinal(value: number): string {
  const suffixes: Record<number, string> = { 1: 'st', 2: 'nd', 3: 'rd' }
  const remainder = value % 100
  const suffix = remainder >= 11 && remainder <= 13 ? 'th' : (suffixes[value % 10] ?? 'th')
  return `${value}${suffix}`
}

export function rejectionLabel(reason: string): string {
  const spaced = reason.replace(/([A-Z])/g, ' $1').toLowerCase()
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
}
