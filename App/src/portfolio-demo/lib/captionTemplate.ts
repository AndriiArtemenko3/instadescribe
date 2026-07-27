// Deterministic, local caption re-rendering on character rename.
//
// A faithful TypeScript mirror of modular_pipeline/normalisation.py
// (render_caption_template + get_first_reference + capitalize_first +
// PRONOUN_FORMS), so a rename in the demo propagates through scene captions
// with exactly the semantics the real pipeline applies server-side.
// Pinned by captionTemplate.test.ts against the committed fixtures.

export interface TemplateEntity {
  name?: string
  first_mention_label?: string
  pronoun?: string
  user_renamed?: boolean
}

const PRONOUN_FORMS: Record<string, { subj: string; obj: string; poss: string }> = {
  he: { subj: 'he', obj: 'him', poss: 'his' },
  she: { subj: 'she', obj: 'her', poss: 'her' },
  they: { subj: 'they', obj: 'them', poss: 'their' },
  it: { subj: 'it', obj: 'it', poss: 'its' },
}

function getPronounSet(pronoun: string | undefined) {
  return PRONOUN_FORMS[(pronoun || 'it').toLowerCase()] ?? PRONOUN_FORMS.it
}

export function capitalizeFirst(text: string): string {
  if (!text) return text
  return text[0].toUpperCase() + text.slice(1)
}

function getFirstReference(entity: TemplateEntity): string {
  if (entity.user_renamed && entity.name) return entity.name
  return entity.first_mention_label || entity.name || 'someone'
}

function getNameReference(entity: TemplateEntity): string {
  return entity.name || 'someone'
}

// Suffix order matters (subj_cap before subj, etc.) — mirrors KNOWN_FIELDS.
const KNOWN_FIELDS = ['subj_cap', 'obj_cap', 'poss_cap', 'first', 'name', 'subj', 'obj', 'poss']

export function renderCaptionTemplate(
  template: string,
  entitiesById: Record<string, TemplateEntity>,
): string {
  return template.replace(/\{([^{}]+)\}/g, (match, token: string) => {
    let entityId: string | null = null
    let field: string | null = null
    for (const f of KNOWN_FIELDS) {
      const suffix = '_' + f
      if (token.endsWith(suffix)) {
        entityId = token.slice(0, -suffix.length)
        field = f
        break
      }
    }
    if (entityId === null || field === null) return match

    const entity = entitiesById[entityId]
    if (!entity) return match

    const pronouns = getPronounSet(entity.pronoun)
    switch (field) {
      case 'first':
        return getFirstReference(entity)
      case 'name':
        return getNameReference(entity)
      case 'subj':
        return pronouns.subj
      case 'obj':
        return pronouns.obj
      case 'poss':
        return pronouns.poss
      case 'subj_cap':
        return capitalizeFirst(pronouns.subj)
      case 'obj_cap':
        return capitalizeFirst(pronouns.obj)
      case 'poss_cap':
        return capitalizeFirst(pronouns.poss)
      default:
        return match
    }
  })
}
