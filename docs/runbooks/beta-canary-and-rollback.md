# API-first beta canary and rollback runbook

This runbook is a checklist, not deployment authority. Every command that
mutates AWS, npm or Git history requires a separately reviewed release action.

## Preconditions

- An accepted Cloud Core v0.2 tag exists and matches the API, worker and
  migration image revision. Do not invent or move the tag during rollout.
- CI is green for Python, PostgreSQL/LocalStack, Node 22 and 24, Next and Vite,
  Terraform validation, OpenAPI/codegen drift, tarball installation and
  the deterministic local Playwright browser/BFF/media parity gate.
- The isolated beta Terraform plan uses its own state and resource suffix. It
  shows TLS to the origin, WAF, 7-day RDS backups/deletion protection, 4-day
  work queue, 14-day DLQ, 0–2 workers, 30/90-day S3 lifecycle and the expected
  alarm set.
- Operator-managed Secrets Manager values exist for the API-key pepper,
  Cognito client secret/session encryption material, and the one canary webhook
  secret. No plaintext value is present in Terraform input, task definitions,
  logs or CI artifacts.
- Confirm `GET /v1/capabilities` reports the beta paid-TTS ceilings: 120
  approved scenes, two render claims and 240 aggregate final synthesis calls
  per review; previews are 25/Job and 100/organization per rolling 24 hours,
  with five active and three attempts per request. Treat any mismatch as
  contract drift and stop the canary before enabling the paid provider.

## Browser parity evidence and its boundary

The CI Playwright gate is deliberately local and deterministic. It builds the
production Next runtime, serves it behind a short-lived localhost HTTPS
boundary, and runs pinned Chromium with no customer credentials. Browser-only
upstream responses are intercepted inside the test process; there is no
production test adapter or unsigned fake session in the application runtime.

Run the same bounded gate locally after installing the exact workspace lock:

```bash
npm ci --ignore-scripts --no-audit --no-fund
npm run playwright:install -w App
npm run build:next -w App
npm run test:e2e -w App
```

It proves a real HTTP 404, optimistic protected-route redirect with a bounded
deep-link `returnTo`, production Origin/double-submit-CSRF rejection, exact
organization + Project + Job review-path binding, and that versioned media
references leave the browser directly without API credentials or a Next media
relay. The signed media host and authenticated upstream metadata are fixtures;
this gate does **not** prove live Cognito, DynamoDB/KMS sessions, WAF/ALB,
presigned S3 POST/Range behaviour, or an AWS deployment.

Those external boundaries require the separately authorized internal canary in
the Expand and canary section. Do not admit a customer or call browser parity
complete from the local Playwright result alone.

## Manual beta operator procedure

`services/api/scripts/beta_operator.py` is the only supported bootstrap tool in
this repository. Run it from a trusted, non-shared operator host against the
already migrated beta database. It reads `DATABASE_URL`,
`INSTADESCRIBE_API_KEY_PEPPER`, `COGNITO_USER_POOL_ID`, `AWS_DEFAULT_REGION`
and `INSTADESCRIBE_WEBHOOK_ALLOWED_HOSTS` from the protected process
environment. Do not put the database password, API-key pepper, AWS credentials,
webhook signing value or any customer API key in command arguments, shell
history, tickets or captured terminal output.

Every subcommand owns one database transaction and writes fixed-shape,
sanitized `AuditEvent` rows. Audit details contain only
`{"outcome":"succeeded"}`; the actor is null because this is an external
operator, not a customer principal. The events never contain owner identity
attributes, endpoint URLs, secret references, key prefixes or key plaintext.
Safe JSON output contains resource IDs and mutation status only.

### Honest owner-bootstrap boundary

The current Cognito invitation seam cannot safely create the first Owner. The
durable `organization_invitations` workflow excludes the `owner` role, so
calling `AdminCreateUser` inside the database transaction would invent an
unrecoverable provider/database split. This tool therefore never creates,
deletes, enables, sets a password for or enrolls MFA on a Cognito user.

Create the initial Owner out of band with a fresh UUID in Cognito's immutable
`custom:invitation_id` attribute. The normal Cognito invitation email may carry
the generated temporary password; never type that password into this tool.
For example, after separately confirming the exact beta user-pool ID and Owner
email:

```bash
OWNER_BOOTSTRAP_ID="$(python -c 'import uuid; print(uuid.uuid4())')"
aws cognito-idp admin-create-user \
  --user-pool-id "$COGNITO_USER_POOL_ID" \
  --username "$OWNER_EMAIL" \
  --user-attributes \
    Name=email,Value="$OWNER_EMAIL" \
    Name=custom:invitation_id,Value="$OWNER_BOOTSTRAP_ID" \
  --desired-delivery-mediums EMAIL
```

Then create the local tenant and binding:

```bash
services/api/.venv/bin/python services/api/scripts/beta_operator.py \
  create-organization \
  --slug canary-customer \
  --name "Canary Customer" \
  --owner-email "$OWNER_EMAIL" \
  --owner-display-name "Canary Owner" \
  --owner-bootstrap-id "$OWNER_BOOTSTRAP_ID"
```

