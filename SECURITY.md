# Security policy

## Supported code

Security fixes target the latest commit on the default branch and the current beta
line. Historical Cloud Core releases are retained as engineering evidence and are
not promised ongoing support.

## Report a vulnerability privately

Do not open a public issue for a suspected vulnerability. Use GitHub's
[private vulnerability reporting](https://github.com/AndriiArtemenko3/instadescribe/security/advisories/new)
for this repository.

Include, when safe:

- the affected component and revision;
- reproduction steps or a minimal proof of concept;
- expected impact and required preconditions;
- whether credentials, tenant data or media may have been exposed.

Do not include live credentials, customer media or personal data. Revoke or rotate
any credential you control before sending a report if exposure is suspected.

Receipt, remediation or disclosure timelines are not guaranteed during beta. Please
allow the maintainer to investigate and coordinate disclosure before publishing
details.

## Security boundary

The API-first beta is implemented and locally verified. The static legacy Cloud
Core v0.1 frontend remains published, but its API/readiness is unavailable as of
`2026-08-29`. The repository does not claim production readiness, an SLA or
customer-data certification. Security issues in either the current source or the
referenced legacy deployment are still welcome through the private channel above.
