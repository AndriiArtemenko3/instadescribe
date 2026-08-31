# Security policy

## Supported code

Security fixes target the latest commit on the default branch and the current beta
line. Historical Cloud Core releases are retained as engineering evidence and are
not promised ongoing support.

The Apache investigation package, BUSL application and MIT SDK/CLI have different
license boundaries but share this private reporting channel.

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

The current video-investigation proof is a deterministic local fixture with no model
inference or public-web request. Local investigation creation accepts only
`geolocateProvenance + local`; connected retrieval is not an implemented security
boundary. The investigation workspace and deployment are also pending.

The repository does not claim production readiness, an SLA or customer-data
certification. Reports about current source or a referenced historical deployment
are welcome through the private channel above. Do not place sensitive footage,
source identities, signed media URLs or private dataset records in a public issue.
