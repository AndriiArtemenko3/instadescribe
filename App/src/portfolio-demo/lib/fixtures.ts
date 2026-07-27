// Static fixture loading for the portfolio demo. The demo reads ONLY these
// committed files (plus media referenced by them) and never talks to an API.
import { toScene, toAudioEvent, toAdGap } from '@/lib/transforms'
import type {
  Scene,
  AudioEvent,
  AdGap,
  Entity,
  PipelineScene,
  PipelineAudioEvent,
  PipelineAdGap,
} from '@/types'

export const DATA_PATH = '/data/sintel-blender-cc'
export const VIDEO_SRC = '/videos/sintel-blender-cc.mp4'
export const EXPORT_SRC = `${DATA_PATH}/export.mp4`
export const POSTER_SRC = `${DATA_PATH}/poster.jpg`
export const CAPTIONS_SRC = `${DATA_PATH}/captions.vtt`
export const DESCRIBED_CAPTIONS_SRC = `${DATA_PATH}/described-captions.vtt`
export const CLIP_DURATION_SECS = 120

/** Scene numbers with a committed pre-generated Onyx narration clip. */
export const BAKED_ONYX_SCENES: ReadonlySet<number> = new Set([1, 2, 3, 4, 5, 6, 7, 8, 9])

export function bakedLineUrl(sceneNumber: number): string {
  return `${DATA_PATH}/tts/scene_${sceneNumber}_onyx.mp3`
}

export interface DemoData {
  scenes: Scene[]
  audioEvents: AudioEvent[]
  adGaps: AdGap[]
  entities: Entity[]
}

async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`Could not load ${url} (${res.status})`)
  return res.json() as Promise<T>
}

export interface TranscriptUtterance {
  text: string
  start: number
  end: number
}

export function loadTranscript(): Promise<TranscriptUtterance[]> {
  return fetchJson<TranscriptUtterance[]>(`${DATA_PATH}/transcript.json`)
}

export async function loadDemoData(): Promise<DemoData> {
  const [rawScenes, rawEvents, rawGaps, entities] = await Promise.all([
    fetchJson<PipelineScene[]>(`${DATA_PATH}/scenes.json`),
    fetchJson<PipelineAudioEvent[]>(`${DATA_PATH}/audio_events.json`),
    fetchJson<PipelineAdGap[]>(`${DATA_PATH}/ad_placement_gaps.json`),
    fetchJson<Entity[]>(`${DATA_PATH}/entities.json`),
  ])
  return {
    scenes: rawScenes.filter((s) => s.end > s.start).map(toScene),
    audioEvents: rawEvents.map(toAudioEvent),
    adGaps: rawGaps.map(toAdGap),
    entities,
  }
}
