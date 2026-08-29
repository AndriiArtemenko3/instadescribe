import { useNavigate } from 'react-router-dom'
import { ArrowRight, Lock } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useAppStore } from '@/store/appStore'
import {
  CATEGORY_LABELS,
  CATEGORY_ORDER,
  getAvailableTutorial,
  LENGTH_LABELS,
  LENGTH_ORDER,
  tutorialProject,
  type Tutorial,
  TUTORIALS,
} from '@/lib/tutorials'

const DIFFICULTY_LABEL: Record<Tutorial['difficulty'], string> = {
  beginner: 'Beginner',
  intermediate: 'Intermediate',
  advanced: 'Advanced',
}

const orderedTutorials = (): Tutorial[] =>
  LENGTH_ORDER.flatMap((len) =>
    CATEGORY_ORDER.map((cat) => TUTORIALS.find((t) => t.lengthTier === len && t.category === cat)).filter(
      (t): t is Tutorial => Boolean(t),
    ),
  )

function TutorialCard({ tutorial }: { tutorial: Tutorial }) {
  const navigate = useNavigate()
  const projects = useAppStore((s) => s.projects)
  const addProject = useAppStore((s) => s.addProject)
  const availableTutorial = getAvailableTutorial(tutorial.id)
  const available = availableTutorial !== undefined

  function start() {
    if (!availableTutorial) return
    if (!projects.some((p) => p.id === tutorial.id)) {
      addProject(tutorialProject(availableTutorial))
    }
    navigate(`/tutorials/${tutorial.id}/editor`)
  }

  return (
    <button
      type="button"
      onClick={start}
      disabled={!available}
      className={cn(
        'flex h-full flex-col rounded-xl border bg-neutral-0 p-4 text-left transition-colors',
        available
          ? 'border-neutral-200 hover:border-brand-400 hover:shadow-sm'
          : 'cursor-not-allowed border-dashed border-neutral-200 opacity-70',
      )}
    >
      <div className="mb-2 flex items-center gap-2">
        <span className="rounded-full bg-neutral-100 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-neutral-500">
          {CATEGORY_LABELS[tutorial.category]}
        </span>
        <span className="rounded-full bg-neutral-100 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-neutral-500">
          {LENGTH_LABELS[tutorial.lengthTier]}
        </span>
      </div>

      <h3 className="text-sm font-semibold text-neutral-900">{tutorial.title}</h3>
      <p className="mt-1 text-xs leading-relaxed text-neutral-500">{tutorial.blurb}</p>

      <ul className="mt-3 space-y-1">
        {tutorial.learningGoals.map((g) => (
          <li key={g} className="flex items-start gap-1.5 text-[11px] text-neutral-600">
            <span className="mt-1 h-1 w-1 shrink-0 rounded-full bg-brand-400" />
            {g}
          </li>
        ))}
      </ul>

      <div className="mt-4 flex items-center justify-between pt-2">
        <span className="text-[11px] text-neutral-400">{DIFFICULTY_LABEL[tutorial.difficulty]}</span>
        {available ? (
          <span className="flex items-center gap-1 text-xs font-medium text-brand-500">
            Start <ArrowRight size={13} />
          </span>
        ) : (
          <span className="flex items-center gap-1 text-[11px] text-neutral-400">
            <Lock size={11} /> Coming soon
          </span>
        )}
      </div>
      {!available && tutorial.plannedClip && (
        <p className="mt-1 text-[10px] text-neutral-300">Planned: {tutorial.plannedClip}</p>
      )}
    </button>
  )
}

export default function TutorialsPage() {
  return (
    <div className="min-h-screen bg-neutral-50">
      <div className="mx-auto max-w-5xl px-5 py-10">
        <header className="mb-8">
          <div className="text-sm font-semibold tracking-tight text-neutral-900">InstaDescribe</div>
          <h1 className="mt-3 text-2xl font-semibold text-neutral-900">Try a tutorial</h1>
          <p className="mt-2 max-w-2xl text-sm leading-relaxed text-neutral-500">
            Audio description is spoken narration of what happens on screen — actions, settings, and
            expressions — fitted into the pauses between dialogue, so people who are blind or have low
            vision can follow along. Each tutorial hands you a short clip and walks you through
            reviewing and refining its description. Everything runs in your browser; no sign-up,
            nothing to install.
          </p>
        </header>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {orderedTutorials().map((t) => (
            <TutorialCard key={t.id} tutorial={t} />
          ))}
        </div>

        <p className="mt-8 text-center text-xs text-neutral-400">
          The quality score you see while editing is computed in your browser — no model call, no
          cost.
        </p>
      </div>
    </div>
  )
}
