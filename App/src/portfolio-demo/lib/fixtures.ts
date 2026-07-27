// Static fixture loading for the portfolio demo. The demo reads ONLY these
// committed files (plus media referenced by them) and never talks to an API.
//
// DIALOGUE AUTHORITY: the committed word-timestamped transcript
// (transcript.json) is the single user-facing spoken-dialogue source. The
// timeline's dialogue bands, the collision arithmetic, the Checks panel and
// every walkthrough claim all derive from it. The pipeline's coarse
// audio_events.json (which labels 32–120 s as silence despite transcribed
// dialogue at 47.56–49.06, 70.32–71.72, 73.48–74.88 and 108.02–109.42 s) is
// provenance only and is neither fetched nor shipped by this demo.
import { toScene, toAdGap } from '@/lib/transforms'
import { sentenceCaseStart } from './text'
import type {
  Scene,
  AudioEvent,
  AdGap,
  Entity,
  PipelineScene,
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

export interface TranscriptUtterance {
  text: string
  start: number
  end: number
}

export interface DemoData {
  scenes: Scene[]
  /** Spoken-dialogue events derived from the transcript (the authority). */
  audioEvents: AudioEvent[]
  adGaps: AdGap[]
  entities: Entity[]
  transcript: TranscriptUtterance[]
}

/** Utterance-level transcript entries → the dialogue events the engine uses. */
export function transcriptToDialogueEvents(transcript: TranscriptUtterance[]): AudioEvent[] {
  return transcript.map((u, index) => ({
    id: index + 1,
    type: 'dialogue' as const,
    startSecs: u.start,
    endSecs: u.end,
    durationSecs: u.end - u.start,
    transcript: u.text,
  }))
}

async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`Could not load ${url} (${res.status})`)
  return res.json() as Promise<T>
}

export function loadTranscript(): Promise<TranscriptUtterance[]> {
  return fetchJson<TranscriptUtterance[]>(`${DATA_PATH}/transcript.json`)
}

export async function loadDemoData(): Promise<DemoData> {
  const [rawScenes, transcript, rawGaps, entities] = await Promise.all([
    fetchJson<PipelineScene[]>(`${DATA_PATH}/scenes.json`),
    loadTranscript(),
    fetchJson<PipelineAdGap[]>(`${DATA_PATH}/ad_placement_gaps.json`),
    fetchJson<Entity[]>(`${DATA_PATH}/entities.json`),
  ])
  return {
    // Display-layer cleanup only: sentence-initial casing of the drafts is
    // normalised on load; the committed fixture is untouched (see text.ts).
    scenes: rawScenes
      .filter((s) => s.end > s.start)
      .map(toScene)
      .map((s) => ({ ...s, text: sentenceCaseStart(s.text) })),
    audioEvents: transcriptToDialogueEvents(transcript),
    adGaps: rawGaps.map(toAdGap),
    entities,
    transcript,
  }
}
