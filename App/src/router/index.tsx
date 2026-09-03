import { createBrowserRouter, Navigate, redirect, type LoaderFunctionArgs } from 'react-router-dom'
import { lazy, Suspense } from 'react'
import { AuthGuard, GuestGuard } from './guards'
import LoginPage from '@/features/auth/pages/LoginPage'
import RegisterPage from '@/features/auth/pages/RegisterPage'
import ForgotPasswordPage from '@/features/auth/pages/ForgotPasswordPage'
import DashboardLayout from '@/components/layout/DashboardLayout'
import HomePage from '@/features/dashboard/pages/HomePage'
import ProjectsPage from '@/features/dashboard/pages/ProjectsPage'
import EditorPage from '@/features/editor/pages/EditorPage'
import StudyIntro from '@/features/study/StudyIntro'
import TutorialsPage from '@/features/tutorials/TutorialsPage'
import { isStudyMode, isDemoBuild } from '@/lib/session'
import { getAvailableTutorial, tutorialProject } from '@/lib/tutorials'
import { useAppStore } from '@/store/appStore'

const UploadPage = lazy(() => import('@/features/upload/pages/UploadPage'))
const FrameSimilarityExplorer = lazy(
  () => import('@/features/frame-analysis/FrameSimilarityExplorer'),
)
const HelpPage = lazy(() => import('@/features/dashboard/pages/HelpPage'))
const SettingsPage = lazy(() => import('@/features/dashboard/pages/SettingsPage'))
const UsagePage = lazy(() => import('@/features/dashboard/pages/UsagePage'))

function PageFallback() { return <div className="p-6 text-sm text-neutral-400">Loading…</div> }

function loadPublicTutorial({ params }: LoaderFunctionArgs) {
  const tutorial = getAvailableTutorial(params.projectId ?? '')
  if (!tutorial) return redirect('/tutorials')

  // Replace any stale client metadata with the registry-owned fixture before
  // EditorPage can issue a query. Mutable browser state never chooses a public
  // data path or converts a real cloud project into a tutorial.
  const canonical = tutorialProject(tutorial)
  const current = useAppStore.getState().projects
  useAppStore.setState({
    projects: [canonical, ...current.filter((project) => project.id !== canonical.id)],
  })
  return null
}

export const router = createBrowserRouter([
  {
    path: '/',
    element: (
      <Navigate to={isDemoBuild() ? '/tutorials' : isStudyMode() ? '/study' : '/dashboard'} replace />
    ),
  },
  {
    path: '/study',
    element: <StudyIntro />,
  },
  {
    path: '/tutorials',
    element: <TutorialsPage />,
  },
  {
    // Prototype viewer for a locally generated frame analysis manifest. It
    // reads a static file, so it needs neither a session nor a database.
    path: '/frame-analysis',
    element: (
      <Suspense fallback={<PageFallback />}>
        <FrameSimilarityExplorer />
      </Suspense>
    ),
  },
  {
    path: '/tutorials/:projectId/editor',
    loader: loadPublicTutorial,
    element: <EditorPage staticFixture />,
  },
  {
    path: '/login',
    element: <GuestGuard><LoginPage /></GuestGuard>,
  },
  {
    path: '/register',
    element: <GuestGuard><RegisterPage /></GuestGuard>,
  },
  {
    path: '/forgot-password',
    element: <GuestGuard><ForgotPasswordPage /></GuestGuard>,
  },
  {
    path: '/dashboard',
    element: <AuthGuard><DashboardLayout /></AuthGuard>,
    children: [
      { index: true,      element: <HomePage /> },
      { path: 'projects', element: <ProjectsPage /> },
      { path: 'usage',    element: <Suspense fallback={<PageFallback />}><UsagePage /></Suspense> },
      { path: 'help',     element: <Suspense fallback={<PageFallback />}><HelpPage /></Suspense> },
      { path: 'settings', element: <Suspense fallback={<PageFallback />}><SettingsPage /></Suspense> },
    ],
  },
  {
    path: '/upload',
    element: <AuthGuard><Suspense fallback={<PageFallback />}><UploadPage /></Suspense></AuthGuard>,
  },
  {
    path: '/editor/:projectId',
    element: <AuthGuard><EditorPage /></AuthGuard>,
  },
])