The command performs read-only `AdminGetUser`, requires an enabled
`FORCE_CHANGE_PASSWORD` or `CONFIRMED` user, and compares the exact email and
immutable marker before binding the returned `sub`. In one database transaction
it creates the organization, human Owner principal, active Owner membership,
quota row and capacity row. A failure leaves the marked Cognito user in place;
inspect the safe error, correct the cause and retry with the same marker. Do not
adopt a pre-existing unmarked user and do not delete the marked user as an
automatic compensation.

The Owner must still accept the temporary password and complete the BFF's TOTP
enrollment. Owner resource routes remain closed until a fresh login proves MFA.
Do not admit the customer until that browser flow has been tested. Later
Editor/Reviewer/Viewer invitations continue to use the existing durable
owner-only invitation API; this operator is not a replacement for it.

### Service account and least-privilege key

Copy the safe `organizationId` from the previous JSON result and create one
service account:

```bash
services/api/.venv/bin/python services/api/scripts/beta_operator.py \
  create-service-account \
  --organization-id '<organization UUID>' \
  --name 'canary integration'
```

For key issuance, first create a private directory but leave the requested file
absent. The tool uses `O_CREAT|O_EXCL` and forces mode `0600`; an existing file,
relative path, symlink or non-regular target fails closed.

```bash
KEY_DIRECTORY="$(mktemp -d)"
KEY_OUTPUT="$KEY_DIRECTORY/canary-api-key.txt"
services/api/.venv/bin/python services/api/scripts/beta_operator.py \
  issue-api-key \
  --organization-id '<organization UUID>' \
  --service-account-id '<service-account UUID>' \
  --label 'initial canary key' \
  --scope projects:read \
  --scope projects:write \
  --scope jobs:read \
  --scope jobs:write \
  --scope deliverables:read \
  --expires-in-days 90 \
  --output-file "$KEY_OUTPUT"
```

The plaintext exists only in that file and is never written to stdout, stderr,
logs, audit rows or the database. Transfer it once through the approved secret
channel using a client that reads the file directly without copying it into a
command argument or log. Confirm the safe JSON `apiKeyId` is present in the
database before distribution. A failure before the complete token is fsynced
rolls back the key/audit transaction and removes the exact file created by that
invocation.

Once the complete token is fsynced, the file is deliberately preserved on any
transaction or commit exception. A connection can disappear after PostgreSQL
commits but before the client receives acknowledgement; deleting the only
plaintext then would strand a live credential. The command reports failure and
the safe `apiKeyId`/`keyPrefix`, never the token. Treat that outcome, process
termination after the file appears, or any lost terminal result as ambiguous:

1. Do not distribute the token, delete the file or retry issuance yet.
2. Through approved read-only database access, look up the exact reported
   `apiKeyId` and `keyPrefix` and verify its organization, service account,
   label, scopes, expiry and matching `operator.api_key.issued` or
   `operator.api_key.rotated` audit event.
3. If no row exists, the token is unusable; securely dispose of the preserved
   file under the operator-host policy.
4. If the row exists and the credential should be abandoned, revoke that exact
   key with the organization/service-account/key IDs, verify `revoked_at`, then
   securely dispose of the file. If it should be retained, verify all metadata
   before using the file as the one-time transfer source.

This reconciliation is mandatory because neither a commit exception nor a
preserved file alone proves whether the database transaction committed.

Rotation deliberately creates one overlapping replacement and leaves the named
current key live. The beta permits at most two live keys per service account:

```bash
ROTATION_DIRECTORY="$(mktemp -d)"
ROTATION_OUTPUT="$ROTATION_DIRECTORY/replacement-api-key.txt"
services/api/.venv/bin/python services/api/scripts/beta_operator.py \
  rotate-api-key \
  --organization-id '<organization UUID>' \
  --service-account-id '<service-account UUID>' \
  --current-api-key-id '<current API-key UUID>' \
  --label 'canary rotation' \
  --scope projects:read \
  --scope projects:write \
  --scope jobs:read \
  --scope jobs:write \
  --scope deliverables:read \
  --expires-in-days 90 \
  --output-file "$ROTATION_OUTPUT"
```

After the recipient has cut over and the replacement succeeds, revoke the old
key by all three tenant-scoping identifiers. Revocation is immediate and can
also be performed while the organization is suspended:

```bash
services/api/.venv/bin/python services/api/scripts/beta_operator.py \
  revoke-api-key \
  --organization-id '<organization UUID>' \
  --service-account-id '<service-account UUID>' \
  --api-key-id '<old API-key UUID>'
```

### Exact webhook configuration

Create the signing secret value out of band in Secrets Manager under the
dispatcher IAM prefix. Pass only its exact full ARN, never its value or a
wildcard. Confirm `describe-secret` resolves that ARN and that the destination's
bare hostname is already in `INSTADESCRIBE_WEBHOOK_ALLOWED_HOSTS`, then run:

