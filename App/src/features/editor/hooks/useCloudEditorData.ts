// Cloud editor data (G7 Gate B4): one manifest query keyed by the STABLE
// projectId + jobId pair (never a signed URL), refreshed before `expiresAt`
// with a safety margin and on window focus. Artifact JSON rides plain fetch
// through the manifest's signed references; an expired-signature failure
// triggers ONE bounded manifest refresh + retry, never an infinite loop.
// The refreshed pinned video URL always comes from the CURRENT manifest.

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toScene, toAudioEvent, toAdGap } from '@/lib/transforms'
import {
  fetchArtifactJson,
  fetchManifest,
  refreshDelayMs,
  type CloudManifest,
  type ManifestRef,
} from '@/lib/manifest'
import { queryKeys } from '@/lib/queryKeys'
import type {
  AdGap,
  AudioEvent,
  Entity,
  PipelineAdGap,
  PipelineAudioEvent,
  PipelineScene,
  Scene,
} from '@/types'

type RefPicker = (manifest: CloudManifest) => ManifestRef

export function useCloudEditorData(projectId: string, jobId: string | undefined, enabled: boolean) {
  const queryClient = useQueryClient()
  const manifestKey = queryKeys.manifest(projectId, jobId ?? '')

  const manifestQuery = useQuery({
    queryKey: manifestKey,
    queryFn: () => fetchManifest(projectId, jobId!),
    enabled: enabled && !!jobId,
    refetchOnWindowFocus: true,
    retry: 1,
    // Refresh before the common expiry instant (safety margin inside).
    refetchInterval: (query) => (query.state.data ? refreshDelayMs(query.state.data) : false),
  })
  const manifest = manifestQuery.data

  // Expired-signature handling: one bounded refresh + retry per fetch.
  async function fetchViaManifest<T>(pick: RefPicker, artifact: string): Promise<T> {
    const current = queryClient.getQueryData<CloudManifest>(manifestKey)
    if (!current) throw new Error(`artifact ${artifact}: manifest missing`)
    try {
      return await fetchArtifactJson<T>(pick(current), artifact)
    } catch (err) {
      const message = err instanceof Error ? err.message : ''
      if (!message.includes('expired-or-denied')) throw err
      const refreshed = await manifestQuery.refetch()
      const next = refreshed.data
      if (!next) throw err
      return fetchArtifactJson<T>(pick(next), artifact) // exactly one retry
    }
  }

  const scenesQuery = useQuery({
    queryKey: queryKeys.cloudScenes(projectId, jobId ?? ''),
    enabled: !!manifest,
    retry: false,
    queryFn: async (): Promise<Scene[]> => {
      const raw = await fetchViaManifest<PipelineScene[]>((m) => m.artifacts.scenes, 'scenes')
      return raw.filter((s) => s.end > s.start).map(toScene)
    },
  })

  const audioEventsQuery = useQuery({
    queryKey: queryKeys.cloudAudioEvents(projectId, jobId ?? ''),
    enabled: !!manifest,
    retry: false,
    queryFn: async (): Promise<AudioEvent[]> => {
      const raw = await fetchViaManifest<PipelineAudioEvent[]>(
        (m) => m.artifacts.audioEvents,
        'audioEvents',
      )
      return raw.map(toAudioEvent)
    },
  })

  const adGapsQuery = useQuery({
    queryKey: queryKeys.cloudAdGaps(projectId, jobId ?? ''),
    enabled: !!manifest,
    retry: false,
    queryFn: async (): Promise<AdGap[]> => {
      const raw = await fetchViaManifest<PipelineAdGap[]>(
        (m) => m.artifacts.placementGaps,
        'placementGaps',
      )
      return raw.map(toAdGap)
    },
  })

  const entitiesQuery = useQuery({
    queryKey: queryKeys.cloudEntities(projectId, jobId ?? ''),
    enabled: !!manifest,
    retry: false,
    queryFn: () => fetchViaManifest<Entity[]>((m) => m.artifacts.entities, 'entities'),
  })

  return {
    manifest,
    manifestPending: manifestQuery.isPending,
    manifestError: manifestQuery.isError,
    rawScenes: scenesQuery.data ?? [],
    scenesLoading: scenesQuery.isPending,
    audioEvents: audioEventsQuery.data ?? [],
    adGaps: adGapsQuery.data ?? [],
    entities: entitiesQuery.data ?? [],
    // Ephemeral by design: read straight from the current manifest, never
    // stored anywhere durable.
    videoUrl: manifest?.artifacts.video.url,
    posterUrl: manifest?.artifacts.posterJpg?.url,
  }
}
