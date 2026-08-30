import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Plus, LayoutGrid, List, Film, Layers, Cpu, Coins, Clock, MoreHorizontal, ExternalLink, Star } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'
import { isCloudMode } from '@/lib/cloudMode'
import { ProjectCard } from '../components/ProjectCard'
import { CloudCompletionButton } from '../components/CloudCompletionButton'
import { formatDuration, formatTokens, formatDate, STATUS_STYLES, STATUS_LABELS } from '../utils/formatters'
import { useAppStore } from '@/store/appStore'
import { reportProjectMutationError } from '@/lib/projectMutationFeedback'
import type { Project } from '@/types'

// ─── List-view row ────────────────────────────────────────────────────────────

interface ProjectRowProps {
  project: Project
  onRename?: (id: string, name: string) => void
  onDelete?: (id: string) => void
  onToggleStar?: (id: string) => void
}

function ProjectRow({ project, onRename, onDelete, onToggleStar }: ProjectRowProps) {
  const navigate = useNavigate()

  function handleRename(e: React.MouseEvent) {
    e.stopPropagation()
    const newName = window.prompt('Rename project:', project.name)
    if (newName && newName.trim() && newName.trim() !== project.name) {
      onRename?.(project.id, newName.trim())
    }
  }

  function handleDelete(e: React.MouseEvent) {
    e.stopPropagation()
    if (window.confirm(`Delete "${project.name}"? This cannot be undone.`)) {
      onDelete?.(project.id)
    }
  }

  return (
    <article
      className="group flex items-center gap-3 sm:gap-4 rounded-lg border border-neutral-200 bg-neutral-0 px-3 sm:px-4 py-3 cursor-pointer transition-all hover:shadow-card hover:border-neutral-300"
      onClick={() => {
        if (project.status !== 'confirmation_pending') navigate(`/editor/${project.id}`)
      }}
    >
      {/* Thumbnail */}
      <div className="relative h-9 w-16 shrink-0 overflow-hidden rounded bg-neutral-950">
        {project.posterPlaceholder && (
          <div
            aria-hidden
            className="absolute inset-0 scale-110"
            style={{
              backgroundImage: `url("data:image/webp;base64,${project.posterPlaceholder}")`,
              backgroundSize: 'cover',
              backgroundPosition: 'center',
              filter: 'blur(8px)',
            }}
          />
        )}
        {project.posterUrl ? (
          <picture>
            {project.posterAvifUrl && (
              <source srcSet={project.posterAvifUrl} type="image/avif" />
            )}
            <img
              src={project.posterUrl}
              width={520}
              height={292}
              className="relative h-full w-full object-cover"
              alt=""
              decoding="sync"
              loading="eager"
              fetchPriority="high"
            />
          </picture>
        ) : project.videoFile ? (
          <video
            src={project.videoFile}
            className="relative h-full w-full object-cover opacity-80"
            muted
            preload="metadata"
          />
        ) : (
          <Film size={14} className="absolute inset-0 m-auto text-neutral-700" />
        )}
      </div>

      {/* Name + date */}
      <div className="flex min-w-0 flex-1 flex-col">
        <p className="flex items-center gap-1.5 text-sm font-medium text-neutral-900">
          {project.starred && (
            <Star size={12} className="shrink-0 fill-warning-400 text-warning-400" aria-label="Starred" />
          )}
          <span className="truncate">{project.name}</span>
        </p>
        <p className="truncate text-xs text-neutral-400">{formatDate(project.createdAt)}</p>
      </div>

      {/* Status badge — always visible; fixed slot at md+ so stats column lines up across rows */}
      <div className="flex shrink-0 justify-start md:w-24">
        <span className={STATUS_STYLES[project.status]}>
          {STATUS_LABELS[project.status]}
        </span>
      </div>

      <CloudCompletionButton project={project} compact />

      {/* Stats — progressive disclosure: scenes+duration at md, +model+tokens at lg */}
      <div className="hidden md:flex items-center shrink-0 text-xs text-neutral-500">
        <span className="flex w-14 items-center gap-1"><Layers size={11} /> {project.sceneCount ?? '—'}</span>
        <span className="flex w-16 items-center gap-1"><Clock  size={11} /> {formatDuration(project.durationSecs)}</span>
        <span className="hidden lg:flex w-20 items-center gap-1"><Cpu size={11} /> {project.model ?? '—'}</span>
        <span className="hidden lg:flex w-16 items-center gap-1"><Coins size={11} /> {formatTokens(project.tokensUsed)}</span>
      </div>

      {/* Overflow menu */}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            aria-label={`Project actions for ${project.name}`}
            className="flex h-7 w-7 items-center justify-center rounded-md text-neutral-400 opacity-0 transition-opacity group-hover:opacity-100 hover:bg-neutral-100"
            onClick={(e) => e.stopPropagation()}
          >
            <MoreHorizontal size={14} />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-44" onClick={(e) => e.stopPropagation()}>
          {project.status === 'confirmation_pending' ? (
            <DropdownMenuItem disabled>
              <ExternalLink className="mr-2 h-4 w-4" />
              Editor available after confirmation
            </DropdownMenuItem>
          ) : (
            <DropdownMenuItem onClick={(e) => { e.stopPropagation(); navigate(`/editor/${project.id}`) }}>
              <ExternalLink className="mr-2 h-4 w-4" />
              Open editor
            </DropdownMenuItem>
          )}
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={(e) => { e.stopPropagation(); onToggleStar?.(project.id) }}>
            <Star className={cn('mr-2 h-4 w-4', project.starred && 'fill-current')} />
            {project.starred ? 'Unstar' : 'Star'}
          </DropdownMenuItem>
          <DropdownMenuItem onClick={handleRename}>Rename</DropdownMenuItem>
          {isCloudMode() ? (
            <DropdownMenuItem disabled className="text-neutral-400">
              Delete — not available yet
            </DropdownMenuItem>
          ) : (
              <DropdownMenuItem
                className="text-danger-400 focus:text-danger-400"
                onClick={handleDelete}
              >
                Delete
              </DropdownMenuItem>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
    </article>
  )
}

// ─── Empty state ──────────────────────────────────────────────────────────────

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center gap-4 pt-24 text-center">
      <div className="rounded-full bg-neutral-100 p-4">
        <Plus size={24} className="text-neutral-400" />
      </div>
      <div>
        <p className="text-sm font-medium text-neutral-700">No projects yet</p>
        <p className="mt-1 text-xs text-neutral-400">Process a video to create your first project</p>
      </div>
      <Link to="/upload">
        <Button variant="default" size="sm">Process a video</Button>
      </Link>
    </div>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

type ViewMode = 'grid' | 'list'

export default function ProjectsPage() {
  const projects = useAppStore((s) => s.projects)
  const deleteProject = useAppStore((s) => s.deleteProject)
  const renameProject = useAppStore((s) => s.renameProject)
  const toggleStar = useAppStore((s) => s.toggleStar)
  const [view, setView] = useState<ViewMode>(() =>
    (localStorage.getItem('projects-view') as ViewMode) ?? 'grid'
  )

  function changeView(v: ViewMode) {
    setView(v)
    localStorage.setItem('projects-view', v)
  }

  function handleDelete(id: string) {
    deleteProject(id).catch((e: Error) => window.alert(`Could not delete: ${e.message}`))
  }
  function handleRename(id: string, name: string) {
    renameProject(id, name).catch((error: unknown) => reportProjectMutationError('rename', error))
  }
  function handleToggleStar(id: string) {
    toggleStar(id).catch((error: unknown) => reportProjectMutationError('update star', error))
  }

  return (
    <main className="flex flex-col h-full">
      <div className="flex-1 overflow-y-auto px-4 sm:px-6 py-6">
        {projects.length === 0 ? (
          <EmptyState />
        ) : (
          <>
            {/* View toggle */}
            <div className="mb-4 flex justify-end gap-1">
              <button
                onClick={() => changeView('grid')}
                className={cn(
                  'flex h-7 w-7 items-center justify-center rounded-md transition-colors',
                  view === 'grid' ? 'bg-neutral-150 text-neutral-900' : 'text-neutral-400 hover:text-neutral-600',
                )}
                title="Grid view"
              >
                <LayoutGrid size={15} />
              </button>
              <button
                onClick={() => changeView('list')}
                className={cn(
                  'flex h-7 w-7 items-center justify-center rounded-md transition-colors',
                  view === 'list' ? 'bg-neutral-150 text-neutral-900' : 'text-neutral-400 hover:text-neutral-600',
                )}
                title="List view"
              >
                <List size={15} />
              </button>
            </div>

            {view === 'grid' ? (
              <div className="grid gap-4 grid-cols-[repeat(auto-fill,minmax(260px,1fr))]">
                {projects.map((project) => (
                  <ProjectCard
                    key={project.id}
                    project={project}
                    onDelete={handleDelete}
                    onRename={handleRename}
                    onToggleStar={handleToggleStar}
                  />
                ))}
              </div>
            ) : (
              <div className="flex flex-col gap-2">
                {projects.map((project) => (
                  <ProjectRow
                    key={project.id}
                    project={project}
                    onDelete={handleDelete}
                    onRename={handleRename}
                    onToggleStar={handleToggleStar}
                  />
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </main>
  )
}
