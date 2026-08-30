import { Outlet, Link, useLocation, useNavigate } from 'react-router-dom'
import { useEffect } from 'react'
import { SidebarProvider, SidebarInset, SidebarTrigger } from '@/components/ui/sidebar'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Plus, ChevronDown, LogOut } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { AppSidebar } from '@/components/app-sidebar'
import { useAppStore } from '@/store/appStore'
import { reconcileProjectsWithServer } from '@/lib/uploadApi'
import { reconcileCloudProjects } from '@/lib/cloudProjects'
import { isCloudMode } from '@/lib/cloudMode'
import { isDemoBuild, isStudyMode } from '@/lib/session'

const PAGE_TITLES: Record<string, string> = {
  '/dashboard':          'Home',
  '/dashboard/projects': 'Projects',
  '/dashboard/usage':    'Usage',
  '/dashboard/help':     'Help',
  '/dashboard/settings': 'Settings',
}

const WITH_NEW_PROJECT = ['/dashboard', '/dashboard/projects']

function getInitials(name: string) {
  return name.split(' ').map((w) => w[0]).filter(Boolean).slice(0, 2).join('').toUpperCase()
}

function UserButton() {
  const navigate = useNavigate()
  const user = useAppStore((s) => s.currentUser)
  const logout = useAppStore((s) => s.logout)
  if (!user) return null
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button className="flex shrink-0 items-center gap-2 rounded-md px-2 py-1.5 hover:bg-neutral-150 transition-colors outline-none">
          <Avatar className="h-6 w-6 rounded-full">
            <AvatarFallback className="rounded-full bg-brand-50 text-xs font-medium text-brand-500">
              {getInitials(user.name)}
            </AvatarFallback>
          </Avatar>
          <span className="hidden text-sm font-medium text-neutral-700 md:inline">{user.name.split(' ')[0]}</span>
          <ChevronDown size={12} className="hidden text-neutral-400 md:inline" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-52">
        <DropdownMenuLabel className="font-normal">
          <p className="text-sm font-medium text-neutral-900">{user.name}</p>
          <p className="text-xs text-neutral-500">{user.email}</p>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={() => { logout(); navigate('/login') }}>
          <LogOut className="mr-2 h-4 w-4" />
          Log out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

export default function DashboardLayout() {
  const { pathname } = useLocation()

  // On every dashboard visit, reconcile local projects against the server:
  // patches stale fields and recovers any projects missing from the local
  // store. Mode-scoped (G7): cloud mode uses ONLY the protected cloud list
  // (and only once token access exists); demo/study issue no request; the
  // legacy build keeps its existing reconcile.
  useEffect(() => {
    if (isDemoBuild() || isStudyMode()) return
    if (isCloudMode()) {
      reconcileCloudProjects()
      return
    }
    reconcileProjectsWithServer()
  }, [])

  return (
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset className="min-w-0">
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-neutral-200 bg-neutral-0 px-4 sm:px-6">
          <SidebarTrigger className="md:hidden -ml-1" />
          <h1 className="min-w-0 truncate text-base font-semibold text-neutral-900">
            {PAGE_TITLES[pathname] ?? ''}
          </h1>
          <div className="flex-1" />
          {WITH_NEW_PROJECT.includes(pathname) && (
            <Link to="/upload" className="shrink-0">
              <Button variant="default" size="sm" className="gap-1.5">
                <Plus size={14} />
                <span className="hidden md:inline">New project</span>
              </Button>
            </Link>
          )}
          <UserButton />
        </header>
        <Outlet />
      </SidebarInset>
    </SidebarProvider>
  )
}
