# SDK and CLI npm release

The repository never publishes from CI on a push or pull request. The
`Publish SDK and CLI` workflow is manual, environment-gated, and publishes the
SDK before the CLI because the CLI declares an exact SDK version.

## One-time registry setup

Configure npm trusted publishing independently for both packages:

- `@instadescribe/sdk`
- `instadescribe`

Each trusted-publisher record must identify this GitHub repository,
`.github/workflows/publish-npm.yml`, and the `npm-publish` GitHub Environment.
Protect that Environment with the required human reviewers. Do not add an npm
automation token or `NODE_AUTH_TOKEN`; the workflow requests a short-lived OIDC
identity and publishes with provenance.

## Beta release

1. Review and authorize the exact release commit/tag. Confirm that both
   package manifests and the CLI's exact SDK dependency carry the intended
   prerelease version.
2. Run the published-tarball E2E against the beta API and record the result.
3. Dispatch `Publish SDK and CLI` from that immutable ref with:
   `expected_version=1.0.0-beta.1`, `dist_tag=next`, and
   `customer_canary_complete=false`.
4. Approve the protected `npm-publish` Environment only after the workflow's
   contract, test, build, and package checks pass.
5. Verify both registry entries, provenance attestations, package contents,
   CLI version, SDK ESM imports, and the `next` dist-tag from a clean consumer.

## Stable release

Stable publication is deliberately stricter. Complete the documented customer
canary first, remove the prerelease suffix from both packages, update the CLI's
exact SDK dependency, and dispatch with `dist_tag=latest` and
`customer_canary_complete=true`. The workflow rejects `latest` for a
prerelease or without the explicit canary assertion.

## Failure handling

If the SDK publishes but the CLI step fails, do not republish or change the SDK
artifact. Correct the CLI/release configuration, confirm that its exact SDK
version is already present, and rerun the same reviewed ref. If a published
package is defective, prefer an explicit npm deprecation plus a corrected new
version; do not silently replace, unpublish, or mutate an existing release.

Running this workflow is an external publication and always requires explicit
release authorization. Repository implementation or passing local tests alone
is not authorization to dispatch it.
