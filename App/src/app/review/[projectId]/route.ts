export function GET(request: Request): Response {
  // The legacy URL lacks organisation and job identity. Never guess either
  // value or mount an editor against ambiguous ownership.
  return Response.redirect(new URL('/projects', request.url), 307)
}
