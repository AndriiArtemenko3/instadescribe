export interface User {
  email: string
  name: string
  tokenBalance?: number
}

export interface UploadSettings {
  mode: 'cheap' | 'normal' | 'custom'
  model: 'gpt-4.1' | 'gpt-5.4'
  fps: 0.5 | 1 | 8
  frameQuality: 'low' | 'high'
  chunkSizeSecs: 30 | 60 | 120
  audioExtraction: boolean
  detailLevel: 1 | 2 | 3 | 4 | 5
  presetStyle: 'documentary' | 'cinematic' | 'news' | 'sports' | 'education'
  language: string | null
}

export type SceneStatus = 'ok' | 'empty' | 'conflict' | 'inactive'

export function getSceneStatus(scene: Scene, collides = false): SceneStatus {
  if (!scene.active) return 'inactive'
  if (!scene.text.trim()) return 'empty'
  if (collides || scene.needsReview) return 'conflict'
  return 'ok'
}

// App-layer scene (used by editor components)
export interface Scene {
  id: number
  sceneNumber: number
  /** Exact canonical pipeline scene id (e.g. "scene_10") — the identity the
      cloud override PATCH uses. id/sceneNumber are UI ordering only. */
  sceneKey: string
  startSecs: number
  endSecs: number
  durationSecs: number
  text: string           // editable AD caption
  template: string       // caption_template from pipeline
  characterIds: string[]
  locked: boolean
  needsReview: boolean
  active: boolean        // user-controlled — false means excluded from export
  voiceId?: string
  voiceSpeed?: number
}

// Raw pipeline shape (from scenes.json)
export interface PipelineScene {
  scene_id: string
  start: number
  end: number
  frame_indices: number[]
  character_ids: string[]
  caption_template: string
  caption: string
  render_mode: string
  locked: boolean
  needs_review: boolean
}

export interface AudioEvent {
  id: number
  type: 'dialogue' | 'music' | 'sfx' | 'silence'
  startSecs: number
  endSecs: number
  durationSecs: number
  transcript?: string
}

// Raw pipeline shape (from audio_events.json)
export interface PipelineAudioEvent {
  start: number
  end: number
  event_type: string
  confidence: number
  transcript: string
}

export interface AdGap {
  id: number
  startSecs: number
  endSecs: number
  durationSecs: number
  isRecommended: boolean
}

// Raw pipeline shape (from ad_placement_gaps.json)
export interface PipelineAdGap {
  start: number
  end: number
  duration_seconds: number
  midpoint: number
  recommended_ad_start: number
  recommended: boolean
}

export interface Entity {
  id: string
  name: string
  first_mention_label: string
  pronoun: string
  aliases: string[]
  name_history: string[]
  user_renamed: boolean
}

export interface Project {
  id: string // durable product projectId (also the editor route id)
  /** Exact optimistic-concurrency token for cloud project metadata. */
  projectVersion?: number
  /**
   * Cloud processing-job id (G7). Distinct from the projectId and never
   * inferred from it: cloud API calls (status/manifest/overrides/scene
   * PATCH) use jobId; the editor route and store identity use projectId.
   * Optional so legacy/demo/study projects are unaffected.
   */
  jobId?: string
  name: string
  status: 'confirmation_pending' | 'processing' | 'ready' | 'draft' | 'failed'
  /** Source bytes are durable but upload-complete still needs retrying for
      this exact job. Ordinary session metadata only; never an upload URL. */
  completionPending?: boolean
  createdAt: string
  durationSecs?: number
  sceneCount?: number
  tokensUsed?: number
  model?: string
  chunkSize?: number
  videoFile?: string         // public path to video e.g. "/vibe.mp4"
  posterUrl?: string         // public path to poster JPG e.g. "/data/{id}/poster.jpg"
  posterAvifUrl?: string     // public path to AVIF poster (preferred when present)
  posterPlaceholder?: string // base64 24×14 WebP, rendered as blurred LQIP background
  dataPath?: string          // public path prefix e.g. "/data/vibe"
  starred?: boolean
}

export interface ExportJob {
  id: string
  projectId: string
  status: 'queued' | 'processing' | 'complete' | 'failed'
  formats: ExportFormat[]
  progress: number
  downloadUrls?: Record<ExportFormat, string>
}

export type ExportFormat = 'mp4' | 'mov' | 'mp3' | 'srt' | 'txt' | 'json'

export interface ApiResponse<T> {
  data: T
  error: string | null
}
