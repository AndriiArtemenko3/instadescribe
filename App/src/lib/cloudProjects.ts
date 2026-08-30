// Cloud dashboard reconciliation (G7 Gate B2).
//
// GET /api/v1/jobs returns a map keyed by JOB id whose entries each carry the
// distinct durable projectId. Reconciliation:
//   - identifies existing store projects by entry.projectId (never the map key);
//   - retains the map-key jobId on the stored project;
//   - replaces cloud metadata from a successful authoritative snapshot;
//   - hides never-uploaded AWAITING_UPLOAD reservations while retaining an
//     explicitly source-verified/completion-pending job for same-job retry;
//   - uses the server created_at when present;
//   - is coalesced so React StrictMode double-mounts stay idempotent.
// Only durable identifiers and ordinary metadata are stored — never upload
// fields, presigned URLs, manifest bodies, tokens, or signed query strings.

import { useAppStore } from '@/store/appStore'
import { listCloudJobs, type CloudJobStatus } from './cloudApi'
import {
  getPortfolioSessionIdentity,
  isCurrentPortfolioSession,
  type PortfolioSessionIdentity,
} from './portfolioToken'
import {
  advanceReconciliationFence,
  reconciliationFence,
} from './cloudProjectReconciliationFence'
import type { Project } from '@/types'

export { fenceCloudProjectReconciliation } from './cloudProjectReconciliationFence'

const inFlightBySession = new Map<PortfolioSessionIdentity, Promise<boolean>>()

function toStatus(entry: CloudJobStatus, locallyUploaded: boolean): Project['status'] | null {
  switch (entry.canonicalState) {
    case 'UPLOAD_COMPLETE':
    case 'QUEUED':
    case 'PROCESSING':
    case 'EXPORT_QUEUED':
    case 'EXPORTING':
      return 'processing'
    case 'READY_FOR_REVIEW':
    case 'COMPLETED':
      return 'ready'
    case 'FAILED':
      return 'failed'
    case 'AWAITING_UPLOAD':
      return entry.sourceUploaded || locallyUploaded ? 'confirmation_pending' : null
    case 'CANCELLED':
      return null
  }
}

interface ReconcileOptions {
  /** A concurrency-conflict recovery must start a request after the 409. It
      may not reuse a jobs-list call that was already in flight. */
  forceFresh?: boolean
}

/** True only when an authoritative snapshot was successfully applied. */
export async function reconcileCloudProjects(options: ReconcileOptions = {}): Promise<boolean> {
  const identity = getPortfolioSessionIdentity()
  if (!identity || !useAppStore.getState().isAuthenticated) return false

  if (options.forceFresh) {
    // Invalidate a pre-409 response and immediately replace it with a new
    // request. A hung older request cannot block conflict recovery, and its
    // eventual response fails the fence check below.
    advanceReconciliationFence(identity)
  } else {
    const existing = inFlightBySession.get(identity)
    if (existing) return existing
  }
  const startingFence = reconciliationFence(identity)
  const request = (async () => {
    let map: Record<string, CloudJobStatus>
    try {
      map = await listCloudJobs()
    } catch {
      return false // sanitized: ordinary reconciliation remains best-effort and silent
    }
    const projects: Project[] = []
    const seenProjects = new Set<string>()
    const existingByProject = new Map(
      useAppStore.getState().projects.map((project) => [project.id, project]),
    )
    for (const [jobId, entry] of Object.entries(map)) {
      const existingProject = existingByProject.get(entry.projectId)
      // A successful S3 POST is client-observed durable evidence for this tab
      // even if the first verification request never produced a response.
      // Logout clears it; later sessions recover only the server marker.
      const locallyUploaded = existingProject?.jobId === jobId && existingProject.completionPending === true
      const status = toStatus(entry, locallyUploaded)
      if (status === null || seenProjects.has(entry.projectId)) continue
      seenProjects.add(entry.projectId)
      projects.push({
        id: entry.projectId, // durable product identity
        projectVersion: entry.projectVersion,
        jobId, // the distinct processing-job identity (map key)
        name: entry.project_name || `Project ${entry.projectId.slice(0, 6)}`,
        status,
        completionPending: status === 'confirmation_pending' ? true : undefined,
        createdAt: entry.created_at ?? new Date().toISOString(),
        durationSecs: entry.duration_secs ?? undefined,
        model: entry.model ?? undefined,
        chunkSize: entry.chunk_size ?? undefined,
        starred: entry.starred,
      })
    }
    // A response belongs to the exact accepted token generation that started
    // it. Logout, token replacement, or a restored unauthenticated shell
    // invalidates it before any Zustand/sessionStorage mutation.
    if (
      !isCurrentPortfolioSession(identity) ||
      !useAppStore.getState().isAuthenticated ||
      reconciliationFence(identity) !== startingFence
    ) return false

    // A successful list is authoritative for cloud metadata. This single
    // replacement prunes fixture, stale, unsupported, and abandoned cards.
    // The catch above deliberately preserves the last valid view on failure.
    useAppStore.setState({ projects })
    return true
  })()
  inFlightBySession.set(identity, request)
  return request.finally(() => {
    if (inFlightBySession.get(identity) === request) inFlightBySession.delete(identity)
  })
}
