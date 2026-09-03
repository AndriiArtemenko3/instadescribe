/**
 * A prototype viewer for one frame analysis manifest.
 *
 * The manifest is fetched once. Moving between frames is local state only —
 * no refetch, no re-analysis — so every number on screen stays the one the
 * pipeline actually computed for that frame.
 *
 * Semantic and quality metrics are shown in separate panels because they are
 * different kinds of measurement: the first group is cosine similarity between
 * embeddings, the second is the selector's scalar frame features. Similarities
 * are rendered as signed values in [-1, 1] and never as percentages, and no
 * label here calls one a confidence, probability or accuracy.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { cn } from '@/lib/utils'
import {
  FRAME_IMAGE_BASE,
  MANIFEST_URL,
  formatSimilarity,
  formatTimestamp,
  ordinal,
  parseManifest,
  rejectionLabel,
  similarityBarPercent,
  type FrameAnalysisManifest,
  type ManifestFrame,
} from './manifest'

const GENERATE_COMMAND = [
  'python services/worker/scripts/frame_analysis_demo.py \\',
  '    --media App/public/videos/sintel-blender-cc.mp4 \\',
  '    --model /path/to/clip-vit-base-patch32/onnx/vision_model.onnx \\',
  '    --output App/public/frame-analysis/manifest.json \\',
  '    --frames-dir App/public/frame-analysis/frames',
].join('\n')

function Panel({
  title,
  subtitle,
  tone,
  children,
}: {
  title: string
  subtitle: string
  tone: 'semantic' | 'quality'
  children: React.ReactNode
}) {
  return (
    <section
      className={cn(
        'rounded-xl border p-4',
        tone === 'semantic' ? 'border-brand-200 bg-brand-50/40' : 'border-neutral-200 bg-neutral-0',
      )}
    >
      <h2 className="text-xs font-semibold uppercase tracking-wider text-neutral-700">{title}</h2>
      <p className="mt-1 text-[11px] leading-relaxed text-neutral-500">{subtitle}</p>
      {tone === 'semantic' && (
        // Name the axis once, so every marker below is read as a coordinate.
        <div className="mt-3 flex justify-between font-mono text-[10px] text-neutral-400">
          <span>-1</span>
          <span>0</span>
          <span>+1</span>
        </div>
      )}
      <dl className="mt-3 space-y-3">{children}</dl>
    </section>
  )
}

function SimilarityRow({
  label,
  value,
  hint,
}: {
  label: string
  value: number | null
  hint?: string
}) {
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <dt className="text-xs text-neutral-600">{label}</dt>
        <dd className="font-mono text-sm tabular-nums text-neutral-900">{formatSimilarity(value)}</dd>
      </div>
      {/*
        A coordinate on the [-1, 1] cosine axis, not a filled proportion. The
        zero gridline is what keeps it from reading as a progress bar: without
        it a marker near the right end looks like "almost full".
      */}
      <div className="relative mt-1.5 h-2.5" aria-hidden>
        <div className="absolute inset-x-0 top-1/2 h-px -translate-y-1/2 bg-neutral-200" />
        <div className="absolute left-1/2 top-0 h-full w-px bg-neutral-300" />
        {value !== null && (
          <div
            className="absolute top-1/2 h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-brand-500 ring-2 ring-brand-50"
            style={{ left: `${similarityBarPercent(value)}%` }}
          />
        )}
      </div>
      {hint && <p className="mt-1 text-[11px] text-neutral-400">{hint}</p>}
    </div>
  )
}

function ScalarRow({ label, value }: { label: string; value: number | null }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-xs text-neutral-600">{label}</dt>
      <dd className="font-mono text-sm tabular-nums text-neutral-900">
        {value === null ? '—' : value.toFixed(3)}
      </dd>
    </div>
  )
}

function EmptyState({ message }: { message: string }) {
  return (
    <div className="mx-auto max-w-2xl p-6">
      <h1 className="text-lg font-semibold text-neutral-900">Frame similarity explorer</h1>
      <p className="mt-2 text-sm text-neutral-600">{message}</p>
      <p className="mt-4 text-sm text-neutral-600">
        Generate a manifest from local media, then reload this page:
      </p>
      <pre className="mt-2 overflow-x-auto rounded-lg bg-neutral-900 p-3 text-[11px] leading-relaxed text-neutral-100">
        {GENERATE_COMMAND}
      </pre>
      <p className="mt-3 text-[11px] text-neutral-400">
        The manifest and its frame images are generated output and are not committed to the
        repository.
      </p>
    </div>
  )
}

