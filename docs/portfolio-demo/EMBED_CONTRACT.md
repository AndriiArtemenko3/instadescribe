# Figure 00 embed contract & static-host package

Prepared for a future origin such as `https://instascribe-demo.andriiartemenko.com`.
**Nothing is deployed by this branch**: no Cloudflare project, DNS, custom domain, or
production change. The portfolio repository is untouched.

## Routes

| Route | Purpose |
| --- | --- |
| `/` | Standalone intro (the Figure 00 invitation state). H1, truthful preface, `Start now`, visible Sintel attribution. |
| `/onboarding` | Direct seeded onboarding (deterministic; in-memory state only). |
| `/onboarding?embed=1` | **Direct embedded entry** — autostarts the walkthrough without repeating the intro; Restart resets in place; "Close demo" (header and completion card) posts `{ type: 'instascribe-live-onboarding:exit' }` to the parent and returns the frame to the intro invitation state. |
| `/onboarding?view=text` | Full text walkthrough + transcript + deferred listening example. |
| `/review/figure-00` | Development-only harness (excluded from production builds). |

SPA fallback: only `/onboarding` rewrites to `index.html` (`_redirects`). `/` is the real
file. A top-level **`404.html`** ships in the package: its presence disables Cloudflare
Pages' implicit everything-is-SPA fallback, so `/login`, `/register`, `/dashboard`,
`/upload`, `/study`, `/tutorials` and any other unknown path return a **genuine 404
status** with a branded not-found page — no open redirect, no backend fall-through.
`scripts/serve-host.mjs` implements exactly these documented semantics locally (plus
`_headers` application and Range support) and serves every browser test and preview;
`e2e/host.spec.ts` asserts the statuses and headers. The deployed Cloudflare host remains
the final authority — verify once more there when it is eventually deployed.

## The intended portfolio integration

1. The case-study page renders Figure 00's intro natively (or as this demo's `/`).
2. On **Start now**, the portfolio creates the iframe pointing at the direct embedded
   entry — the intro is not repeated:

```html
<iframe
  src="https://instascribe-demo.andriiartemenko.com/onboarding?embed=1"
  title="InstaScribe — live onboarding (interactive product walkthrough)"
  width="1232" height="693"
  loading="lazy"
  referrerpolicy="no-referrer"
  sandbox="allow-scripts allow-same-origin allow-popups"
></iframe>
```

- **Minimum size:** 1024 × 640 CSS px (measured eligibility boundary: the three-panel
  editor keeps a ~500 px center workspace at 1024; verified in browser evidence). The
  approved stage is 1232 × 693 (true 16:9). The editor fits 693 px height — no in-place
  expansion was needed.
- **`sandbox`:** `allow-scripts allow-same-origin` are required (same-origin lets the SPA
  read its own URL/history; no cookies or storage are used). `allow-popups` only if the
  narrow-fallback "Open the full demo in a new tab" escape should work; drop it otherwise.
- **`allow`:** none required. The demo never autoplays with sound — all audio/video starts
  from a click inside the frame, which the default policies permit. Add
  `allow="fullscreen"` only if native video fullscreen inside the iframe is wanted.
- **Referrer policy:** `no-referrer` (the host also sends `Referrer-Policy: no-referrer`).
- **Loading:** create the iframe only after the CTA (plus `loading="lazy"` as a backstop);
  the demo's own intro payload is ~161 KB transferred, and it fetches no media before the
  visitor starts.
- **Intro→editor transition:** inside the frame, `Start now` swaps intro → editor in the
  same region with no layout shift (both fill the frame; asserted by e2e).
- **Close / return-to-case-study:** the demo's "Close demo" control (header; the
  completion card offers Restart, and narrow embedded fallbacks offer Close) sends
  `{ type: 'instascribe-live-onboarding:exit' }` via `postMessage` **only to the explicit
  allowed parent origins** — `https://andriiartemenko.com` and
  `https://www.andriiartemenko.com` (dev builds additionally target the dev-server's own
  origin; production builds strip that branch). It is never posted to `'*'` and carries no
  user data (unit-tested in `lib/embed.test.ts`). The frame then navigates itself back to
  `/` (the invitation state), so a parent that ignores the message still shows a coherent
  stage. The parent MUST verify both the origin and the source before acting:

  ```js
  window.addEventListener('message', (event) => {
    if (event.origin !== 'https://instascribe-demo.andriiartemenko.com') return
    if (event.source !== iframe.contentWindow) return
    if (event.data?.type !== 'instascribe-live-onboarding:exit') return
    // swap the iframe back to the static Figure 00 state
  })
  ```
- **Text alternative:** the full text walkthrough is reachable in every mode — from the
  intro ("Read it as text instead") and from the editor's About & licensing dialog
  (`/onboarding?embed=1&view=text` inside the embed).
- **Below the boundary** (frame narrower than 1024 px): the demo itself renders the
  coherent text fallback with the full walkthrough, transcript, deferred listening
  example, and (embed only) an "Open the full demo in a new tab" link — it never renders a
  crushed editor and never horizontally scrolls the host page.
- **Failure state:** if the demo's static files fail to load, it shows an honest in-frame
  error with a Try-again action (never a blank panel, never a backend call). If the iframe
  itself cannot load, the parent's static Figure 00 poster/caption should remain.

## Host security package (`App/portfolio-demo-host/`, copied into the build)

- `_headers` — `X-Robots-Tag: noindex, nofollow, noarchive`; `X-Content-Type-Options:
  nosniff`; `Referrer-Policy: no-referrer`; Permissions-Policy denying every capability
  the demo doesn't need (camera, mic, geolocation, payment, USB, …; `autoplay=()` — the demo never autoplays,
  `fullscreen=(self)`); CSP `default-src 'none'` with self-only script/style/img/media/
  font/connect (`style-src 'unsafe-inline'` is required by React inline style attributes —
  documented tradeoff), `base-uri 'none'`, `form-action 'none'`, and
  `frame-ancestors https://andriiartemenko.com https://www.andriiartemenko.com`.
  **No `X-Frame-Options`** — it would block the intended cross-origin portfolio iframe;
  `frame-ancestors` is the modern equivalent that permits exactly the portfolio.
  Documented preview origins: none in production headers; for local previews
  (`vite preview` on `localhost:4174`, dev server on `localhost:5175`) no CSP is served,
  so local testing is unaffected.
- `_redirects` — the single SPA rewrite above.
- `robots.txt` — `Disallow: /` for all agents.
- Caching: hashed `/assets/*` immutable for 1 year; `/videos/*` and `/data/*` 24 h;
  everything else 5 min.
