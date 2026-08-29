import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'
import { DEMO_USER } from '@/features/auth/constants'
import { PROJECTS } from '@/lib/projects'
import { deleteProjectOnServer, patchProjectOnServer } from '@/lib/uploadApi'
import { clearPortfolioToken } from '@/lib/portfolioToken'
import { clearAllCloudDrafts } from '@/lib/persistence'
import { isCloudSession } from '@/lib/cloudMode'
import { CloudApiError, patchCloudProject } from '@/lib/cloudApi'
import { fenceCloudProjectReconciliation } from '@/lib/cloudProjectReconciliationFence'
import { queryClient } from '@/lib/queryClient'
import type { User, Project } from '@/types'

interface AppState {
  currentUser: User | null
  isAuthenticated: boolean
  sidebarCollapsed: boolean
  isDemoMode: boolean
  projects: Project[]
  login: (email: string, password: string) => boolean
  logout: () => void
  addProject: (project: Project) => void
  updateProjectStatus: (id: string, status: Project['status']) => void
  updateProject: (id: string, patch: Partial<Project>) => void
  deleteProject: (id: string) => Promise<void>
  renameProject: (id: string, name: string) => Promise<void>
  toggleStar: (id: string) => Promise<void>
}

export function initialProjects(): Project[] {
  return isCloudSession() ? [] : PROJECTS
}

export const useAppStore = create<AppState>()(
  persist(
    (set, get) => ({
      currentUser: null,
      isAuthenticated: false,
      sidebarCollapsed: false,
      isDemoMode: true,
      projects: initialProjects(),
      login: (email, password) => {
        if (email === DEMO_USER.email && password === DEMO_USER.password) {
          set({
            currentUser: { email: DEMO_USER.email, name: DEMO_USER.name, tokenBalance: 1_000_000 },
            isAuthenticated: true,
          })
          return true
        }
        return false
      },
      logout: () => {
        clearPortfolioToken() // the token never outlives the session (G7)
        if (isCloudSession()) {
          // G7.1 B: no cached editor access may survive logout — clear every
          // TanStack query (manifests, signed URLs, artifact JSON), the
          // cloud project metadata, and the session-scoped scene drafts.
          queryClient.clear()
          clearAllCloudDrafts()
          set({ currentUser: null, isAuthenticated: false, projects: [] })
          return
        }
        set({ currentUser: null, isAuthenticated: false })
      },
      addProject: (project) => set((s) => ({ projects: [project, ...s.projects] })),
      updateProjectStatus: (id, status) =>
        set((s) => ({
          projects: s.projects.map((p) => (p.id === id ? { ...p, status } : p)),
        })),
      updateProject: (id, patch) =>
        set((s) => ({
          projects: s.projects.map((p) => {
            if (p.id !== id) return p
            const next = { ...p, ...patch }
            if (patch.projectVersion !== undefined) {
              next.projectVersion = Math.max(p.projectVersion ?? 0, patch.projectVersion)
            }
            return next
          }),
        })),
      deleteProject: async (id) => {
        // Cloud deletion is intentionally not part of this tranche. Never
        // fall through to the legacy filesystem route in a cloud session.
        if (isCloudSession()) throw new CloudApiError('validation')
        await deleteProjectOnServer(id)
        set((s) => ({ projects: s.projects.filter((p) => p.id !== id) }))
      },
      renameProject: async (id, name) => {
        const trimmed = name.trim()
        if (!trimmed) return
        if (isCloudSession()) {
          const project = get().projects.find((candidate) => candidate.id === id)
          if (!project || !project.projectVersion) throw new CloudApiError('service')
          const response = await patchCloudProject(id, {
            name: trimmed,
            expectedVersion: project.projectVersion,
          })
          const current = get().projects.find((candidate) => candidate.id === id)
          if (!current || response.version <= (current.projectVersion ?? 0)) return
          // A jobs-list request that began before this accepted N→N+1 write
          // must not overwrite the authoritative response with version N.
          fenceCloudProjectReconciliation()
          set((state) => ({
            projects: state.projects.map((candidate) => candidate.id === id ? {
              ...candidate,
              name: response.name,
              starred: response.starred,
              projectVersion: response.version,
            } : candidate),
          }))
          return
        }
        await patchProjectOnServer(id, { name: trimmed })
        set((s) => ({
          projects: s.projects.map((p) => (p.id === id ? { ...p, name: trimmed } : p)),
        }))
      },
      toggleStar: async (id) => {
        const project = get().projects.find((p) => p.id === id)
        if (!project) return
        const next = !project.starred
        if (isCloudSession()) {
          if (!project.projectVersion) throw new CloudApiError('service')
          const response = await patchCloudProject(id, {
            starred: next,
            expectedVersion: project.projectVersion,
          })
          const current = get().projects.find((candidate) => candidate.id === id)
          if (!current || response.version <= (current.projectVersion ?? 0)) return
          fenceCloudProjectReconciliation()
          set((state) => ({
            projects: state.projects.map((candidate) => candidate.id === id ? {
              ...candidate,
              name: response.name,
              starred: response.starred,
              projectVersion: response.version,
            } : candidate),
          }))
          return
        }
        await patchProjectOnServer(id, { starred: next })
        set((s) => ({
          projects: s.projects.map((p) => (p.id === id ? { ...p, starred: next } : p)),
        }))
      },
    }),
    {
      // Stable v0.1 key: Vite rollback and existing browser sessions must be
      // readable throughout the route-by-route beta cutover.
      name: 'instascribe-app',
      // G7.1 B: cloud-mode metadata is SESSION-scoped (a closed tab leaves
      // no usable cloud session; server reconciliation is the recovery
      // source). Legacy/demo/study keep durable localStorage persistence.
      storage: createJSONStorage(() => (isCloudSession() ? sessionStorage : localStorage)),
      partialize: (state) => ({
        currentUser: state.currentUser,
        isAuthenticated: state.isAuthenticated,
        sidebarCollapsed: state.sidebarCollapsed,
        isDemoMode: state.isDemoMode,
        projects: state.projects,
      }),
    }
  )
)
