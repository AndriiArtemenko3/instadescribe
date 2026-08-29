# InstaDescribe Web App

The web workspace contains the authenticated Next.js App Router application and the
Vite editor/demo build retained as the beta rollback surface.

## Responsibilities

- login, invitation, password recovery and MFA routes;
- opaque HttpOnly browser sessions backed by encrypted server-side Cognito tokens;
- same-origin JSON BFF requests to the FastAPI Browser API;
- direct browser-to-S3 upload and signed-media download;
- project, upload and human-review interaction;
- deterministic fixture demo with no credentials or provider calls.

FastAPI remains responsible for JWT validation, membership and role enforcement,
tenant isolation, state transitions and review/render rules. Next.js never receives
service-account API keys, and media bytes do not proxy through Next.

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

The beta cutover is not yet deployed. Vite remains the rollback build until the
browser/media parity gate passes. See the repository
[architecture](../docs/architecture.md) and [BFF boundary](./NEXT_BFF.md).

## License

The Web App is part of the BUSL-1.1-licensed product core. See
[LICENSING.md](../LICENSING.md).
