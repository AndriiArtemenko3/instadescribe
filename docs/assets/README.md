# Public image assets

`investigation-workspace.png` is a deterministic full-page desktop Chromium capture
of the authenticated investigation workspace fixture. All identifiers,
observations and the `example.test` analyst identity are synthetic. The screen
states `Deterministic fixture · no model inference` and `Metadata overlay only · no
source pixels are returned by this API.` It is evidence of the committed UI and
strict Browser contract only—not a model, retrieval, geolocation-quality or
source-frame claim. The captured machine-abstention constraint also locks final-candidate
selection; it is not an inferred location result.

The capture deliberately renders both a synthetic `Proposed observation` and a
synthetic `Verified by tool` state so the distinction is reviewable. The worker
acceptance fixture still persists its generated observations as `proposed`; this UI
fixture does not claim that its generated metadata was independently verified.

`instadescribe-product-capture.png` is a viewport capture of the committed keyless
Vite demo after opening the `Edit a short film` Sintel tutorial. It contains only
committed fixture text/media and no user account, email, customer, service key or
real job history.

`instadescribe-social-preview.png` is a separate, exact 1280×640 browser capture
of that same real fixture editor. It is intended for the GitHub repository social
preview.

The source media is Sintel © Blender Foundation under CC BY 3.0. See
[THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md).

Capture procedure:

1. `npm ci`
2. `npm run demo -w App`
3. open `/tutorials` and select `Edit a short film`
4. capture the 1280×720 editor viewport
5. capture the same fixture editor at exactly 1280×640 for the social preview

Regenerate both assets after any material editor or brand change; do not substitute
screenshots containing real identities or cloud job history.

Investigation-workspace capture procedure:

1. `npm ci`
2. `npm run build:next -w App`
3. `UPDATE_PUBLIC_CAPTURE=1 npm run test:e2e -w App -- --project=chromium --grep "deterministic workspace"`
4. inspect the generated PNG and rerun the full desktop/mobile Playwright suite

Regenerate it after a material investigation-layout or fixture-contract change.
Never replace the synthetic route fixture with a screenshot from a real account,
real footage or a deployed environment.