export function FrameSimilarityExplorer() {
  const [manifest, setManifest] = useState<FrameAnalysisManifest | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [index, setIndex] = useState(0)
  const request = useRef<Promise<FrameAnalysisManifest> | null>(null)

  useEffect(() => {
    // The request is memoized rather than guarded by an "already fetched" flag, so
    // React's development double-effect subscribes to the same in-flight
    // promise instead of cancelling it and leaving the view loading forever.
    // Either way the manifest is read exactly once: navigation must never
    // re-read it, let alone re-run an analysis.
    request.current ??= fetch(MANIFEST_URL)
      .then((response) => {
        if (!response.ok) throw new Error(`no manifest at ${MANIFEST_URL} (${response.status})`)
        return response.json()
      })
      .then(parseManifest)

    let active = true
    request.current
      .then((loaded) => {
        if (active) setManifest(loaded)
      })
      .catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : String(cause))
      })
    return () => {
      active = false
    }
  }, [])

  const frames: ManifestFrame[] = useMemo(() => manifest?.frames ?? [], [manifest])
  const step = useCallback(
    (delta: number) => {
      setIndex((current) => Math.min(Math.max(current + delta, 0), Math.max(frames.length - 1, 0)))
    },
    [frames.length],
  )

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'ArrowLeft') step(-1)
      else if (event.key === 'ArrowRight') step(1)
      else return
      event.preventDefault()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [step])

  if (error) return <EmptyState message={error} />
  if (!manifest) return <div className="p-6 text-sm text-neutral-400">Loading manifest…</div>

  const frame = frames[Math.min(index, frames.length - 1)]
  const vector = frame.vectorMetrics
  const quality = frame.qualityMetrics
  const outcome = frame.keyframe

  return (
    <div className="mx-auto max-w-5xl p-6">
      <header className="mb-5">
        <h1 className="text-lg font-semibold text-neutral-900">Frame similarity explorer</h1>
        <p className="mt-1 text-xs text-neutral-500">
          {manifest.media.name} · {manifest.selector.candidateCount} candidate frames ·{' '}
          {manifest.selector.selectedCount} selected · {manifest.embeddingModel.name} (
          {manifest.embeddingModel.dimension}-d)
        </p>
      </header>

      <div className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-2">
        <button
          type="button"
          onClick={() => step(-1)}
          disabled={index === 0}
          className="flex items-center gap-1 rounded-lg border border-neutral-200 px-3 py-1.5 text-xs font-medium text-neutral-700 transition-colors hover:border-brand-400 disabled:cursor-not-allowed disabled:opacity-40"
        >
          <ChevronLeft className="h-3.5 w-3.5" /> Previous
        </button>
        <button
          type="button"
          onClick={() => step(1)}
          disabled={index >= frames.length - 1}
          className="flex items-center gap-1 rounded-lg border border-neutral-200 px-3 py-1.5 text-xs font-medium text-neutral-700 transition-colors hover:border-brand-400 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Next <ChevronRight className="h-3.5 w-3.5" />
        </button>
        <span className="text-xs text-neutral-500">
          Frame <span className="font-mono text-neutral-900">{frame.index + 1}</span> of{' '}
          {frames.length} · <span className="font-mono">{formatTimestamp(frame.timeMs)}</span>
        </span>
        <span className="ml-auto whitespace-nowrap text-[11px] text-neutral-400">
          ← → to move between frames
        </span>
      </div>

      <div className="grid gap-4 md:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <div>
          <div className="overflow-hidden rounded-xl border border-neutral-200 bg-neutral-100">
            {frame.image ? (
              <img
                src={`${FRAME_IMAGE_BASE}${frame.image}`}
                alt={`Frame ${frame.index + 1} at ${formatTimestamp(frame.timeMs)}`}
                width={frame.width}
                height={frame.height}
                className="w-full object-contain"
              />
            ) : (
              <div className="flex aspect-video items-center justify-center text-xs text-neutral-400">
                No image exported for this frame
              </div>
            )}
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px] text-neutral-500">
            <span className="font-mono text-neutral-700">{frame.frameId}</span>
            <span>shot {frame.shotIndex}</span>
            {outcome.selected ? (
              <span
                className="rounded-full bg-brand-100 px-2 py-0.5 font-medium text-brand-600"
                title="Rank is the order the selector accepted this frame, not a quality ranking"
              >
                Selected · picked {ordinal((outcome.rank ?? 0) + 1)}
              </span>
            ) : (
              <span className="rounded-full bg-neutral-100 px-2 py-0.5 text-neutral-500">
                Not selected
                {outcome.rejectionReason ? ` · ${rejectionLabel(outcome.rejectionReason)}` : ''}
              </span>
            )}
          </div>

          {/* Timeline strip: every candidate frame in time order. */}
          <div className="mt-4 flex gap-0.5" role="list" aria-label="Frame timeline">
            {frames.map((item, position) => (
              <button
                key={item.frameId}
                type="button"
                role="listitem"
                aria-label={`Frame ${position + 1} at ${formatTimestamp(item.timeMs)}`}
                aria-current={position === index}
                onClick={() => setIndex(position)}
                className={cn(
                  'h-8 flex-1 rounded-sm transition-colors',
                  position === index
                    ? 'bg-brand-500'
                    : item.keyframe.selected
                      ? 'bg-brand-200 hover:bg-brand-400'
                      : 'bg-neutral-200 hover:bg-neutral-300',
                )}
              />
            ))}
          </div>
          <p className="mt-1 text-[11px] text-neutral-400">
            Timeline in time order; the lighter marks are frames the selector kept. Pick order is
            the sequence the selector accepted them in, not a quality ranking.
          </p>

          {/* Provenance, so a reading of these numbers can be traced back to
              the run that produced them. */}
          <dl className="mt-4 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 border-t border-neutral-200 pt-3 text-[11px]">
            <dt className="text-neutral-500">Selector</dt>
            <dd className="truncate font-mono text-neutral-700">{manifest.selector.version}</dd>
            <dt className="text-neutral-500">Embedding model</dt>
            <dd className="truncate font-mono text-neutral-700">
              {manifest.embeddingModel.name} · {manifest.embeddingModel.runtime} ·{' '}
              {manifest.embeddingModel.dimension}-d
            </dd>
            <dt className="text-neutral-500">Model digest</dt>
            <dd className="truncate font-mono text-neutral-700">
              {manifest.embeddingModel.digest.slice(0, 16)}…
            </dd>
            <dt className="text-neutral-500">Frame hash</dt>
            <dd className="truncate font-mono text-neutral-700">{frame.perceptualHash ?? '—'}</dd>
            <dt className="text-neutral-500">Novelty weight</dt>
            <dd className="font-mono text-neutral-700">{manifest.selector.noveltyWeight}</dd>
          </dl>
        </div>

        <div className="space-y-4">
          <Panel
            title="Semantic / vector metrics"
            subtitle="Cosine similarity between CLIP embeddings, from -1 to 1. A measure of how closely two frames point the same way — not a confidence, probability or accuracy."
            tone="semantic"
          >
            <SimilarityRow
              label="CLIP centroid similarity"
              value={vector.clipCentroidSimilarity}
              hint="Against the mean direction of every frame in this video"
            />
            <SimilarityRow
              label="Previous frame similarity"
              value={vector.previousFrameSimilarity}
              hint={frame.index === 0 ? 'The first frame has no predecessor' : undefined}
            />
            <SimilarityRow
              label="Nearest selected similarity"
              value={vector.nearestSelectedSimilarity}
              hint={
                vector.mostSimilarFrameId ? `Closest to ${vector.mostSimilarFrameId}` : undefined
              }
            />
            <SimilarityRow
              label="Semantic novelty"
              value={vector.semanticNovelty}
              hint="1 minus the nearest selected similarity, clamped to [0, 1]"
            />
          </Panel>

          <Panel
            title="Frame quality metrics"
            subtitle="The selector's scalar frame features and its scoring outcome. These are separate measurements from the vector metrics above."
            tone="quality"
          >
            <ScalarRow label="Sharpness" value={quality.sharpness} />
            <ScalarRow label="Exposure quality" value={quality.exposureQuality} />
            <ScalarRow label="Novelty" value={quality.novelty} />
            <ScalarRow label="OCR density" value={quality.ocrDensity} />
            <ScalarRow label="Motion stability" value={quality.motionStability} />
            <div className="border-t border-neutral-200 pt-3">
              <ScalarRow label="Information score" value={outcome.informationScore} />
              <ScalarRow label="Quality score" value={outcome.qualityScore} />
            </div>
          </Panel>
        </div>
      </div>
    </div>
  )
}

export default FrameSimilarityExplorer
