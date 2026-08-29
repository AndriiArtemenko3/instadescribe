import { CloudApiError } from './cloudApi'
import { reconcileCloudProjects } from './cloudProjects'

/** Shared Home/Projects mutation feedback. A stale response first refreshes
    the authoritative project version, then asks the user to retry. No raw
    response body, URL, token, or storage detail is surfaced. */
export async function reportProjectMutationError(action: string, error: unknown): Promise<void> {
  if (error instanceof CloudApiError) {
    if (error.code === 'stale_version') {
      try {
        const refreshed = await reconcileCloudProjects({ forceFresh: true })
        window.alert(refreshed
          ? `Could not ${action}: this project changed elsewhere. The latest version is loaded; retry your change.`
          : `Could not ${action}: this project changed elsewhere, but the latest version could not be loaded. Refresh the page before retrying.`)
      } catch {
        window.alert(`Could not ${action}: this project changed elsewhere, but the latest version could not be loaded. Refresh the page before retrying.`)
      }
      return
    }
    window.alert(`Could not ${action}: the cloud service did not accept the change. Please retry.`)
    return
  }
  window.alert(`Could not ${action}: an unexpected error occurred. Please retry.`)
}
