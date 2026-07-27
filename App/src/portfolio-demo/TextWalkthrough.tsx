import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  loadDemoData,
  loadTranscript,
  EXPORT_SRC,
  DESCRIBED_CAPTIONS_SRC,
  type DemoData,
  type TranscriptUtterance,
} from './lib/fixtures'

function formatTime(secs: number): string {
  const m = Math.floor(secs / 60)
  const s = Math.floor(secs % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

function screenCanHostEditor(eligibleWidth: number): boolean {
  return typeof window !== 'undefined' && window.screen.width >= eligibleWidth
}

interface TextWalkthroughProps {
  /** True when rendered because the viewport is too narrow for the editor. */
  narrow: boolean
  embed: boolean
  eligibleWidth: number
}

/**
 * The complete text version of the walkthrough: the same five stages, the
 * film's dialogue transcript, the drafted narration lines, and the
 * pre-rendered listening example (loaded only on request). Serves both as the
 * narrow-viewport fallback and as the text alternative linked from the intro.
 */
export default function TextWalkthrough({ narrow, embed, eligibleWidth }: TextWalkthroughProps) {
  const [data, setData] = useState<DemoData | null>(null)
  const [transcript, setTranscript] = useState<TranscriptUtterance[]>([])
  const [loadError, setLoadError] = useState(false)
  const [showExample, setShowExample] = useState(false)

  useEffect(() => {
    let cancelled = false
    Promise.all([loadDemoData(), loadTranscript()])
      .then(([d, t]) => {
        if (cancelled) return
        setData(d)
        setTranscript(t)
      })
      .catch(() => {
        if (!cancelled) setLoadError(true)
      })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <main className="pd-fallback" aria-labelledby="pd-text-title">
      <div className="pd-fallback-inner">
        <p className="pd-kicker">INSTASCRIBE · LIVE ONBOARDING — TEXT VERSION</p>
        <h1 id="pd-text-title" className="pd-title" style={{ fontSize: '1.45rem' }}>
          {narrow ? 'The interactive editor needs a wider display' : 'The walkthrough, as text'}
        </h1>
        {narrow && (
          <p className="pd-note">
            InstaScribe's editor is a three-panel desktop workspace; below {eligibleWidth}px
            wide it can't offer a meaningful editing surface, so this page gives you the whole
            walkthrough as text plus the final listening example instead.
            {embed && screenCanHostEditor(eligibleWidth) && (
              <>
                {' '}
                <a className="pd-link" href="/" target="_blank" rel="noreferrer noopener">
                  Open the full demo in a new tab
                </a>
                .
              </>
            )}
          </p>
        )}
        {!narrow && (
          <p>
            Prefer the interactive version?{' '}
            <Link className="pd-link" to={embed ? '/onboarding?embed=1' : '/'}>
              {embed ? 'Back to the interactive demo' : 'Back to the intro'}
            </Link>
            .
          </p>
        )}

        <h2>What audio description is</h2>
        <p>
          Audio description is spoken narration that makes film accessible to blind and
          low-vision audiences — it describes what happens on screen, placed in the pauses
          between dialogue. InstaScribe drafts those lines from the video, then keeps a person
          in charge of every editorial decision.
        </p>

        <h2>1 · Orient — the material</h2>
        <p>
          The demo loads a ~2-minute excerpt of <em>Sintel</em> with nine drafted narration
          lines, a dialogue map, and four recommended silence gaps. The film's spoken dialogue,
          from the committed transcript:
        </p>
        {loadError ? (
          <p className="pd-note">The static fixtures could not be loaded — try reloading.</p>
        ) : (
          <ul>
            {transcript.map((u, i) => (
              <li key={i}>
                <span style={{ fontFamily: 'var(--pd-font-mono)', fontSize: '0.8125rem' }}>
                  {formatTime(u.start)}
                </span>{' '}
                — “{u.text}”
              </li>
            ))}
          </ul>
        )}

        <h2>2 · Identify — a real conflict</h2>
        <p>
          Scene 2's draft is 45 words — an estimated 18 seconds spoken — inside a 13-second
          scene, and the film's dialogue starts just 0.15 seconds after the line begins.
          Narration would talk over dialogue for about 3 seconds. That collision is real,
          computed from the film's dialogue map.
        </p>

        <h2>3 · Refine — two honest fixes</h2>
        <p>
          Scene 2 can't be rescued by shortening (the dialogue starts almost the moment the
          line begins), so the
          editorial fix is to switch that line off and let the dialogue carry the moment.
          Scene 5's draft simply runs long — its moment offers 8 seconds of clear silence, so
          a local, deterministic trim ("Fit to gap") cuts it to size. No AI is involved in
          either fix.
        </p>

        <h2>4 · Listen — honestly labeled</h2>
        <p>
          In the editor you can hear each line two ways: the pre-generated narration of the
          original draft (voice “Onyx”, rendered earlier by the project's pipeline), or your
          browser's own voice reading the current text. The final described example below was
          pre-rendered by the full pipeline from the original drafts — edits made in the demo
          are not in it.
        </p>
        {showExample ? (
          <video
            controls
            preload="metadata"
            aria-label="Sintel excerpt with audio description narration mixed in (pre-rendered)"
          >
            <source src={EXPORT_SRC} type="video/mp4" />
            <track
              kind="captions"
              src={DESCRIBED_CAPTIONS_SRC}
              srcLang="en"
              label="Dialogue + narration (English)"
            />
          </video>
        ) : (
          <p>
            <button type="button" className="pd-link" onClick={() => setShowExample(true)}>
              Load the described example (≈8.5 MB video)
            </button>
          </p>
        )}

        <h2>5 · Complete</h2>
        <p>
          That's the loop a describer runs all day: review the draft, resolve timing against
          dialogue, listen, move on. In the full application, narration re-renders per edit
          and the finished track exports with the film.
        </p>

        {data && (
          <>
            <h2>The nine drafted lines</h2>
            <ol>
              {data.scenes.map((s) => (
                <li key={s.id}>
                  <span style={{ fontFamily: 'var(--pd-font-mono)', fontSize: '0.8125rem' }}>
                    {formatTime(s.startSecs)}–{formatTime(s.endSecs)}
                  </span>{' '}
                  {s.text}
                </li>
              ))}
            </ol>
          </>
        )}

        <p className="pd-fineprint">
          Film: <em>Sintel</em> — © Blender Foundation ·{' '}
          <a href="https://durian.blender.org" target="_blank" rel="noreferrer noopener">
            durian.blender.org
          </a>{' '}
          ·{' '}
          <a
            href="https://creativecommons.org/licenses/by/3.0/"
            target="_blank"
            rel="noreferrer noopener"
          >
            CC BY 3.0
          </a>
          . Excerpt adapted with an audio-description track. This page makes no request beyond
          its own bundled files.
        </p>
      </div>
    </main>
  )
}
