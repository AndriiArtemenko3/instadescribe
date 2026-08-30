import { handleProjectsGet } from '@/server/bff/handlers'
import { getBffDependencies } from '@/server/bff/runtime-dependencies'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

export function GET(request: Request) {
  return handleProjectsGet(request, getBffDependencies())
}