```bash
aws secretsmanager describe-secret \
  --secret-id "$WEBHOOK_SECRET_ARN" \
  --query ARN \
  --output text
services/api/.venv/bin/python services/api/scripts/beta_operator.py \
  configure-webhook \
  --organization-id '<organization UUID>' \
  --endpoint-url 'https://hooks.customer.example/instadescribe' \
  --signing-secret-ref "$WEBHOOK_SECRET_ARN"
```

The tool accepts one exact HTTPS URL only: no credentials, fragments, query
strings, literal IP addresses, non-default ports or non-allowlisted hostname.
Reconfiguration locks the single endpoint row, increments `secretVersion`, and
reactivates it atomically. The dispatcher independently revalidates the host and
public DNS answers before every no-redirect delivery attempt.

### Suspension

Suspension requires both the organization UUID and an exact slug confirmation:

```bash
services/api/.venv/bin/python services/api/scripts/beta_operator.py \
  suspend-organization \
  --organization-id '<organization UUID>' \
  --confirm-slug canary-customer
```

The transaction disables organization authentication and the webhook endpoint,
preventing new webhook claims. A delivery already claimed by the dispatcher may
finish, so inspect in-flight delivery state separately. Suspension does not
erase data and does not silently revoke stored keys; explicitly revoke every
key that must remain unusable after any future reviewed recovery. This bounded
tool intentionally has no resume, delete, quota-edit, Cognito-delete or bulk-key
command. Any recovery or broader mutation requires a separate reviewed
procedure and authority.

## Expand and canary

1. Take an RDS snapshot and record the current API/worker/Vite revisions.
2. Run the one-shot migration task. Require `alembic current --check-heads`,
   then verify portfolio backfill counts and all non-null/composite constraints.
3. Deploy the backward-compatible worker first, then FastAPI. Keep customer
   organization feature flags off and the Next cutover disabled.
4. Create one internal organization, membership, service account, two
   overlapping keys and one exact HTTPS webhook endpoint through the operator
   procedure. Apply tiny quotas and provider budget kill switches.
5. Prove create → direct video/transcript upload → complete → analysis → every
   scene decision → Finish Review → atomic five-format render → webhook →
   checksummed download. Repeat zero-AD and cancellation paths. Inject a
   partial render upload and stolen-fence race; verify only the losing exact
   VersionIds are deleted, a failed delete remains journaled, and the bounded
   janitor later clears it without touching a published Deliverable.
6. Revoke the first key and verify it fails immediately while the rotated key
   continues. Redeliver the same webhook event and verify recipient dedupe.
7. Enable Next for internal users only. Prove invitation, required Owner MFA,
   session expiry, roles, direct S3 upload, signed Range playback, deep links,
   true 404 and Vite rollback.
8. Admit one manually approved customer behind its organization flag. Keep one
   endpoint, fixed quotas, no SLA and polling as webhook recovery.

## Observe

Correlate request ID → organization/key → Job → queue message → claim/fence →
Render → webhook event/delivery. Alert on API 5xx/latency, oldest queue message,
DLQ, worker backlog, expired leases, outbox age, exhausted webhook delivery,
quota/spend rejection, RDS storage/connections and storage errors. Never paste
Authorization headers, signed URLs, prompts or webhook bodies into an incident.
Treat a non-zero terminal/stale render-artifact journal as cleanup backlog. The
aggregate render metric keeps a worker awake for it; inspect identities only
through tenant-scoped database evidence and never substitute a prefix delete.

The dispatcher also runs one bounded retention cycle per hour. Before customer
admission, prove that a 23-hour upload remains active, a 24-hour abandoned
upload is cancelled with quota/capacity released, a review receives only an
internal audit warning with seven days remaining, and a scene edit extends its
deadline by 30 days. For object expiry, verify the log contains aggregate
counters only, the request uses the persisted VersionId, a failed delete keeps
the row retryable, and a successful delete leaves an Asset/Deliverable metadata
tombstone until the organization's metadata deadline.

DLQ redrive is manual: inspect the persisted message identity and authoritative
Job state first. Never bulk/redrive blindly. A successful Job is not made failed
because a webhook exhausted retries.

## Kill switches

- suspend the organization;
- revoke one or all service keys;
- set organization quota to zero;
- disable its webhook endpoint or the global dispatcher;
- disable paid-provider execution while retaining safe reconciliation reads;
- set worker scaling maximum to zero after active leases finish or are
  explicitly cancelled.

## Rollback

1. Stop new writes with the organization/global feature flags; do not delete
   rows or objects.
2. Disable Next cutover and restore the proven Vite origin. Existing signed S3
   transfers may continue until their short expiry.
3. Roll FastAPI/worker back only to a revision proven compatible with the
   expanded schema and current queue messages. Do not downgrade the database
   during an incident.
4. Drain or quarantine messages by persisted identity. Preserve outbox and
   audit evidence.
5. If data restoration is required, restore the latest snapshot to a new RDS
   instance, validate row counts/tenant constraints and rehearse application
   read-only recovery before any DNS or service switch.

## Exit evidence

Record immutable image SHAs, migration head, sanitized test and alarm evidence,
canary organization ID, restore timing, rollback timing, known limitations and
the exact user-approved decision. Only then may a separate release task create
tags, publish npm `next` packages or enable customer traffic.
