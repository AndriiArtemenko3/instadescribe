import { handleCloudProxy } from '@/server/bff/cloud-proxy'
import { getBffDependencies } from '@/server/bff/runtime-dependencies'
import { readBffRuntimeConfiguration } from '@/server/bff/runtime-config'

export const runtime = 'nodejs'
export const dynamic = 'force-dynamic'

interface RouteContext {
  params: Promise<{ path: string[] }>
}

async function relay(request: Request, context: RouteContext) {
  const configuration = readBffRuntimeConfiguration()
  if (!configuration) {
    return new Response(JSON.stringify({
      error: { code: 'app_api_unavailable', message: 'The application API is temporarily unavailable.' },
    }), {
      status: 503,
      headers: {
        'Cache-Control': 'private, no-store',
        'Content-Type': 'application/json; charset=utf-8',
        'X-Content-Type-Options': 'nosniff',
      },
    })
  }
  const { path } = await context.params
  return handleCloudProxy(
    request,
    path,
    getBffDependencies(),
    configuration.appApiOrigin,
    configuration.browserAssertionSecret,
  )
}

export const GET = relay
export const POST = relay
export const PATCH = relay
