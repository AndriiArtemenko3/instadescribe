import {
  handleSessionDelete,
  handleSessionGet,
  handleSessionPost,
} from '@/server/bff/handlers'
import { getBffDependencies } from '@/server/bff/runtime-dependencies'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

export function GET(request: Request) {
  return handleSessionGet(request, getBffDependencies())
}

export function POST(request: Request) {
  return handleSessionPost(request, getBffDependencies())
}

export function DELETE(request: Request) {
  return handleSessionDelete(request, getBffDependencies())
}
