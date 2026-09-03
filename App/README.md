# InstaDescribe Web App

The web workspace contains the authenticated Next.js App Router application for
local video investigations and the Vite audio-description editor/demo retained as a
legacy rollback surface.

## Responsibilities

- login, invitation, password recovery and MFA routes;
- opaque HttpOnly browser sessions backed by encrypted server-side Cognito tokens;
- same-origin JSON BFF requests to the FastAPI Browser API;
- direct browser-to-S3 upload and signed-media download;
- investigation list/create, evidence workspace, abstention/finalization and report
  routes;
- role-aware Owner/Editor/Reviewer/Viewer presentation controls;
- source-lineage, keyframe-metadata, evidence-state, uncalibrated posterior, entropy
  and objective-tool-ledger views;
- legacy audio-description project, upload and human-review interaction;
- deterministic fixture browser coverage with no model or public-web call.

FastAPI remains responsible for JWT validation, membership and role enforcement,
tenant isolation, state transitions and review/render rules. Next.js never receives
service-account API keys, and media bytes do not proxy through Next.

The primary navigation links to `/investigations`, `/investigations/new` and
`/account`. Audio description remains reachable at `/legacy/audio-description` but
is deliberately absent from the primary navigation. Investigation creation remains
limited to `geolocateProvenance + local`; future modes have no interactive control
and are named only as unavailable milestones.

## Local commands

Run from the repository root after `npm ci`:

```bash
npm run dev:next -w App       # Next.js development server
npm run build:next -w App     # Next.js production build
npm run demo -w App           # keyless Vite fixture demo
npm test -w App               # component/unit tests
npm run test:e2e -w App       # Playwright browser journey
npm run lint -w App
```

The Playwright suite runs the legacy parity journey on desktop Chromium and the
investigation fixture on both desktop and mobile Chromium profiles. The fixture is
synthetic, PII-free and visibly marked `Deterministic fixture · no model inference`.
It intercepts the representative direct upload and denies unexpected external
requests; it is UI/contract evidence, not a model-quality result.

For investigation runs, `local` forbids public-internet retrieval during analysis;
authenticated same-origin BFF calls and the direct private-storage upload remain
transport paths. Nonterminal workspaces poll with a bounded delay, and a latest
machine belief that abstained locks candidate selection so analyst finalization must
preserve abstention.

The beta cutover is not yet deployed. Vite remains the rollback build until the
browser/media parity gate passes. See the repository
[architecture](../docs/architecture.md) and [BFF boundary](./NEXT_BFF.md).

## License

The Web App is part of the BUSL-1.1-licensed product core. See
[LICENSING.md](../LICENSING.md).
