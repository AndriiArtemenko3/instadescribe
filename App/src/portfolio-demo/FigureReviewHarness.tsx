// DEVELOPMENT-ONLY review harness (excluded from production builds via the
// DEV-gated route in DemoRoot). Approximates the portfolio case-study page
// around Figure 00 — light ground, 1232px editorial track, mono evidence
// label/caption — so the intro/editor can be judged at exact case-study scale
// WITHOUT touching the read-only website repository. Not a portfolio page and
// never deployed.

const S: Record<string, React.CSSProperties> = {
  page: {
    minHeight: '100vh',
    background: '#f6f7f9',
    color: '#16181c',
    fontFamily: "'Söhne', 'Helvetica Neue', Arial, sans-serif",
    padding: '64px 24px 120px',
  },
  track: { maxWidth: 1232, margin: '0 auto' },
  notice: {
    margin: '0 0 48px',
    padding: '8px 12px',
    border: '1px dashed #7f848c',
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
    fontSize: '0.8125rem',
    letterSpacing: '0.08em',
    color: '#585c63',
    display: 'inline-block',
  },
  label: {
    margin: '0 0 8px',
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
    fontSize: '0.8125rem',
    letterSpacing: '0.06em',
    color: '#585c63',
  },
  stage: {
    width: '100%',
    aspectRatio: '16 / 9',
    background: '#16181c',
    border: '1px solid #ccd2da',
    display: 'block',
  },
  proves: {
    margin: '12px 0 0',
    fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
    fontSize: '0.8125rem',
    lineHeight: 1.5,
    color: '#61666d',
  },
  caption: {
    marginTop: '8px',
    fontSize: '0.875rem',
    lineHeight: 1.35,
    color: '#61666d',
  },
}

export default function FigureReviewHarness() {
  return (
    <main style={S.page}>
      <div style={S.track}>
        <p style={S.notice} role="note">
          DEV REVIEW HARNESS · FIGURE 00 PLACEMENT · NOT A PORTFOLIO PAGE
        </p>
        <p style={S.label}>FIGURE 00 / SYSTEM OVERVIEW</p>
        <iframe
          src="/"
          title="InstaScribe — live onboarding (interactive product walkthrough)"
          style={S.stage}
        />
        <p style={S.proves}>
          The Figure 00 stage carries the live onboarding: the intro invites “Try InstaScribe
          Live Onboarding”, and Start now loads the full walkthrough in this same region.
        </p>
        <p style={S.caption}>
          1232 × 693 (true 16:9), near-black #16181C field, 1px keyline, square and shadowless
          — matching the approved case-study shell.
        </p>
      </div>
    </main>
  )
}
