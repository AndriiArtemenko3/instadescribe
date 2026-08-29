# Third-party notices

This file records third-party material distributed in, or directly represented by,
the repository. It is not a substitute for the license metadata shipped by each
dependency.

## Sintel demonstration fixture

The committed demonstration uses a segment from **Sintel**:

- Copyright © Blender Foundation, [durian.blender.org](https://durian.blender.org/).
- Licensed under [Creative Commons Attribution 3.0](https://creativecommons.org/licenses/by/3.0/).
- The source segment is stored under `App/public/videos/`.
- Posters, preview media and audio-description fixture data under
  `App/public/data/sintel-blender-cc/` are derived from or describe that source and
  retain the attribution above.
- The recruiter-facing fixture captures under `docs/assets/` display a frame from
  that committed source inside the InstaDescribe editor and retain the same
  attribution.

## Software dependencies

Python and Node.js dependencies are identified in the repository's requirements,
package manifests and lockfiles. They remain subject to their own license terms;
neither the root BUSL-1.1 license nor the nested MIT licenses replace those terms.
Generated dependency directories such as `node_modules/` and `.venv/` are not part
of the source distribution.

FFmpeg and other operating-system packages are installed into development or
container environments rather than copied into this source tree. Model weights are
not bundled in the repository. Before any container image or model bundle is
distributed publicly, its release gate must generate an SBOM and verify the required
binary, shared-library, font, codec and model notices for that exact artifact.

## shadcn/ui component sources

`App/components.json` and these component sources were generated from or adapted
from [shadcn/ui](https://ui.shadcn.com/):

- `App/src/components/ui/avatar.tsx`
- `App/src/components/ui/breadcrumb.tsx`
- `App/src/components/ui/button.tsx`
- `App/src/components/ui/card.tsx`
- `App/src/components/ui/collapsible.tsx`
- `App/src/components/ui/dropdown-menu.tsx`
- `App/src/components/ui/input.tsx`
- `App/src/components/ui/label.tsx`
- `App/src/components/ui/separator.tsx`
- `App/src/components/ui/sheet.tsx`
- `App/src/components/ui/sidebar.tsx`
- `App/src/components/ui/skeleton.tsx`
- `App/src/components/ui/tooltip.tsx`

Only those listed files remain available under the following MIT terms and are
not relicensed under the repository's root BUSL-1.1 license. In particular,
`App/src/components/ui/Logo.tsx` is InstaDescribe-authored and remains within the
BUSL-1.1 core boundary.

```text
MIT License

Copyright (c) 2023 shadcn

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## InstaDescribe-authored code

The product core is licensed under [BUSL-1.1](./LICENSE). The TypeScript
[SDK](./packages/sdk/LICENSE) and [CLI](./packages/cli/LICENSE) are separately
licensed under MIT. See [LICENSING.md](./LICENSING.md) for the complete boundary.
