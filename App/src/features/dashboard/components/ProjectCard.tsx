import { useNavigate } from 'react-router-dom'
import { Film, Layers, Cpu, Coins, Clock, MoreHorizontal, ExternalLink, Star } from 'lucide-react'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'
import { isCloudMode } from '@/lib/cloudMode'
import type { Project } from '@/types'
import { formatDuration, formatTokens, formatDate, STATUS_STYLES, STATUS_LABELS } from '../utils/formatters'
import { CloudCompletionButton } from './CloudCompletionButton'

interface ProjectCardProps {
  project: Project
  onRename?: (id: string, name: string) => void
  onDelete?: (id: string) => void
  onToggleStar?: (id: string) => void
}

export function ProjectCard({ project, onRename, onDelete, onToggleStar }: ProjectCardProps) {
  const navigate = useNavigate()

  function handleCardClick() {
    if (project.status === 'confirmation_pending') return
    navigate(`/editor/${project.id}`)
  }

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
      className={cn(
        'group relative flex flex-col overflow-hidden rounded-lg border border-neutral-200 bg-neutral-0 shadow-card transition-all hover:shadow-modal hover:border-neutral-300',
        project.status === 'confirmation_pending' ? 'cursor-default' : 'cursor-pointer',
      )}
      onClick={handleCardClick}
    >
      {/* Thumbnail */}
      <div className="relative aspect-video bg-neutral-950 overflow-hidden">
        {project.posterPlaceholder && (
          <div
            aria-hidden
            className="absolute inset-0 scale-110"
            style={{
              backgroundImage: `url("data:image/webp;base64,${project.posterPlaceholder}")`,
              backgroundSize: 'cover',
              backgroundPosition: 'center',
              filter: 'blur(20px)',
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
          <Film size={32} className="absolute inset-0 m-auto text-neutral-700" />
        )}

        {/* Status badge — left */}
        <span className={cn('absolute left-2 top-2', STATUS_STYLES[project.status])}>
          {STATUS_LABELS[project.status]}
        </span>

        {/* Overflow menu — right, visible on hover */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              aria-label={`Project actions for ${project.name}`}
              className="absolute right-2 top-2 flex h-6 w-6 items-center justify-center rounded-md bg-neutral-0/80 text-neutral-500 opacity-0 transition-opacity group-hover:opacity-100 hover:bg-neutral-0"
              onClick={(e) => e.stopPropagation()}
            >
              <MoreHorizontal size={13} />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            align="end"
            className="w-44"
            onClick={(e) => e.stopPropagation()}
          >
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
            <DropdownMenuItem onClick={handleRename}>
              Rename
            </DropdownMenuItem>
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
      </div>

      {/* Body */}
      <div className="flex flex-1 flex-col gap-3 p-4">
        <div>
          <h3 className="flex items-center gap-1.5 text-sm font-medium text-neutral-900 leading-snug">
            {project.starred && (
              <Star size={12} className="shrink-0 fill-warning-400 text-warning-400" aria-label="Starred" />
            )}
            <span className="truncate">{project.name}</span>
          </h3>
          <p className="mt-0.5 text-xs text-neutral-400">{formatDate(project.createdAt)}</p>
        </div>

        <div className="grid grid-cols-2 gap-x-4 gap-y-1.5">
          <Stat icon={<Layers size={12} />} label="Scenes" value={project.sceneCount ?? '—'} />
          <Stat icon={<Clock size={12} />} label="Duration" value={formatDuration(project.durationSecs)} />
          <Stat icon={<Cpu size={12} />} label="Model" value={project.model ?? '—'} />
          <Stat icon={<Coins size={12} />} label="Tokens" value={formatTokens(project.tokensUsed)} />
        </div>

        {project.status === 'confirmation_pending' && (
          <div className="space-y-2 border-t border-neutral-150 pt-3">
            <p className="text-xs leading-relaxed text-neutral-500">
              Your video is uploaded. Confirm this same job when the processing slot is available.
            </p>
            <CloudCompletionButton project={project} />
          </div>
        )}
      </div>
    </article>
  )
}

function Stat({ icon, label, value }: { icon: React.ReactNode; label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center gap-1.5 min-w-0">
      <span className="shrink-0 text-neutral-400">{icon}</span>
      <span className="shrink-0 text-xs text-neutral-500">{label}:</span>
      <span className="truncate text-xs font-medium text-neutral-700">{value}</span>
    </div>
  )
}
