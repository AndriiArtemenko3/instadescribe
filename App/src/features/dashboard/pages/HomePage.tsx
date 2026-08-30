import { Link } from 'react-router-dom'
import { Plus, ArrowRight } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ProjectCard } from '../components/ProjectCard'
import { useAppStore } from '@/store/appStore'
import { reportProjectMutationError } from '@/lib/projectMutationFeedback'

const RECENT_COUNT = 4

export default function HomePage() {
  const projects = useAppStore((s) => s.projects)
  const deleteProject = useAppStore((s) => s.deleteProject)
  const renameProject = useAppStore((s) => s.renameProject)
  const toggleStar = useAppStore((s) => s.toggleStar)
  const recent = projects.slice(0, RECENT_COUNT)

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
    <main className="flex flex-col gap-8 px-6 py-6">
      {recent.length > 0 && (
        <section className="flex flex-col gap-4">
          <div className="flex items-center justify-between">
            <h2 className="text-xs font-medium uppercase tracking-widest text-neutral-400">
              Recent
            </h2>
            <Link
              to="/dashboard/projects"
              className="flex items-center gap-1 text-xs text-neutral-400 hover:text-neutral-700 transition-colors"
            >
              View all <ArrowRight size={12} />
            </Link>
          </div>

          <div className="grid gap-4 grid-cols-[repeat(auto-fill,minmax(260px,1fr))]">
            {recent.map((project) => (
              <ProjectCard
                key={project.id}
                project={project}
                onDelete={handleDelete}
                onRename={handleRename}
                onToggleStar={handleToggleStar}
              />
            ))}
          </div>
        </section>
      )}

      {recent.length === 0 && (
        <div className="flex flex-col items-center justify-center gap-4 pt-16 text-center">
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
      )}
    </main>
  )
}
