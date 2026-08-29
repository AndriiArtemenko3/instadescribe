import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  fetchCloudDeliverables,
  fetchCloudRender,
  fetchCloudReview,
} from '@/lib/reviewLifecycle'

function key(kind: 'review' | 'render' | 'deliverables', jobId: string) {
  return ['cloud-lifecycle', kind, jobId] as const
}

export function useCloudReviewLifecycle(jobId: string | undefined, enabled: boolean) {
  const queryClient = useQueryClient()
  const active = enabled && !!jobId
  const reviewQuery = useQuery({
    queryKey: key('review', jobId ?? ''),
    queryFn: () => fetchCloudReview(jobId!),
    enabled: active,
    retry: 1,
    refetchOnWindowFocus: true,
  })
  const review = reviewQuery.data
  const renderQuery = useQuery({
    queryKey: key('render', jobId ?? ''),
    queryFn: () => fetchCloudRender(jobId!),
    enabled: active && review?.state === 'completed',
    retry: 1,
    refetchOnWindowFocus: true,
    refetchInterval: (query) => {
      const state = query.state.data?.state
      return state === 'completed' || state === 'failed' || state === 'cancelled' ? false : 3_000
    },
  })
  const render = renderQuery.data
  const deliverablesQuery = useQuery({
    queryKey: key('deliverables', jobId ?? ''),
    queryFn: () => fetchCloudDeliverables(jobId!),
    enabled: active && render?.state === 'completed',
    retry: 1,
    refetchOnWindowFocus: true,
  })

  async function refreshAfterFinish() {
    if (!jobId) return
    await queryClient.invalidateQueries({ queryKey: key('review', jobId), exact: true })
    await queryClient.invalidateQueries({ queryKey: key('render', jobId), exact: true })
    await queryClient.invalidateQueries({ queryKey: key('deliverables', jobId), exact: true })
  }

  return {
    review,
    render,
    deliverables: deliverablesQuery.data?.items ?? [],
    loading: reviewQuery.isPending || (
      review?.state === 'completed' && renderQuery.isPending
    ) || (render?.state === 'completed' && deliverablesQuery.isPending),
    unavailable: reviewQuery.isError || renderQuery.isError || deliverablesQuery.isError,
    refreshAfterFinish,
  }
}
