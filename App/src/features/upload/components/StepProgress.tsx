import { useNavigate } from 'react-router-dom'
import { AlertTriangle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

const STAGE_LABELS: Record<string, string> = {
  queued:             'Queued…',
  initializing:       'Initialising…',
  extracting_frames:  'Extracting frames…',
  transcribing_audio: 'Transcribing audio…',
  analyzing_frames:   'Analysing frames with AI…',
  exporting:          'Exporting results…',
  complete:           'Complete',
}

interface StepProgressProps {
  progress:        number
  isReady:         boolean
  isFailed:        boolean
  failedError:     string | null
  stage:           string
  chunksDone:      number
  chunksTotal:     number
  estimatedMinutes: number
  newProjectId:    string | null
  onRetry:         () => void
}

export function StepProgress({
  progress, isReady, isFailed, failedError, stage,
  chunksDone, chunksTotal, estimatedMinutes,
  newProjectId, onRetry,
}: StepProgressProps) {
  const navigate = useNavigate()

  const stageLabel = STAGE_LABELS[stage] ?? (stage ? `${stage}…` : 'Processing…')

  const minutesLeft = isReady
    ? 0
    : Math.max(1, Math.round(estimatedMinutes * (1 - progress / 100)))

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-3 rounded-xl border border-neutral-200 bg-neutral-50 px-5 py-5">
        <p className="text-sm font-medium text-neutral-700">Progress:</p>

        <div className="h-3 w-full overflow-hidden rounded-full bg-neutral-200">
          <div
            role="progressbar"
            aria-label="Processing progress"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={isFailed || isReady ? 100 : progress}
            className={cn(
              'h-full rounded-full transition-all duration-500',
              isFailed ? 'bg-danger-400' : 'bg-neutral-900',
            )}
            style={{ width: `${isFailed || isReady ? 100 : progress}%` }}
          />
        </div>

        {isFailed ? (
          <p className="text-sm font-medium text-danger-400">Failed</p>
        ) : isReady ? (
          <div className="flex items-center justify-between">
            <p className="text-sm font-medium text-success-400">Ready!</p>
            <p className="text-xs font-medium tabular-nums text-success-400">100%</p>
          </div>
        ) : (
          <div className="flex items-center justify-between">
            <p className="text-sm text-neutral-500">{stageLabel}</p>
            <div className="flex items-center gap-3">
              {chunksTotal > 0 && (
                <p className="text-xs text-neutral-400">
                  {chunksDone} / {chunksTotal} chunks
                </p>
              )}
              <p className="text-xs text-neutral-400">
                ~{minutesLeft} min left
              </p>
            </div>
          </div>
        )}
      </div>

      {/* Error details */}
      {isFailed && failedError && (
        <div className="rounded-xl border border-danger-200 bg-danger-50 px-5 py-4">
          <div className="flex items-start gap-2">
            <AlertTriangle size={15} className="mt-0.5 shrink-0 text-danger-400" />
            <div>
              <p className="text-sm font-medium text-danger-700 mb-1">Pipeline failed</p>
              <p className="text-xs text-danger-600">{failedError}</p>
            </div>
          </div>
        </div>
      )}

      {!isFailed && (
        <p className="text-sm text-neutral-500 leading-relaxed">
          {isReady
            ? 'Your audio descriptions are ready to review. Open the project in the editor, or find it later in the Projects tab.'
            : 'Your audio descriptions are being generated. When your project is ready you can open it in the editor. You can find all your projects in the Projects tab.'}
        </p>
      )}

      <div className="flex justify-center gap-3">
        <Button
          variant="outline"
          size="sm"
          onClick={() => navigate('/dashboard/projects')}
        >
          See All Projects
        </Button>

        {isFailed ? (
          <Button variant="default" size="sm" onClick={onRetry}>
            Try Again
          </Button>
        ) : (
          <Button
            variant="default"
            size="sm"
            disabled={!isReady || !newProjectId}
            onClick={() => newProjectId && navigate(`/editor/${newProjectId}`)}
          >
            Open in Editor
          </Button>
        )}
      </div>
    </div>
  )
}
