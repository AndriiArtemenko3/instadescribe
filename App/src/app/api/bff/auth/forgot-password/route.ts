import { handleForgotPasswordPost } from '@/server/bff/handlers'
import { getBffDependencies } from '@/server/bff/runtime-dependencies'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

export function POST(request: Request) {
  return handleForgotPasswordPost(request, getBffDependencies())
}
