# Contributing to InstaDescribe

Thank you for taking the time to improve InstaDescribe.

## Beta contribution policy

Issues, reproducible bug reports, architecture feedback and private security reports
are welcome. External pull requests are not accepted during the beta. This keeps the
source-available core's copyright and relicensing boundary unambiguous while the
public API is stabilizing.

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

- the API-first beta is implemented and locally verified, not yet deployed;
- the static legacy Cloud Core v0.1 frontend remains published, but API/readiness
  is unavailable as of `2026-08-29`;
- the SDK and CLI are source-complete but not yet published to npm;
- the project is designed to support audio-description workflows and does not claim
  legal WCAG compliance, production readiness, live customers, billing or an SLA.

## Licensing

The core is source-available under BUSL-1.1. The SDK and CLI are separately MIT.
Opening an issue or sharing feedback does not grant additional rights. See
[LICENSING.md](./LICENSING.md) for the exact boundary.
