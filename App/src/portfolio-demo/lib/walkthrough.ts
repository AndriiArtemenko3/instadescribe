import type { WalkStep } from '../components/WalkthroughOverlay'

// The focused five-stage walkthrough (ORIENT → IDENTIFY → REFINE → LISTEN →
// COMPLETE). Every number below is derived from the committed fixtures with
// the transcript as the single dialogue authority, and is pinned by
// lib/dialogueAuthority.test.ts:
//
//   scene 2 (26–39 s, 45-word draft ≈ 18.0 s at 1×): the film's dialogue
//   starts at 26.22 s — a blink BEFORE the narration's 26.25 s start — and
//   the overlap across "Oh" / "Hey, it's almost done" / "Hey sit still"
//   totals ≈ 4.7 s. No trim at any offered speed clears it.
//
//   scene 5 (60–68 s, 44-word draft ≈ 17.6 s at 1×): overruns its 8.0 s of
//   clear silence into the film's later lines "Oh" (70.32 s) and "Skills"
//   (73.48 s) — ≈ 2.8 s of overlap. The deterministic trim clears both the
//   overrun and the collision at every offered speed.

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
      'Green bands mark recommended silences — room for narration. Blue marks the ' +
      'film’s spoken lines, from its transcript. Where a drafted line would talk over ' +
      'speech, the overlap is drawn in red. The lower strip seeks through the clip.',
  },
  {
    id: 'orient-script',
    mode: 'modal',
    selector: '[data-tour="script"]',
    title: 'The script panel',
    body:
      'Selecting a scene opens its description here. The time budget under the text keeps ' +
      'score: how long the line takes to speak (estimated at 0.4 s per word, adjusted for ' +
      'speed) versus the clear silence its moment offers.',
  },
  // ── IDENTIFY ─────────────────────────────────────────────────────────────
  {
    id: 'identify',
    mode: 'modal',
    selector: '[data-tour="script"]',
    title: 'One line fights the dialogue',
    body:
      'Scene 2’s draft is 45 words — an estimated 18 seconds spoken — inside a ' +
      '13-second scene. Worse: the film is already speaking when the line begins ' +
      '(“Oh — hey, it’s almost done” starts a blink earlier), and together with ' +
      '“Hey sit still” they’d overlap the narration for about 4.7 seconds — the red ' +
      'span on the timeline.',
  },
  // ── REFINE A: the editorial fix ───────────────────────────────────────────
  {
    id: 'refine-off',
    mode: 'action',
    selector: '[data-tour="toggle-line"]',
    title: 'Let the dialogue breathe',
    body:
      'Trimming can’t rescue this line: the film is speaking from the line’s very first ' +
      'beat, so however short it gets, they still collide. The honest fix is editorial — ' +
      'switch the line off and let the dialogue carry the moment.',
    actionHint: 'Click “Switch this line off”.',
    doneNote: 'Scene 2’s conflict is cleared — its red span is gone from the timeline.',
  },
  // ── REFINE B: the trim fix ────────────────────────────────────────────────
  {
    id: 'refine-overrun',
    mode: 'modal',
    selector: '[data-tour="script"]',
    title: 'Another line runs into the next dialogue',
    body:
      'Scene 5’s draft is 44 words — roughly 17.6 estimated seconds spilling past its ' +
      '8 seconds of clear silence, straight into the film’s next lines (“Oh”, ' +
      '“Skills”). This one a trim genuinely fixes.',
  },
  {
    id: 'refine-fit',
    mode: 'action',
    selector: '[data-tour="fit"]',
    title: 'Trim it to fit',
    body:
      '“Fit to gap (local)” is a deterministic cut — it keeps the leading words and ' +
      'trims to what fits the silence at your current playback speed. No AI involved. ' +
      'Afterwards you can fine-tune the wording; the estimate updates as you type.',
    actionHint: 'Click “Fit to gap (local)”.',
    doneNote: 'The line fits its silence — and no longer touches the later dialogue.',
  },
  // ── LISTEN ────────────────────────────────────────────────────────────────
  {
    id: 'listen-line',
    mode: 'action',
    selector: '[data-tour="listen"]',
    title: 'Hear it — honestly labeled',
    body:
      '“Original line · Onyx” plays narration pre-generated for the original ' +
      'draft — your edits are not in it. Where your browser offers an on-device voice, ' +
      '“Read my text” speaks exactly what’s in the box, locally.',
    actionHint: 'Play a narration source.',
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
    actionHint: 'Click “Play described example”, then press play on the video.',
    doneNote: 'Take a moment with it — try listening without watching.',
  },
  // ── COMPLETE ─────────────────────────────────────────────────────────────
  {
    id: 'complete',
    mode: 'modal',
    title: 'That’s the loop',
    body:
      'You reviewed the drafted narration, retired a line that fought the dialogue, and ' +
      'trimmed one to fit its moment. The Checks tab still lists other drafts that run ' +
      'long or brush later dialogue — real drafts arrive imperfect; trim or switch them ' +
      'off if you like. In the full application this loop continues: narration re-renders ' +
      'for every edit, and the finished track exports with the film.',
  },
]
