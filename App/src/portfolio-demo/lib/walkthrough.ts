import type { WalkStep } from '../components/WalkthroughOverlay'

// The focused five-stage walkthrough (ORIENT → IDENTIFY → REFINE → LISTEN →
// COMPLETE). Every number quoted below is real, computed from the committed
// fixtures: scene 2 (26–39 s, 45-word draft) begins at 26.25 s and the film's
// dialogue starts 0.15 s later (26.4 s), overlapping ≈3.2 s across three
// dialogue events — shortening cannot clear an overlap that starts at the
// line's first beat. Scene 5 (60–68 s, 44-word draft) is a pure timing
// overrun with 8 s of genuinely clear silence.

export const SCENE_TWO_ID = 2
export const SCENE_FIVE_ID = 5

export const WALKTHROUGH_STEPS: WalkStep[] = [
  // ── ORIENT ────────────────────────────────────────────────────────────────
  {
    id: 'orient-scenes',
    mode: 'modal',
    selector: '[data-tour="scenes"]',
    title: 'The scene list',
    body:
      'InstaScribe drafted one narration line for each of this film’s nine scenes. ' +
      'The coloured edge shows each line’s status, and the small toggle switches a ' +
      'line on or off — off means it stays out of the final mix.',
  },
  {
    id: 'orient-timeline',
    mode: 'modal',
    selector: '[data-tour="video"]',
    title: 'The film and its timeline',
    body:
      'Green bands mark recommended silences — room for narration. Blue is dialogue. ' +
      'When a line would talk over speech, the overlap is drawn in red. The lower strip ' +
      'seeks through the clip.',
  },
  {
    id: 'orient-script',
    mode: 'modal',
    selector: '[data-tour="script"]',
    title: 'The script panel',
    body:
      'Selecting a scene opens its description here. The time budget under the text keeps ' +
      'score: how long the line takes to speak (estimated at 0.4 s per word) versus the ' +
      'clear silence its moment offers.',
  },
  // ── IDENTIFY ─────────────────────────────────────────────────────────────
  {
    id: 'identify',
    mode: 'modal',
    selector: '[data-tour="script"]',
    title: 'One line fights the dialogue',
    body:
      'Scene 2’s draft is 45 words — an estimated 18 seconds spoken — inside a ' +
      '13-second scene. The narration starts at the top of the scene, and the ' +
      'characters start talking just 0.15 s later (“Hey, it’s almost done”), so the ' +
      'two collide for about 3 seconds — the red span on the timeline.',
  },
  // ── REFINE A: the editorial fix ───────────────────────────────────────────
  {
    id: 'refine-off',
    mode: 'action',
    selector: '[data-tour="toggle-line"]',
    title: 'Let the dialogue breathe',
    body:
      'Trimming can’t rescue this line: the dialogue begins almost the moment the ' +
      'narration does, so however short the line gets, they still collide. The honest ' +
      'fix is editorial — switch the line off and let the dialogue carry the moment.',
    actionHint: 'Click “Switch this line off”.',
    doneNote: 'Conflict cleared — the red span is gone from the timeline.',
  },
  // ── REFINE B: the trim fix ────────────────────────────────────────────────
  {
    id: 'refine-overrun',
    mode: 'modal',
    selector: '[data-tour="script"]',
    title: 'Another line just runs long',
    body:
      'Scene 5’s draft is 44 words — roughly double what its 8 seconds of clear ' +
      'silence can hold — and no dialogue is anywhere near. This one a trim can ' +
      'genuinely fix.',
  },
  {
    id: 'refine-fit',
    mode: 'action',
    selector: '[data-tour="fit"]',
    title: 'Trim it to fit',
    body:
      '“Fit to gap (local)” is a deterministic cut — it keeps the leading words ' +
      'and trims to what fits the silence. No AI involved. Afterwards you can fine-tune ' +
      'the wording; the estimate updates as you type.',
    actionHint: 'Click “Fit to gap (local)”.',
    doneNote: 'The line now fits its moment.',
  },
  // ── LISTEN ────────────────────────────────────────────────────────────────
  {
    id: 'listen-line',
    mode: 'action',
    selector: '[data-tour="listen"]',
    title: 'Hear it — two honest ways',
    body:
      '“Original line · Onyx” plays narration pre-generated for the original ' +
      'draft — your edits are not in it. “Read my text · browser voice” has your ' +
      'browser speak exactly what’s in the box right now.',
    actionHint: 'Play either one.',
    doneNote: 'That’s the difference between baked audio and your live text.',
  },
  {
    id: 'listen-film',
    mode: 'action',
    selector: '[data-tour="preview"]',
    title: 'The described film',
    body:
      'Finally, the film with narration mixed in — pre-rendered by the full pipeline from ' +
      'the original drafts, so your edits are not in this file.',
    actionHint: 'Click “Play described example”.',
    doneNote: 'Take a moment with it — try listening without watching.',
  },
  // ── COMPLETE ─────────────────────────────────────────────────────────────
  {
    id: 'complete',
    mode: 'modal',
    title: 'That’s the loop',
    body:
      'You reviewed the drafted narration, retired a line that fought the dialogue, ' +
      'trimmed one to fit its moment, and heard the result. The Checks tab still lists ' +
      'other drafts running past their scenes — real drafts arrive imperfect; trim or ' +
      'switch them off if you like. In the full application this loop continues: ' +
      'narration re-renders for every edit, and the finished track exports with the film.',
  },
]
