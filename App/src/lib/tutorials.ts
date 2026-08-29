// The guided-tutorial registry. Each cell of the 3x3 matrix (clip length x content
// category) is one entry — data, not code — so growing the grid is a new fixture
// folder plus an entry here, no component changes. The picker, the demo project
// seeding, and (later) the per-clip guided steps all derive from this list.

import type { Project } from '@/types'

export type LengthTier = 'short' | 'medium' | 'long'
export type Category = 'educational' | 'entertainment' | 'other'
export type Difficulty = 'beginner' | 'intermediate' | 'advanced'
export type TutorialStatus = 'available' | 'coming-soon'

export interface Tutorial {
  id: string
  title: string
  blurb: string
  lengthTier: LengthTier
  category: Category
  difficulty: Difficulty
  status: TutorialStatus
  learningGoals: string[]
  // Present once the clip's fixtures are baked (status === 'available'):
  durationSecs?: number
  sceneCount?: number
  videoFile?: string
  dataPath?: string
  posterUrl?: string
  posterAvifUrl?: string
  clipCredit?: string
  // For not-yet-built cells: the openly-licensed clip earmarked for it.
  plannedClip?: string
}

export const LENGTH_LABELS: Record<LengthTier, string> = {
  short: 'Short clip',
  medium: 'Medium clip',
  long: 'Long clip',
}

export const CATEGORY_LABELS: Record<Category, string> = {
  educational: 'Educational',
  entertainment: 'Entertainment',
  other: 'Other / non-typical',
}

export const LENGTH_ORDER: LengthTier[] = ['short', 'medium', 'long']
export const CATEGORY_ORDER: Category[] = ['educational', 'entertainment', 'other']

