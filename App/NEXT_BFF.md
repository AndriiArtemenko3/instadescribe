# Next BFF deployment contract

The Next runtime fails closed unless every server-only value in `.env.next.example` is present and valid. `APP_ORIGIN` is also the fixed CSRF origin authority; production never trusts a request-derived Host header for this decision. None may be exposed through a `NEXT_PUBLIC_` variable. Runtime AWS credentials must come from the workload role; static AWS credentials and integration API keys are not supported.

## Boundaries

- The browser receives a random `__Host-instadescribe_session` HttpOnly cookie. DynamoDB receives only an HMAC digest (`s#…`) of that value.
- Cognito access, refresh and ID tokens plus the resolved human principal are envelope-encrypted with a per-record AES-256-GCM data key; only the KMS-encrypted data key is stored.
- Cognito challenge `Session` values and MFA setup state use a separate encrypted, short-lived digest record and HttpOnly cookie.
- `POST /api/bff/auth/mfa/enroll` lets an authenticated non-Owner voluntarily start software-token MFA. It consumes the current web session before Cognito enrollment, persists only the encrypted short-lived challenge, and requires a fresh MFA login after verification.
- Authenticated mutations and logout require both an exact same-origin `Origin` header and the readable `__Host-instadescribe_csrf` double-submit cookie/header pair.
- The BFF never proxies media. Its session and project handlers call
  `${APP_API_ORIGIN}/api/app/v1/session` and `${APP_API_ORIGIN}/api/app/v1/projects`; the
  authenticated `/api/bff/cloud/[...path]` relay forwards only the exact JSON metadata/control
  routes listed below to `${APP_API_ORIGIN}/api/app/v1/*`. Every upstream call uses the server-held
  Cognito access token.
- There is deliberately no integration API/service-key/legacy fallback.

The metadata relay currently admits these method/path pairs, which match the human Browser API:

- `POST /jobs`, `GET /jobs/{jobId}`, `POST /jobs/{jobId}/uploads/complete`, and
  `POST /jobs/{jobId}/cancel`;
- `GET /jobs/{jobId}/manifest`, `GET /jobs/{jobId}/overrides`, and
  `PATCH /jobs/{jobId}/scenes/{sceneId}`;
- `GET /jobs/{jobId}/review`, `POST /jobs/{jobId}/review/finish`,
  `GET /jobs/{jobId}/render`, and `GET /jobs/{jobId}/deliverables`;
- `GET /deliverables/{deliverableId}/content`, whose upstream response is a redirect to a signed
  object URL rather than media bytes through Next.
- `POST /jobs/{jobId}/scenes/{sceneId}/tts-previews`, plus `GET` on a preview and
  its signed-content redirect;
- `PATCH /projects/{projectId}` for an idempotent, version-checked metadata update.
- `POST /organization/invitations` for an Owner/MFA-only editor, reviewer or viewer invitation.
- `GET` and `POST /investigations`;
- `GET /investigations/{investigationId}` plus `GET` on its `steps`, `keyframes`,
  `evidence`, `beliefs` and `report` children;
- `POST /investigations/{investigationId}/cancel` and
  `POST /investigations/{investigationId}/decision`.

Those ten dedicated investigation method/path pairs, together with the existing
shared `POST /jobs/{jobId}/uploads/complete` route, are the complete investigation
workspace relay footprint. The BFF also retains the audio-description and
organization routes listed above. Malformed identifiers, wrong methods, raw/nested
paths and proposed egress paths return `404` before an upstream request. Source video
still follows the direct-upload contract; Next receives neither the media bytes nor
an investigation keyframe image.

Every relay call carries a short-lived `X-InstaDescribe-Browser-Assertion` generated only by the
BFF. It binds canonical email and completed-MFA state to the SHA-256 digest of the exact Cognito
access token. FastAPI independently validates the token subject and the assertion HMAC; neither
side trusts browser-provided identity fields or Cognito access-token `email`/`amr` claims.

