# Public image assets

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
