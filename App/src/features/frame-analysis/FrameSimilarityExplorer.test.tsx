// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { FrameSimilarityExplorer } from './FrameSimilarityExplorer'
import {
  formatSimilarity,
  formatTimestamp,
  ordinal,
  parseManifest,
  similarityBarPercent,
} from './manifest'

function frame(index: number, overrides: Record<string, unknown> = {}) {
  return {
    frameId: `frame-${String(index).padStart(3, '0')}`,
    index,
    timeMs: index * 7_059,
    shotIndex: index,
    image: `frame-${String(index).padStart(3, '0')}.jpg`,
    width: 564,
    height: 240,
    perceptualHash: 'a983deb9c187c62c',
    vectorMetrics: {
      clipCentroidSimilarity: 0.8 + index / 100,
      previousFrameSimilarity: index === 0 ? null : 0.6 + index / 100,
      nearestSelectedSimilarity: 0.77,
      semanticNovelty: 0.23,
      mostSimilarFrameId: 'frame-002',
    },
    qualityMetrics: {
      sharpness: 0.93,
      exposureQuality: 0.95,
      novelty: 1,
      ocrDensity: 0.83,
      motionStability: 0.75,
    },
    keyframe: {
      selected: index % 2 === 0,
      rank: index % 2 === 0 ? index / 2 : null,
      informationScore: index % 2 === 0 ? 0.93 : null,
      qualityScore: index % 2 === 0 ? 0.88 : null,
      selectionNearestSimilarity: null,
      selectionSemanticNovelty: index % 2 === 0 ? 1 : null,
      rejectionReason: index % 2 === 0 ? null : 'globalLimit',
      duplicateOf: null,
    },
    ...overrides,
  }
}

const MANIFEST = {
  schemaVersion: 'frame-analysis-demo-1',
  media: { name: 'fixture.mp4', durationSeconds: 120, sourceSha256: '0'.repeat(64) },
  embeddingModel: {
    name: 'clip-vision-onnx',
    runtime: 'onnxruntime-cpu',
    version: '1',
    digest: 'a'.repeat(64),
    dimension: 512,
  },
  selector: {
    version: 'ffmpeg-uniform+heuristic-v1+semantic-v1',
    semanticEnabled: true,
    candidateCount: 4,
    selectedCount: 2,
    maxKeyframes: 8,
    noveltyWeight: 0.3,
    similarityThreshold: null,
  },
  metricDefinitions: { clipCentroidSimilarity: 'Cosine similarity, not a confidence.' },
  frames: [frame(0), frame(1), frame(2), frame(3)],
}

let fetchCalls = 0

beforeEach(() => {
  fetchCalls = 0
  vi.stubGlobal('fetch', () => {
    fetchCalls += 1
    return Promise.resolve({ ok: true, json: () => Promise.resolve(MANIFEST) } as Response)
  })
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

async function mounted() {
  render(<FrameSimilarityExplorer />)
  await waitFor(() => expect(screen.getByText(/Frame similarity explorer/)).toBeTruthy())
  await waitFor(() => expect(screen.getByRole('button', { name: /Next/ })).toBeTruthy())
}

describe('formatting helpers', () => {
  it('renders a cosine as a signed decimal, never a percentage', () => {
    expect(formatSimilarity(0.846074)).toBe('0.846')
    expect(formatSimilarity(-0.12)).toBe('-0.120')
    expect(formatSimilarity(0.846074)).not.toContain('%')
  })

  it('renders an absent comparison as a dash rather than zero', () => {
    expect(formatSimilarity(null)).toBe('—')
  })

  it('maps the cosine range onto a bar position', () => {
    expect(similarityBarPercent(-1)).toBe(0)
    expect(similarityBarPercent(0)).toBe(50)
    expect(similarityBarPercent(1)).toBe(100)
    expect(similarityBarPercent(4)).toBe(100) // clamped, never off the scale
  })

  it('names the pick order with an ordinal', () => {
    expect([1, 2, 3, 4, 11, 12, 13, 21].map(ordinal)).toEqual([
      '1st',
      '2nd',
      '3rd',
      '4th',
      '11th',
      '12th',
      '13th',
      '21st',
    ])
  })

  it('formats a frame time as a position in the source', () => {
    expect(formatTimestamp(0)).toBe('00:00.000')
    expect(formatTimestamp(7_059)).toBe('00:07.059')
    expect(formatTimestamp(112_941)).toBe('01:52.941')
  })

  it('refuses a payload that is not a manifest', () => {
    expect(() => parseManifest(null)).toThrow(/JSON object/)
    expect(() => parseManifest({ frames: [] })).toThrow(/no frames/)
    expect(() => parseManifest({ frames: [frame(0)] })).toThrow(/schemaVersion/)
  })
})

describe('navigation', () => {
  it('starts on the first frame and shows its index and timestamp', async () => {
    await mounted()
    expect(screen.getByText(/of\s*4/)).toBeTruthy()
    expect(screen.getByText('00:00.000')).toBeTruthy()
  })

  it('moves forward and back with the buttons', async () => {
    await mounted()
    fireEvent.click(screen.getByRole('button', { name: /Next/ }))
    expect(screen.getByText('00:07.059')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Previous/ }))
    expect(screen.getByText('00:00.000')).toBeTruthy()
  })

  it('moves with the arrow keys', async () => {
    await mounted()
    fireEvent.keyDown(window, { key: 'ArrowRight' })
    expect(screen.getByText('00:07.059')).toBeTruthy()
    fireEvent.keyDown(window, { key: 'ArrowRight' })
    expect(screen.getByText('00:14.118')).toBeTruthy()
    fireEvent.keyDown(window, { key: 'ArrowLeft' })
    expect(screen.getByText('00:07.059')).toBeTruthy()
  })

  it('stops at both ends instead of wrapping', async () => {
    await mounted()
    expect(screen.getByRole('button', { name: /Previous/ }).hasAttribute('disabled')).toBe(true)
    for (let step = 0; step < 6; step += 1) fireEvent.keyDown(window, { key: 'ArrowRight' })
    expect(screen.getByText('00:21.177')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Next/ }).hasAttribute('disabled')).toBe(true)
  })

  it('never refetches the manifest while navigating', async () => {
    await mounted()
    expect(fetchCalls).toBe(1)
    fireEvent.click(screen.getByRole('button', { name: /Next/ }))
    fireEvent.keyDown(window, { key: 'ArrowRight' })
    fireEvent.keyDown(window, { key: 'ArrowLeft' })
    expect(fetchCalls).toBe(1)
  })

  it('jumps to a frame from the timeline', async () => {
    await mounted()
    const timeline = screen.getByRole('list', { name: /Frame timeline/ })
    fireEvent.click(within(timeline).getByRole('listitem', { name: /Frame 3 at/ }))
    expect(screen.getByText('00:14.118')).toBeTruthy()
  })
})

