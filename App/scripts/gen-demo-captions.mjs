#!/usr/bin/env node
// Deterministically derive WebVTT caption tracks for the portfolio demo from the
// committed Sintel fixtures. Output is committed; re-running reproduces it exactly.
//
//  - captions.vtt            dialogue of the source clip (from transcript.json)
//  - described-captions.vtt  dialogue + the narration lines as the pre-rendered
//                            export.mp4 placed them: each line starts at
//                            scene.start + 0.25 s (the export mux offset) and runs
//                            for the MEASURED duration of the committed per-line
//                            narration clip (ffprobe of tts/scene_<n>_onyx.mp3 —
//                            the closest measured proxy for the baked mix),
//                            clamped to the clip end.
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const APP = join(dirname(fileURLToPath(import.meta.url)), '..')
const FIXTURES = join(APP, 'public', 'data', 'sintel-blender-cc')
const OUT = join(APP, 'portfolio-demo-assets', 'data', 'sintel-blender-cc')

const AD_START_OFFSET = 0.25
const CLIP_END = 120

// ffprobe format.duration of the committed Onyx per-line clips (see
// docs/portfolio-demo/ARTIFACT.md). Keyed by 1-based scene number.
const MEASURED_LINE_SECS = {
  1: 20.688, 2: 17.112, 3: 9.744, 4: 14.856, 5: 15.552,
  6: 15.144, 7: 13.416, 8: 20.424, 9: 21.864,
}

const ts = (secs) => {
  const s = Math.max(0, secs)
  const h = String(Math.floor(s / 3600)).padStart(2, '0')
  const m = String(Math.floor((s % 3600) / 60)).padStart(2, '0')
  const rest = (s % 60).toFixed(3).padStart(6, '0')
  return `${h}:${m}:${rest}`
}
const cue = (start, end, text) => `${ts(start)} --> ${ts(end)}\n${text}\n`

const transcript = JSON.parse(readFileSync(join(FIXTURES, 'transcript.json'), 'utf8'))
const scenes = JSON.parse(readFileSync(join(FIXTURES, 'scenes.json'), 'utf8'))
  .filter((s) => s.end > s.start)

const dialogueCues = transcript.map((u) => ({ start: u.start, end: u.end, text: u.text }))

const narrationCues = scenes.map((s, i) => {
  const start = s.start + AD_START_OFFSET
  const measured = MEASURED_LINE_SECS[i + 1]
  if (!measured) throw new Error(`no measured duration for scene ${i + 1}`)
  const end = Math.min(CLIP_END, start + measured)
  return { start, end, text: `[Narrator] ${s.caption}`, scene: i + 1 }
})

const vtt = (cues) =>
  'WEBVTT\n\n' +
  cues
    .slice()
    .sort((a, b) => a.start - b.start || a.end - b.end)
    .map((c) => cue(c.start, c.end, c.text))
    .join('\n')

mkdirSync(OUT, { recursive: true })
writeFileSync(join(OUT, 'captions.vtt'), vtt(dialogueCues))
writeFileSync(join(OUT, 'described-captions.vtt'), vtt([...dialogueCues, ...narrationCues]))
console.log(`wrote ${join(OUT, 'captions.vtt')} (${dialogueCues.length} cues)`)
console.log(
  `wrote ${join(OUT, 'described-captions.vtt')} (${dialogueCues.length + narrationCues.length} cues)`,
)
