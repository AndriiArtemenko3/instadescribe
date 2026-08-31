# Contributing to InstaDescribe

Thank you for taking the time to improve InstaDescribe.

## Contribution policy

Issues, reproducible bug reports, architecture feedback and private security reports
are welcome. External pull requests are not accepted during the beta. This keeps the
BUSL product shell's copyright and relicensing boundary unambiguous while the public
API is stabilizing.

The repository is mixed-license: the autonomous investigation core is Apache-2.0,
the SDK/CLI are MIT, and the product shell is BUSL-1.1. The current issue-first
policy applies across those areas; a nested open-source license does not by itself
change the maintainer's pull-request intake policy.

Please open an issue before doing implementation work; unsolicited pull requests may
be closed without review. The maintainer may revise this policy after the beta.

## Open an issue

Use the matching issue form and provide:

- the affected revision, operating system and runtime versions;
- a minimal reproduction using synthetic or rights-cleared fixtures;
- the expected and actual behavior;
- sanitized logs with tokens, signed URLs, media and personal data removed.

Use [private vulnerability reporting](./SECURITY.md) instead of an issue for security
findings. Commercial-license questions have a dedicated public issue form; include
only non-confidential context.

## Product claims

Keep documentation precise:

- the video-investigation foundation is implemented and fixture-verified, with no
  model inference or public-web request in its end-to-end proof;
- the authenticated investigation workspace is implemented and browser-verified
  with a synthetic, PII-free no-model fixture;
- live model inference, retrieval, persisted replay, benchmark and deployment are
  not implemented capabilities;
- the audio-description API-first beta remains implemented and locally verified,
  not evidence that the investigation product is deployed;
- the SDK and CLI are source-complete but not yet published to npm;
- the project does not claim investigation accuracy, legal WCAG compliance,
  production readiness, live customers, billing or an SLA.

## Licensing

The product shell is source-available under BUSL-1.1, the investigation baseline is
Apache-2.0, and the SDK/CLI are MIT. Opening an issue or sharing feedback does not
grant additional rights. See
[LICENSING.md](./LICENSING.md) for the exact boundary.
