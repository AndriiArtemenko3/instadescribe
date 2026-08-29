import { useState, type MouseEvent } from 'react'
import { Button } from '@/components/ui/button'
import { completeWithRetry, submitErrorMessage } from '@/lib/cloudUpload'
import { fenceCloudProjectReconciliation } from '@/lib/cloudProjects'
import { useAppStore } from '@/store/appStore'
import type { Project } from '@/types'

interface CloudCompletionButtonProps {
  project: Project
  compact?: boolean
}

/** Recovery action for a source that is durable while upload-complete is
    pending. It calls only upload-complete for the stored jobId: no create,
    upload contract, signed URL, or S3 request is available to this component. */
export function CloudCompletionButton({ project, compact = false }: CloudCompletionButtonProps) {
  const [confirming, setConfirming] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const updateProject = useAppStore((state) => state.updateProject)

  if (project.status !== 'confirmation_pending' || !project.jobId) return null

  async function confirm(event: MouseEvent<HTMLButtonElement>) {
    event.stopPropagation()
    if (confirming || !project.jobId) return
    // Fence a dashboard list already in flight, and fence again on success
    // against a list that began while the completion request was pending.
    fenceCloudProjectReconciliation()
    setConfirming(true)
    setError(null)
    try {
      await completeWithRetry(project.jobId, 1, 0)
      fenceCloudProjectReconciliation()
      updateProject(project.id, { status: 'processing', completionPending: false })
    } catch (reason) {
      setError(submitErrorMessage(reason, 'completing'))
    } finally {
      setConfirming(false)
    }
  }

  return (
    <div className={compact ? 'min-w-0' : 'space-y-1.5'} onClick={(event) => event.stopPropagation()}>
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={confirming}
        onClick={confirm}
      >
        {confirming ? 'Confirming…' : 'Confirm upload'}
      </Button>
      {error && (
        <p
          role="alert"
          className={compact
            ? 'mt-1 max-w-48 text-xs leading-snug text-danger-500'
            : 'text-xs leading-relaxed text-danger-500'}
        >
          {error}
        </p>
      )}
    </div>
  )
}
