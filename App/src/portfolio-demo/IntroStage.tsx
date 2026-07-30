import { useNavigate } from 'react-router-dom'

/**
 * The Figure 00 intro. Fills its viewport (the portfolio embeds it at
 * 1232 × 693), sits on the case-study shell's near-black media field, and is
 * the invitation into the walkthrough. Everything stated here is true of the
 * artifact: preloaded film, committed fixtures, no backend or model calls,
 * pre-rendered final example.
 */
export function IntroStage() {
  const navigate = useNavigate()
  return (
    <main className="pd-intro" aria-labelledby="pd-intro-title">
      <div className="pd-intro-inner">
        <p className="pd-kicker">INSTASCRIBE · INTERACTIVE PRODUCT WALKTHROUGH</p>
        <h1 id="pd-intro-title" className="pd-title">
          Try InstaScribe Live Onboarding
        </h1>
        <p className="pd-support">
          Audio description is spoken narration that makes film accessible to blind and
          low-vision audiences. Step inside InstaScribe's review workflow, preloaded with one
          short film — review its drafted narration and fix a genuine timing problem. Then
          listen: your current text can be read aloud locally where your browser has an
          on-device voice, and a separate pre-rendered pipeline example plays the original
          drafts with the film.
        </p>
        <div aria-label="What to expect" role="group">
          <ul className="pd-facts">
            <li>3–4 minutes</li>
            <li>runs entirely in your browser</li>
          </ul>
          <ul className="pd-facts">
            <li>no upload, no account, no live model or API calls</li>
            <li>final example pre-rendered</li>
          </ul>
        </div>
        <div className="pd-actions">
          <button type="button" className="pd-start" onClick={() => navigate('/onboarding')}>
            Start now
          </button>
          <button
            type="button"
            className="pd-link"
            onClick={() => navigate('/onboarding?view=text')}
          >
            Read it as text instead
          </button>
        </div>
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
          . A ~2-minute excerpt, adapted with an audio-description track.
        </p>
      </div>
    </main>
  )
}
