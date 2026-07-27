import { describe, it, expect } from 'vitest'
import { sentenceCaseStart } from './text'

describe('sentenceCaseStart (display-layer casing cleanup)', () => {
  it('uppercases a lowercase draft start', () => {
    expect(sentenceCaseStart('a young woman sets down a stone bowl of water.')).toBe(
      'A young woman sets down a stone bowl of water.',
    )
  })

  it('uppercases after sentence boundaries only', () => {
    expect(sentenceCaseStart('suddenly, a chicken darts. a young woman pursues them.')).toBe(
      'Suddenly, a chicken darts. A young woman pursues them.',
    )
  })

  it('leaves already-correct text byte-identical', () => {
    const s = 'Beneath a blazing sky, a young woman stands. Her expression softens.'
    expect(sentenceCaseStart(s)).toBe(s)
  })

  it('never touches mid-sentence characters', () => {
    const s = 'she waits — a dragon-like creature, wounded. it stirs?  it rests.'
    expect(sentenceCaseStart(s)).toBe('She waits — a dragon-like creature, wounded. It stirs?  It rests.')
  })

  it('handles empty and punctuation-led strings', () => {
    expect(sentenceCaseStart('')).toBe('')
    expect(sentenceCaseStart('“oh.” she said')).toBe('“Oh.” She said')
  })
})