export const TUTORIALS: Tutorial[] = [
  // ── Educational ──────────────────────────────────────────────────────────
  {
    id: 'edu-short',
    title: 'First steps',
    blurb: 'Learn what audio description is and write one line into one quiet gap.',
    lengthTier: 'short',
    category: 'educational',
    difficulty: 'beginner',
    status: 'coming-soon',
    plannedClip: 'NASA explainer clip (public domain)',
    learningGoals: ['What audio description is', 'Activate a scene', 'Write to the gap'],
  },
  {
    id: 'edu-medium',
    title: 'Pacing a lesson',
    blurb: 'Keep coverage high across a longer explainer without overrunning the gaps.',
    lengthTier: 'medium',
    category: 'educational',
    difficulty: 'intermediate',
    status: 'coming-soon',
    plannedClip: 'TIB AV-Portal CC-BY science video',
    learningGoals: ['Coverage', 'Trim to the time budget', 'Reading speed'],
  },
  {
    id: 'edu-long',
    title: 'A full lesson',
    blurb: 'Produce a complete, exportable description track for a real explainer.',
    lengthTier: 'long',
    category: 'educational',
    difficulty: 'advanced',
    status: 'coming-soon',
    plannedClip: 'NASA long-form (public domain)',
    learningGoals: ['Drive the flag list to zero', 'Sustain all five dimensions', 'Export'],
  },

  // ── Entertainment ────────────────────────────────────────────────────────
  {
    id: 'ent-short',
    title: 'Edit a short film',
    blurb:
      'Spot where the narration talks over dialogue, then fix it and watch the quality score climb.',
    lengthTier: 'short',
    category: 'entertainment',
    difficulty: 'beginner',
    status: 'available',
    durationSecs: 120,
    sceneCount: 9,
    videoFile: '/videos/sintel-blender-cc.mp4',
    dataPath: '/data/sintel-blender-cc',
    posterUrl: '/data/sintel-blender-cc/poster.jpg',
    posterAvifUrl: '/data/sintel-blender-cc/poster.avif',
    clipCredit: 'Sintel — (CC) Blender Foundation · durian.blender.org (CC BY 3.0)',
    learningGoals: [
      'Read the dialogue-collision flag',
      'Edit a description line',
      'Watch the live quality score respond',
    ],
  },
  {
    id: 'ent-medium',
    title: 'Many short collisions',
    blurb: 'Triage a flag list across a dialogue-dense scene; decide Smart Fill vs a manual rewrite.',
    lengthTier: 'medium',
    category: 'entertainment',
    difficulty: 'intermediate',
    status: 'coming-soon',
    plannedClip: 'Sintel (Blender, CC-BY 3.0)',
    learningGoals: ['Work a flag list', 'Smart Fill at scale', 'Character consistency'],
  },
  {
    id: 'ent-long',
    title: 'The capstone',
    blurb: 'A dense, multi-character scene where every dimension is hostile at once. Drive it green.',
    lengthTier: 'long',
    category: 'entertainment',
    difficulty: 'advanced',
    status: 'coming-soon',
    plannedClip: 'Sintel (Blender, CC-BY 3.0)',
    learningGoals: ['Prioritise by weight', 'Pacing across 20+ scenes', 'A complete track'],
  },

  // ── Other / non-typical ──────────────────────────────────────────────────
  {
    id: 'other-short',
    title: 'Describe the abstract',
    blurb: 'Describe motion and mood when there are no people, no plot, and no dialogue to dodge.',
    lengthTier: 'short',
    category: 'other',
    difficulty: 'beginner',
    status: 'coming-soon',
    plannedClip: 'Pexels timelapse (no attribution required)',
    learningGoals: ['Describe what is visible', 'Avoid duplicate lines', 'Grounding'],
  },
  {
    id: 'other-medium',
    title: 'Mixed-mode content',
    blurb: 'Decide when to stay silent and let existing narration carry it, versus when to describe.',
    lengthTier: 'medium',
    category: 'other',
    difficulty: 'intermediate',
    status: 'coming-soon',
    plannedClip: 'Prelinger Archives (public domain)',
    learningGoals: ['When not to describe', 'Defend a lower coverage', 'Editorial judgment'],
  },
  {
    id: 'other-long',
    title: 'The edge case',
    blurb: 'Genuinely atypical content where the rules bend and a perfect score is the wrong goal.',
    lengthTier: 'long',
    category: 'other',
    difficulty: 'advanced',
    status: 'coming-soon',
    plannedClip: 'Wordless experimental / archival film (public domain)',
    learningGoals: ['A description philosophy', 'Consistency at length', 'Less is more'],
  },
]

export const getTutorial = (id: string): Tutorial | undefined =>
  TUTORIALS.find((t) => t.id === id)

export const getAvailableTutorial = (id: string): Tutorial | undefined => {
  const tutorial = getTutorial(id)
  return tutorial?.status === 'available' &&
    tutorial.durationSecs !== undefined &&
    tutorial.sceneCount !== undefined &&
    !!tutorial.videoFile &&
    !!tutorial.dataPath
    ? tutorial
    : undefined
}

export const isAvailableTutorialId = (id: string): boolean =>
  getAvailableTutorial(id) !== undefined

/** Build the browser-only project for one baked tutorial fixture. */
export function tutorialProject(tutorial: Tutorial): Project {
  if (
    tutorial.status !== 'available' ||
    tutorial.durationSecs === undefined ||
    tutorial.sceneCount === undefined ||
    !tutorial.videoFile ||
    !tutorial.dataPath
  ) {
    throw new Error(`Tutorial fixture is incomplete: ${tutorial.id}`)
  }

  return {
    id: tutorial.id,
    name: tutorial.title,
    status: 'ready',
    createdAt: new Date().toISOString(),
    durationSecs: tutorial.durationSecs,
    sceneCount: tutorial.sceneCount,
    videoFile: tutorial.videoFile,
    dataPath: tutorial.dataPath,
    posterUrl: tutorial.posterUrl,
    posterAvifUrl: tutorial.posterAvifUrl,
  }
}

export const tutorialAt = (length: LengthTier, category: Category): Tutorial | undefined =>
  TUTORIALS.find((t) => t.lengthTier === length && t.category === category)