describe('metric presentation', () => {
  it('separates the vector metrics from the quality metrics', async () => {
    await mounted()
    const semantic = screen.getByText(/Semantic \/ vector metrics/).closest('section')!
    const quality = screen.getByText(/Frame quality metrics/).closest('section')!

    expect(within(semantic).getByText('CLIP centroid similarity')).toBeTruthy()
    expect(within(semantic).getByText('Semantic novelty')).toBeTruthy()
    expect(within(quality).getByText('Sharpness')).toBeTruthy()
    expect(within(quality).getByText('Information score')).toBeTruthy()
    // The two groups must not merge: a quality feature never appears in the
    // vector panel, and vice versa.
    expect(within(semantic).queryByText('Sharpness')).toBeNull()
    expect(within(quality).queryByText('CLIP centroid similarity')).toBeNull()
  })

  it('mentions confidence and probability only to disclaim them', async () => {
    await mounted()
    const text = document.body.textContent ?? ''
    // The disclaimer "not a confidence, probability or accuracy" is the only
    // permitted appearance; a metric must never be labelled with one.
    const disclaimer = /not a confidence, probability or accuracy/i
    for (const word of ['confidence', 'probability', 'accuracy', 'certainty']) {
      const occurrences = text.toLowerCase().split(word).length - 1
      const allowed = disclaimer.test(text) && word !== 'certainty' ? 1 : 0
      expect(occurrences).toBe(allowed)
    }
  })

  it('never renders a similarity as a percentage', async () => {
    await mounted()
    const semantic = screen.getByText(/Semantic \/ vector metrics/).closest('section')!
    expect(semantic.textContent).not.toMatch(/\d%/)
  })

  it('shows a null previous-frame similarity as a dash on the first frame', async () => {
    await mounted()
    const semantic = screen.getByText(/Semantic \/ vector metrics/).closest('section')!
    const row = within(semantic).getByText('Previous frame similarity').closest('div')!
    expect(row.textContent).toContain('—')
    expect(row.textContent).not.toContain('0.000')
  })

  it('reports the real selection outcome for each frame', async () => {
    await mounted()
    expect(screen.getByText(/Selected · picked 1st/)).toBeTruthy()
    fireEvent.keyDown(window, { key: 'ArrowRight' })
    expect(screen.getByText(/Not selected · Global limit/)).toBeTruthy()
  })
})

describe('missing manifest', () => {
  it('explains how to generate one instead of failing blank', async () => {
    vi.stubGlobal('fetch', () =>
      Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) } as Response),
    )
    render(<FrameSimilarityExplorer />)
    await waitFor(() => expect(screen.getByText(/frame_analysis_demo.py/)).toBeTruthy())
    expect(screen.getByText(/no manifest at/)).toBeTruthy()
  })
})
