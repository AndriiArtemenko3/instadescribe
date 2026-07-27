# InstaScribe Live Onboarding v1 — implementation contract & work order

**Status:** Correction pass delivered for human visual review (draft PR #1; not merged, not deployed)
**Branch:** `polish/instascribe-live-onboarding-v1`
**Base SHA:** `88571e982c12148b002c4bf099964790ccbb99bb` (origin/main at start; matched the
authoring contract's expected SHA exactly; working tree was clean)
**Baseline commands at base SHA (all exit 0):** `npm run build` (516.08 kB main chunk),
`npm run lint` (0 errors, 2 pre-existing warnings), `npm test` (16/16 pass)

This document translates the authored goal contract
(`CLAUDE_FABLE_INSTASCRIBE_LIVE_ONBOARDING_GOAL.md`, 2026-07-27) into a tracked,
non-weakened implementation contract. Decisions and deviations are recorded here as work
progresses.

---

## 1. Outcome

A truthful, backend-free **InstaScribe Live Onboarding** prototype:

- a dedicated compile-time **portfolio-demo** entry (`build:portfolio-demo`) containing only
  the bounded onboarding experience and its static fixtures — not the application router;
- the approved **Figure 00** entry composition at exact case-study scale: an intro screen in
  that position inviting "Try InstaScribe Live Onboarding" with a "Start now" action; the
  full guided experience then loads **in the same compositional region**;
- one focused, truthful **3–4 minute** onboarding loop, end-to-end, with **no backend,
  account, upload, API key, telemetry, fake network operation, or runtime model call**;
- every visible capability and limitation accurately represented at the point of
  interaction;
- accessibility, keyboard, responsive fallback, licensing, provenance, security, routing,
  restart, zero-API criteria explicitly verified;
- the normal InstaScribe application build output-equivalent;
- a static-host security package (prepared, **not deployed**; no DNS/Cloudflare changes).

Explicitly **not** claimed: subjective visual excellence, research validation, production or
publication readiness. No completed research protocol, participant sample, measures, or
findings are asserted anywhere. `VITE_STUDY_MODE`'s existence is not presented as research
evidence. Closing one's eyes is never described as simulating blindness.

## 2. Authority boundaries

- Work only in this repository. The portfolio website repo
  (`andriiartemenko.com`, incl. branch `polish/instascribe-case-study-shell-v1 @ dd82a7b`)
  is **read-only visual/placement authority**; it is not edited, built, or pushed.
- The supplied ZIP (`~/Downloads/instascribe-demo.zip`) is untrusted reference. It was
  inventoried and extracted only into a scratchpad directory; path-traversal and executable
  checks passed; `tools/bake_tts.py` was read as text only and **never executed** (it embeds
  another user's absolute path and could start billable TTS jobs). No OpenAI call, no key,
  no TTS regeneration, no usage cost. The ZIP's compiled site is a behavioral baseline only;
  none of its minified JS/CSS is copied into this repository.
- Never reset, rebase, force-push, delete branches, or discard unknown work. Draft PR only;
  no merge, release, deploy, DNS, or Cloudflare action.

## 3. Verified ground truth the design rests on

Fixture facts (committed `App/public/data/sintel-blender-cc/`, byte-identical to the ZIP's
copies — SHA-256 verified):

- 9 scenes over 0–119 s of the 120 s Sintel excerpt (564×240 h264 + AAC, CC BY 3.0,
  archive.org-tagged); 2 entities (`char_1` "young woman"/she, `char_2` "dragon-like
  creature"/it); scene captions carry `caption_template` with `{char_N_first|name|subj|obj|
  poss|*_cap}` tokens whose rendering semantics live in
  `modular_pipeline/normalisation.py::render_caption_template`.
- Dialogue (audio_events): 26.4–27.2, 27.9–28.8, 30.5–32.0 s (the only transcribed dialogue
  events; a wordless 0.6–1.0 s "dialogue" event is reclassified silence by
  `transforms.toAudioEvent`).
- Curated AD gaps: 0–26.22, 32.12–47.43, 49.06–70.32, 74.88–108.02 s.
- The real export mux places each narration line at `scene.start + 0.25 s`
  (`export_service.py:167`), exactly as the client collision model assumes
  (`collisions.ts`, `AD_START_OFFSET = 0.25`).

Consequences (all verified by arithmetic against the fixtures, to be re-verified in browser):

- **Scene 2** (26–39 s, 45-word draft ≈ 18.0 s estimated): genuinely collides with the
  three dialogue events at 26.4–32.0 s (≈3.2 s overlap) **and cannot be cleared by
  shortening** — the dialogue begins 0.15 s after the narration start. The engine's honest
  resolutions: shorten (reduces overrun, not the head collision) or **switch the line off**
  (the dialogue carries the moment — a real AD editorial decision). This is the IDENTIFY
  teaching moment.
- **Scene 5** (60–68 s, 44-word draft ≈ 17.6 s estimated): a pure **timing** overrun — no
  dialogue anywhere near; an 8 s silence window is genuinely available (gap 3 ∩ scene 5).
  "Fit to gap (local)" truthfully resolves it end-to-end. This is the REFINE teaching
  moment.
- Current demo-mode fakes to resolve (file:line at base SHA):
  1. `demoPatchEntity` no-op rename (`demoApi.ts:40-42`) vs copy promising propagation
     (`CharactersPanel.tsx:143-144`, tour copy `EditorPage.tsx:167`).
  2. Speed select never affects demo playback (`api.ts:71` drops text+speed; no
     `playbackRate` anywhere).
  3. "Preview" implies the edited text is spoken; demo fetches a baked
     `scene_<n>_<voice>.mp3` (none committed → 2 s silence fallback) regardless of edits.
  4. Smart Fill is a 2.3 words/s leading-clause truncation (`demoApi.ts:47-58`), not
     semantic rewriting; its gating title promises collision fixes it cannot deliver.
  5. Export dialog offers mp4/mp3/srt/csv/docx and "Download srt" etc., but demo always
     returns the fixed pre-rendered `export.mp4` (`demoApi.ts:108-114`); progress UI implies
     rendering that never happens; study copy claims "~30 seconds" and "This is what a blind
     or low-vision viewer hears."
  6. Quality tab presents a weighted "overall" score; "grounding" is only a duplicate-text
     check (`evaluation.ts:127`).
  7. Telemetry endpoints exist in the bundle: `/api/log`, `/api/study/session`
     (`session.ts:120,143`), plus `/api/jobs*`, `/api/providers` client code.

## 4. Architecture — dedicated compile-time entry

New source under `App/src/portfolio-demo/` + `App/portfolio-demo.html` +
`App/vite.portfolio-demo.config.ts` (out dir `App/dist-portfolio-demo/`, entry renamed to
`index.html` at close-bundle; host files copied in). The portfolio-demo entry **never
imports** `lib/api.ts`, `lib/session.ts`, `lib/uploadApi.ts`, `lib/providersApi.ts`,
`store/appStore.ts`, or any auth/dashboard/upload/study/tutorials module — the forbidden
strings and routes are absent by construction and confirmed by a post-build string audit.

Reused pure modules: `types`, `lib/collisions.ts`, `lib/transforms.ts`, UI primitives
(`button`, `separator`, …), `SceneListPanel`, `VideoPanel` (both prop-pure). Forked/demo-own:
script panel, quality checks, characters panel, walkthrough overlay (with real focus trap /
inert background / live regions), fixtures loader, caption-template renderer (TS mirror of
`normalisation.py`, pinned by tests), narration engine (baked mp3 + `playbackRate`;
`speechSynthesis` for current-text reading, feature-detected and labeled "browser voice"),
local fit-to-gap.

Routes (react-router, dedicated router):

- `/` — standalone intro (Figure-00-native composition; H1; truthful preface; Start now).
- `/onboarding` — direct seeded onboarding (deterministic; suitable for a future iframe;
  `?embed=1` autostart variant does not repeat the intro).
- `/review/figure-00` — **development-only** Figure 00 review harness (light page chrome
  approximating the case-study shell; excluded from production build via `import.meta.env.DEV`).
- SPA fallback documented for exactly `/` and `/onboarding`.

State: in-memory only (deterministic restart; reload = clean start; no cookies, no
localStorage requirement; nothing persisted). No request to any other origin; no `/api/*`;
media loads only after user intent (video after Start now; per-line mp3 on demand;
`export.mp4` only at the LISTEN step).

Scripts (App/package.json): `build:portfolio-demo`, `preview:portfolio-demo`,
`test:portfolio-demo` (Playwright e2e; `@playwright/test` is the narrowly-justified new dev
dependency), `verify:portfolio-demo` (string audit + budget + SHA-256 manifest + report).
Default `build`/`lint`/`test` remain untouched and green.

## 5. The onboarding loop (5 stages + completion, target 3–4 min)

0. **INTRO** — one respectful sentence on audio description; states: interactive product
   prototype, one preloaded film (Sintel, CC BY 3.0, visible attribution + link), committed
   fixtures, no upload/sign-up/backend/model call, ~3–4 minutes, some final media
   pre-rendered. Action: Start now.
1. **ORIENT** — the editor loads in place (no layout shift beyond an explicitly documented
   in-place expansion if measurement demands it). Teaches only: scene list (9 drafted
   lines), video + timeline (green = recommended silence, blue = dialogue, red = overlap),
   script panel. Modal walkthrough dialog: focus received/trapped/restored, Escape, inert
   background, ARIA-correct.
2. **IDENTIFY** — jumps to scene 2; explains with fixture numbers why it needs attention
   (18 s draft in a 13 s scene, talking over two dialogue lines for ~3.2 s; red timeline
   span). No fabricated conflict.
3. **REFINE** — action steps (background interactive; walkthrough collapses to a task
   card): switch scene 2 off and watch the conflict clear (the honest fix: this line starts
   during dialogue; trimming cannot clear it); then scene 5: "Fit to gap (local)" trims the
   draft into the genuinely available 8 s of silence — time budget (target vs estimated
   seconds) shown; flag clears. Fit to gap is labeled local/deterministic, never AI.
4. **LISTEN** — three honestly-labeled narration behaviors: (a) *original generated line*
   (pre-generated Onyx mp3 from the handoff; does not include edits; real `playbackRate`
   speed), (b) *read my current text* via the browser's speech synthesis (labeled "browser
   voice", feature-detected, omitted with an honest note when unavailable), (c) the
   *pre-rendered described example* (`export.mp4`) — presented as produced by the full
   pipeline for the original draft; **explicitly not containing the visitor's edits**;
   loaded only at this step; audio-only-focus invitation without any blindness-simulation
   claim; captions track (from committed transcript + scene fixtures) attached.
5. **COMPLETE** — factual summary derived from actual session state; Restart (full
   deterministic reset, never touching a backend route); Back-to-intro / close semantics
   suitable for the future embed; visible licensing/provenance; text transcript of the
   walkthrough available outside the interactive region.

Rename: deterministic **local propagation is implemented** (TS mirror of
`render_caption_template` + `user_renamed` first-reference rule), covered by unit tests, so
the Characters tab keeps its promise inside the demo. Voice picker is replaced by a static
"Onyx · pre-generated" indication (only Onyx clips are shipped). The weighted overall
quality score is replaced in the demo by transparent per-scene checks ("fits its moment",
"clear of dialogue", "switched on") with plain-language explanations; no authoritative
overall number, no "grounding" claim.

## 6. Assets, performance, provenance

Imported from the audited ZIP (only what the walkthrough uses): the 9 **Onyx** narration
clips `tts/scene_<1..9>_onyx.mp3` (24 kHz mono 160 kb/s CBR; SHA-256 recorded in
`docs/portfolio-demo/ARTIFACT.md`; source: supplied handoff ZIP; exact generation commit
unproven — documented as such; **not** regenerated). Everything else already exists in-repo
and is hash-identical to the ZIP. New tiny derived fixtures: WebVTT captions generated
deterministically from committed `transcript.json`/`scenes.json` (provenance documented).

Budgets: no MP4/MP3/large-JSON fetch before Start now; intro payload lightweight (measured
and reported); total deploy payload target ≤ 21 MB; `export.mp4` fetched only at LISTEN;
`preload` attributes deliberate; no source maps; no unused app chunks; no horizontal
overflow; no avoidable layout shift at the intro→editor handoff. ffprobe recorded for both
MP4s and a representative retained MP3 set.

Visible attribution: Sintel — © Blender Foundation, durian.blender.org, CC BY 3.0 with a
working link, shown on the intro and in the demo's About/complete surfaces, noting the
excerpt is adapted with an audio-description track. Canonical notice extended in a bounded
way (`THIRD_PARTY_NOTICES.md` already covers the clip; portfolio-demo NOTICE added under
`docs/portfolio-demo/`).

## 7. Figure 00 / embed review states

Composition targets (from the read-only website authority): 1232 × 693 stage (true 16:9),
square shadowless plane on `#16181C` (`--project-media`), 1px keyline `#ccd2da`, mono
label/caption hierarchy, cool trace palette, indigo reserved for real interactive states,
the product's own green only as a restrained transition into the product UI. Required
review states: 1440×900 standalone, exact 1232×693 stage, 1120 eligible desktop, 1024
boundary, 768 and 390×844 and 320 fallbacks, zoom/short-height, reduced motion. 1024 px is
the initial embed-eligibility boundary; the measured threshold will be recorded and used.
Below eligibility: coherent intro, honest "the interactive editor needs a wider display"
explanation, full text walkthrough + transcript + pre-rendered listening example, "Open the
full demo" only where it leads to a usable wider context; never a blank/zero-width panel;
never horizontal scroll of the host page.

Söhne is licensed to the website and is **not** bundled here; the intro declares
`'Söhne', 'Helvetica Neue', Arial, sans-serif` (and a system mono stack) so the future
same-origin deployment renders it natively while this repo ships no new font. *(Deviation
recorded: visual parity of the intro's display face depends on the deploy origin serving
Söhne; fallbacks approximate it here.)*

## 8. Security & hosting package (prepared, not deployed)

`App/portfolio-demo-host/`: `_headers` (noindex/nofollow/noarchive X-Robots-Tag; nosniff;
strict Referrer-Policy; Permissions-Policy denying unneeded capabilities; CSP limited to
self-hosted script/style/font/media/connect with `frame-ancestors` restricted to
`https://andriiartemenko.com https://www.andriiartemenko.com` + documented preview origins;
no X-Frame-Options that would block the intended cross-origin iframe; immutable caching for
hashed assets, shorter for media/data), `_redirects` (SPA fallback for intro/onboarding
only), `robots.txt`. Copied into the deploy package by the build. Future iframe contract
(direct embed URL, minimum size, title, sandbox, allow, referrer policy, loading, intro→
editor handoff, mobile fallback, failure state) documented in
`docs/portfolio-demo/EMBED_CONTRACT.md`. Intended future origin:
`https://instascribe-demo.andriiartemenko.com` (spelling verified).

## 9. Verification matrix

Functional / network / truthfulness / accessibility / responsive / build-regression checks
exactly as enumerated in the goal contract, executed after the final source edit:
`npm run build`, `npm run lint`, `npm test`, `npm run build:portfolio-demo`,
`npm run test:portfolio-demo` (Playwright: intro, start-in-place, direct embed seeding,
core loop, Back/Skip/Exit/Restart, clean restart, static data/media resolution, removed
routes absent, zero console errors, `/api/*`-and-external-request ban, media deferral,
keyboard-only completion, focus containment, reduced motion), `npm run verify:portfolio-demo`
(forbidden-string audit, size budget, SHA-256 manifest). Real-browser captures at all §7
viewports. Evidence under `docs/portfolio-demo/review/<timestamp>/` postdating the final
frontend edit.

## 10. Decision log

- **D1** IDENTIFY uses scene 2's real dialogue collision; its honest resolution is
  deactivation (shortening provably cannot clear it — recorded arithmetic in §3). REFINE
  uses scene 5's real, fully fixable timing overrun. No conflict is fabricated.
- **D2** Rename propagation implemented locally (mirroring `normalisation.py`) rather than
  removed — with pinned tests.
- **D3** Speed control kept and made real (`playbackRate` / `utterance.rate`); voice picker
  reduced to the truthful single shipped narrator (Onyx).
- **D4** Overall weighted quality score not shown in the demo; transparent per-scene checks
  instead.
- **D5** Export dialog (format chooser, progress simulation) absent from the demo; the
  LISTEN step presents the pre-rendered described example for what it is.
- **D6** All demo state in memory: restart is deterministic; nothing persists; zero
  cookies/localStorage.
- **D7** `@playwright/test` added as the narrowly-justified browser-test dev dependency
  (repo had none); lockfile updated deliberately.
- **D8** Söhne not bundled (license); declared with fallbacks (§7 deviation note).
- **D9** Only the 9 Onyx mp3s imported (≈2.98 MB) to hold the ≤21 MB deploy budget; the 27
  other voice clips are not shipped.

- **D10** Measured results (Phase 3): the 1024 px eligibility boundary is confirmed
  empirically (three-panel editor keeps a ~500 px center workspace; browser capture
  `editor-1024x693-boundary.png`); the editor fits the 693 px stage height with no
  in-place expansion; intro payload ~161 KB transferred with zero media/JSON requests;
  deploy package 20.04 MB ≤ 21 MB. Default `npm run build` output verified
  **byte-identical** to the pre-change baseline (Tailwind content scan scoped per D11).
- **D11** `tailwind.config.ts` content globs were narrowed to the application's own
  directories (equivalent scan set) so portfolio-demo classes cannot alter the default
  build; the portfolio build uses `tailwind.portfolio-demo.config.ts`. Byte-identity of
  the default dist proves output-equivalence.
- **D12** Correction pass 1 (evidence-driven): scene-list Off chip moved below the text
  (row no longer wraps); Fit-to-gap no longer counts as a hand edit (summary truthful;
  renames also preserve fitted lines; the fit step's done-condition now checks that scene
  5 genuinely fits); intro facts became two deliberate rows (no orphaned separator);
  source video got its fixture poster; "Switch this line off" uses the outline button;
  the walkthrough card remounts per step instead of animating across the editor.
- **D13** Exact counts in copy corrected (scene 2 draft is 45 words).

- **D14** Correction pass 2 (independent read-only critique, all evidenced findings):
  dialogue-onset order corrected everywhere (the narration starts 0.15 s *before* the
  dialogue — copy no longer claims the reverse); "Fit to gap" is disabled when a trial trim
  provably cannot clear a dialogue collision (scene 2), with an honest tooltip; the demo now
  uses one timing model everywhere (0.4 s/word — the fit budget no longer disagrees with the
  estimate); undefined Tailwind tokens replaced and all retained-state text raised to AA
  contrast (danger-800/success-800/neutral-500); embed mode gained explicit
  "Close demo"/"Return" semantics (postMessage `instascribe-live-onboarding:exit` + return
  to the intro state) and the completion card now offers Restart and Close/Back-to-intro;
  the text walkthrough is reachable from the editor (About dialog) in every mode; described
  captions now use measured per-clip durations instead of the word heuristic; unused public
  assets (silence.mp3, icons.svg, poster.avif, system_info.json) are pruned from the deploy
  package; the deploy manifest is now genuinely verified (UPDATE_MANIFEST=1 to refresh);
  the forbidden-string audit covers every contract-named route; e2e gained the
  1232/1120/1024 ladder, a layout-shift assertion, and screen-aware fallback-link tests;
  the walkthrough card sits beside full-height spotlight targets instead of covering them;
  the tour ring uses the website's light-surface indigo #2e2edc; the intro CTA border was
  strengthened and "the real InstaScribe editor" softened to "InstaScribe's review
  workflow"; scene-5 copy quotes the 44-word draft and attributes all durations to the
  estimate model.

- **D15 (correction pass) — Dialogue authority.** The committed word-timestamped
  `transcript.json` is now the single user-facing spoken-dialogue authority (7 utterances:
  26.22–26.78 "Oh", 26.78–28.86 "Hey, it's almost done", 30.00–32.12 "Hey sit still",
  47.56–49.06 "Good night's kiss", 70.32–71.72 "Oh", 73.48–74.88 "Skills", 108.02–109.42
  "Oh"). The coarse `audio_events.json` (silence 32–120 s) is provenance only — no longer
  fetched and pruned from the deploy. Corrected arithmetic (pinned by
  `lib/dialogueAuthority.test.ts`): scene 2 begins 0.03 s AFTER the dialogue ("Oh" at
  26.22 vs narration 26.25 — the earlier "0.15 s after" claim had the wrong source and the
  wrong direction), overlap ≈ 4.73 s across three utterances, untrimmable at every offered
  speed; scene 5 overruns its 8.0 s gap into "Oh"/"Skills" (≈ 2.8 s) and the trim clears
  both the overrun and the collision at 0.75/1/1.25/1.5×; at 1× scenes 2,3,4,5,6,8,9
  collide and 1,7 are clear. All copy, the timeline bands, the Checks panel and the
  walkthrough tell this one story.
- **D16 — Evidence-based LISTEN.** Baked audio completes on the media 'playing' event,
  browser speech on `onstart`, and the described film only when its video actually enters
  playback; the completion card claims "you heard…" only with that evidence. Playwright
  starts real playback and asserts the state transition.
- **D17 — Single audio owner.** `lib/audioBus.ts`: source video, baked line, speech and the
  described example claim a single bus; opening any modal surface (walkthrough explanation
  step, listen/about dialog), changing scene, restart, exit, tour completion and unmount
  stop all sources. Unit lifecycle tests + browser regressions (no orphaned "Stop" state).
- **D18 — Speed-consistent trim.** `fitToGap(text, target, speed)` floors
  `target × 2.5 × speed` words (round() overshot at 0.75×: 13 words → 6.93 s > 6.88 s);
  eligibility, budget, note, collision trial and step completion share the one model;
  tested at all four speeds (browser + unit), fixing the 0.75× dead end.
- **D19 — Local-only speech.** Speech uses only an explicitly selected
  `SpeechSynthesisVoice` with `localService === true` (async `voiceschanged` handled,
  bounded wait); with no local voice the control is withheld with honest copy. Public
  wording is now "no live model or API calls". Unit-tested selection; the rule is asserted
  separately from network observation since speech services don't surface as page requests.
- **D20 — Accessibility.** All flagged sub-AA tokens replaced (status pills →
  `*-800` on `*-50`; remaining `neutral-400` body text → `neutral-500/600`; the demo
  darkens `--primary` to hsl(158 74% 26%) ≈ #117350 so white button labels reach ≈5.8:1 —
  demo-scoped, shared tokens untouched); `<main>` landmark on the editor; action steps
  announce title+rationale+instruction via a polite live region before focus moves (450 ms)
  and the target carries the card body as `aria-describedby`; axe (wcag2a/aa + 2.1) scans
  seven retained states and fails CI on any violation.
- **D21 — Recovery & terminal UX.** "Try again" genuinely refetches (retry nonce — the old
  effect never re-ran); narrow standalone fallback gained "Back to the intro", embedded
  narrow gained "Close demo"; the completion card is a terminal model: primary "Explore the
  editor" + secondary "Restart demo", no Skip/Back. Sentence-initial casing is corrected at
  the DISPLAY layer only (`lib/text.ts`, applied on fixture load and after rename
  re-render); the committed fixture is byte-unchanged.
- **D22 — Embed message contract.** The exit event posts only to
  `https://andriiartemenko.com` and `https://www.andriiartemenko.com` (plus the dev-server
  origin in dev builds only) — never `'*'` — and carries nothing but its type; the parent
  must verify `event.origin` AND `event.source` (documented with a snippet). Unit-tested.
- **D23 — Hosting truth.** A real top-level `404.html` establishes the host 404 boundary
  (its presence disables Pages' implicit SPA fallback); `scripts/serve-host.mjs` implements
  the documented `_redirects`/`_headers`/404 semantics (with Range support) and now serves
  ALL browser tests and previews; `e2e/host.spec.ts` asserts genuine statuses (app routes
  404, `/onboarding` 200 via the single rewrite, pruned assets 404, dev-harness route
  absent) and the exact security headers, including immutable/day cache tiers.
  `Permissions-Policy` now denies autoplay entirely (the demo never autoplays).
  *Limitation:* in-browser `frame-ancestors` blocking was attempted three ways; Chromium
  does not expose blocked-subframe telemetry to Playwright deterministically, so the
  header's exact value is asserted on every route and enforcement is the browser's
  standard behavior. The real Cloudflare host remains the final authority and is
  deliberately not deployed by this branch.
- **D24 — CI.** A dedicated `portfolio-demo` job (npm ci → Playwright Chromium →
  `build:portfolio-demo` → `verify:portfolio-demo` → full browser suite incl. host and axe
  specs) runs on the fresh artifact; `reuseExistingServer` is disabled under CI. The
  browser suite has GLOBAL network/console/pageerror observation (fails on any external
  origin, `/api/`, page error or unexpected console error). The deploy manifest is now
  genuinely verified against the committed copy (refresh requires `UPDATE_MANIFEST=1`).

*(Further decisions/deviations appended as work proceeds.)*
