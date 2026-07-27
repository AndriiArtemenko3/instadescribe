import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { renderCaptionTemplate, type TemplateEntity } from './captionTemplate'

// The committed Sintel fixtures are the ground truth: rendering every
// caption_template with the fixture entities must reproduce the pipeline's own
// rendered `caption` byte-for-byte (that is what normalisation.py produced).
const FIXTURES = join(__dirname, '..', '..', '..', 'public', 'data', 'sintel-blender-cc')

interface RawScene {
  caption_template: string
  caption: string
}
interface RawEntity {
  id: string
  name: string
  first_mention_label: string
  pronoun: string
  user_renamed: boolean
}

const scenes = JSON.parse(readFileSync(join(FIXTURES, 'scenes.json'), 'utf8')) as RawScene[]
const entities = JSON.parse(readFileSync(join(FIXTURES, 'entities.json'), 'utf8')) as RawEntity[]

const byId = Object.fromEntries(entities.map((e) => [e.id, e as TemplateEntity]))

describe('renderCaptionTemplate — parity with the pipeline output', () => {
  it('reproduces every committed caption from its template', () => {
    for (const s of scenes) {
      expect(renderCaptionTemplate(s.caption_template, byId)).toBe(s.caption)
    }
  })
})

describe('renderCaptionTemplate — rename semantics (mirrors normalisation.py)', () => {
  it('uses the renamed value for {_first} only once user_renamed is set', () => {
    const renamed: Record<string, TemplateEntity> = {
      ...byId,
      char_1: { ...byId.char_1, name: 'Sintel', user_renamed: true },
    }
    const out = renderCaptionTemplate('{char_1_first} kneels. {char_1_subj} waits.', renamed)
    expect(out).toBe('Sintel kneels. she waits.')
  })

  it('keeps first_mention_label while user_renamed is false', () => {
    const out = renderCaptionTemplate('{char_1_first} kneels.', byId)
    expect(out).toBe('a young woman kneels.')
  })

  it('renders pronoun forms with capitalization variants', () => {
    const out = renderCaptionTemplate(
      '{char_2_subj_cap} flaps {char_2_poss} wings; {char_1_subj} calms {char_2_obj}.',
      byId,
    )
    expect(out).toBe('It flaps its wings; she calms it.')
  })

  it('leaves unknown tokens and unknown entities untouched', () => {
    expect(renderCaptionTemplate('{mystery_token} and {char_9_subj}', byId)).toBe(
      '{mystery_token} and {char_9_subj}',
    )
  })

  it('propagates a rename through every fixture scene deterministically', () => {
    const renamed: Record<string, TemplateEntity> = {
      ...byId,
      char_1: { ...byId.char_1, name: 'Sintel', user_renamed: true },
      char_2: { ...byId.char_2, name: 'Scales', user_renamed: true },
    }
    for (const s of scenes) {
      const out = renderCaptionTemplate(s.caption_template, renamed)
      // Every scene that referenced the old first-mention labels now uses names.
      expect(out).not.toContain('a young woman')
      expect(out).not.toContain('a dragon-like creature')
      if (s.caption.includes('a young woman')) expect(out).toContain('Sintel')
    }
  })
})