The membership response must be exactly:

```json
{"subject":"…","email":"…","displayName":"…","organizationId":"…","role":"editor","mfaVerified":false}
```

`role` is one of `owner`, `editor`, `reviewer`, or `viewer`; service-account principals are
rejected. Cognito `GetUser` is the email authority. A successful password/new-password exchange is
not MFA evidence; only a completed `SOFTWARE_TOKEN_MFA` or `MFA_SETUP` challenge sets the encrypted
server session flag. An Owner without that evidence is routed through TOTP enrolment, pre-MFA
tokens are revoked, and a fresh MFA login is required before an application session is created.
Editor, Reviewer and Viewer accounts can start the same fail-closed flow from Account; already
MFA-enabled sessions are left intact and do not receive a second seed.

The project response must be exactly `{ "data": [{ "id", "orgSlug", "currentJobId", "name", "status", "updatedAt" }] }`. Extra fields, including media URLs or storage details, invalidate the response.

Investigation responses are also parsed as exact, bounded shapes in the browser
client. Unknown fields, invalid UUIDs or timestamps, non-normalized probabilities,
out-of-frame boxes and inconsistent abstention/final-hypothesis states fail closed as
`invalid_response`. The UI never interprets an arbitrary upstream object as evidence.
The Browser API evidence projection exposes the bounded observation summary but
omits internal observation-detail maps; those details are not part of the BFF or UI
contract.

The product routes `/investigations`, `/investigations/new`, canonical UUID workspace
and report paths, and `/legacy/audio-description` are protected by the same optimistic
cookie boundary. A bounded `returnTo` preserves only those paths and the existing
account/upload/review paths; origins, queries, fragments, malformed UUIDs and nested
suffixes fall back to `/investigations`.

## Current operational contract

The source tree now contains the Cognito/DynamoDB/KMS adapters, the FastAPI human Browser API under
`/api/app/v1`, the confidential Cognito client with `ALLOW_USER_PASSWORD_AUTH`, and the beta
Terraform wiring for the session table, KMS context restrictions, runtime secret shell, least-
privilege task role, and edge authentication throttles. AWS SDK v3 dependencies are pinned in the
App workspace and consolidated in the repository-root npm lock. This describes implemented source
and configuration only; it is not evidence that an AWS plan was applied or a runtime was deployed.

Before starting the provisioned service, an operator must populate the Next runtime shell with the
Cognito client secret and independent session HMAC secret, and populate the separate shared
browser-assertion shell with one canonical base64url value for exactly 32 random bytes. API and Next
must receive that same assertion value; the API role cannot read the Next runtime shell. Then run
the reviewed database/deployment sequence and exercise fail-closed membership, MFA, refresh,
logout, recovery, assertion-replay and challenge paths. Rotate either HMAC secret only through an
explicit overlap migration; immediate replacement invalidates sessions or in-flight assertions.
In-process rate limiting remains intentionally absent because it is ineffective across replicas;
the beta edge WAF owns that control.

DynamoDB TTL is cleanup only; every read also checks expiry. Refresh writes use a record version condition. Logout deletes the local record before Cognito revocation, so a remote outage cannot keep the BFF cookie session usable, while the API still reports unconfirmed remote revocation.

Invited users must have all required Cognito attributes pre-populated. The beta `NEW_PASSWORD_REQUIRED` form submits only a new password and surfaces any additional required attribute names rather than accepting untrusted arbitrary attributes.

The account page now exposes invitation creation only to an authenticated Owner. FastAPI, not the
browser, rechecks the Owner membership and authoritative `mfaVerified` assertion, persists an
inactive principal/membership first, and activates it only after Cognito confirms the exact user.
The immutable Cognito `custom:invitation_id` marker permits a safe retry across the narrow
AdminCreateUser/SQL commit crash window without adopting a pre-existing account. Canonical invited
email is globally unique in beta because browser sessions intentionally have no organization
selector. Resend, revoke and role-changing invitation management are not implemented in beta.
