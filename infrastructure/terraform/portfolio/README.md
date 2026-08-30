# InstaDescribe Terraform (legacy v0.1 + API-first beta)

This directory defines one explicit, short-lived `eu-west-2` evidence environment. It intentionally
uses no modules, no remote backend, no NAT Gateway and no DNS. The API and worker use public-subnet
Fargate tasks with public IPs but security groups permit no direct task ingress. RDS is private,
encrypted, single-AZ and isolated from the internet. Both ECS services default to a desired count of
zero; the API may become one only after the migration task succeeds, and the worker may never exceed
one task in v0.1.

Applied v0.1 resource names and Terraform addresses retain their historical physical
`instascribe` prefixes. They are compatibility identities, not current product branding, and are
not renamed in place because doing so could replace or orphan legacy infrastructure. New beta
resources use the canonical InstaDescribe naming where the existing state boundary permits it.

The beta can additionally provision a separate Next standalone ECS service. It is off by default
and does not replace the private, versioned Vite S3 origin. The three reviewed stages are:

1. set `enable_next_app_runtime = true`, keep `next_app_desired_count = 0` and
   `app_delivery_origin = "vite_static"`; apply to create the ECR/runtime shell;
2. after generating the single repository-root npm lock, build the ECS architecture from the
   repository root with
   `docker buildx build --platform linux/amd64 -f App/Dockerfile --load .`, push only the full
   `release_commit_sha` tag, populate the `next-bff-runtime` secret JSON out-of-band, set the desired
   count to one, and verify the `/login` target health while S3 still serves browser pages;
3. in a separate reviewed plan/apply, set `app_delivery_origin = "next"`.

Rollback is the inverse origin-only change to `vite_static`; it does not delete the S3 bucket or its
versioned Vite assets. After CloudFront propagation and verification, the unused Next task may be
scaled to zero and eventually disabled in later reviewed applies. The more specific `/api/bff/*`
behavior is the only `/api` path sent to Next; all other `/api/*` routes stay on FastAPI, and media
bytes remain direct signed-S3 browser transfers.

The `next-bff-runtime` Secrets Manager resource intentionally has no Terraform-managed version.
Before starting a task, insert one JSON object out-of-band with exactly
`COGNITO_APP_CLIENT_SECRET` (copied from the sensitive Cognito output) and an independent
`WEB_SESSION_HMAC_SECRET` (at least 32 random bytes encoded as base64url). Never put either value in
tfvars, a plan, the image, task environment literals or source control. The execution role can read
only this shell; the task role is limited to the session table, encryption-context-bound session KMS
operations and the Cognito browser-session calls.

Populate the separate `${project_name}-beta/browser-assertion` secret shell out-of-band with a
canonical, unpadded base64url encoding of exactly 32 independent random bytes. Only the Next
execution role and API execution role may read this shell; it is deliberately not bundled with the
Cognito client/session secrets. Both runtimes receive the same raw value as
`BROWSER_ASSERTION_SECRET`.

The beta API task receives `COGNITO_ISSUER`, `COGNITO_USER_POOL_ID`, `COGNITO_APP_CLIENT_ID` and the exact
`${COGNITO_ISSUER}/.well-known/jwks.json` URL for the fail-closed browser membership resolver. It
does not receive the Cognito client secret. FastAPI validates the Cognito access-token subject and
the token-bound BFF assertion independently before resolving an active membership. Missing,
malformed, stale, future-dated or cross-token assertions fail closed without falling back to an
integration API key or legacy identity.

The API task role can call only `AdminCreateUser`, `AdminGetUser` and `AdminDeleteUser` on this exact
beta user pool for Owner invitations. The pool stores an immutable `custom:invitation_id`; a retry
may reconcile an existing Cognito username only when that random marker and canonical email match
the durable pending invitation. Before enabling customer access, run Alembic through the invitation
migration, manually create the organization/initial Owner membership, and canary one invite through
temporary-password acceptance and the expected role. Cognito email delivery (SES configuration,
sandbox exit and verified sender as applicable) remains an operator prerequisite. The beta has no
resend, revoke, self-service Owner invitation or invitation role-change route.

`webhook_allowed_hosts` is serialized into the beta API task as
`INSTADESCRIBE_WEBHOOK_ALLOWED_HOSTS`; its default is the deny-all JSON value `[]`. Values are exact
operator-approved lowercase DNS names only. This is configuration for the separate dispatcher
process, not for Next, and Terraform holds no webhook endpoint URL or signing-secret value.

The beta also provisions a dedicated no-ingress `webhook-dispatcher` task definition/service, off by
default while the beta API is off. It reuses the immutable API image and execs
`python /srv/scripts/dispatch_webhooks.py` after constructing the escaped RDS URL from the same
RDS-managed credential components. Before setting `webhook_dispatcher_desired_count = 1`, create
each endpoint's signing secret out-of-band under the emitted
`${project_name}-beta/webhook-signing/` namespace, encrypt it with the emitted dedicated KMS key,
store only that secret ARN in the tenant-scoped database endpoint record, and review a non-empty
exact-host allowlist. The dispatcher role can call only `GetSecretValue` in that namespace and
`kms:Decrypt` through regional Secrets Manager with a matching `SecretARN` context. Its only S3
permission is `DeleteObjectVersion` on the tenant-shaped source/transcript/deliverable patterns,
the exact analysis-attempt pattern, and the narrow pre-tenancy portfolio source pattern; it has no
key-only delete, bucket listing, SQS, Cognito or API-key access. The singleton runs the bounded
retention cycle hourly and tombstones metadata only after the exact version deletion succeeds.
Legacy Artifact rows without a recoverable VersionId are reported as unsafe and retained; terminal
Jobs/Projects remain blocked until every object reference and pending webhook delivery is gone.
Its security group has no inbound rule. The code-level public event
allowlist excludes internal `render.requested`, and no Terraform input can widen it.

The same singleton publishes one sanitized CloudWatch heartbeat from durable PostgreSQL state.
`RenderBacklog` remains a dimensionless aggregate for the worker autoscaling contract. The beta
alarm series carry only `Environment=beta`: `OutboxOldestSeconds` is the oldest due public event for
an active webhook endpoint, `WebhookDeliveryExhausted` is the current durable exhausted-delivery
count, and `ExpiredProcessingLeases` counts `PROCESSING` jobs whose analysis lease is missing or
expired. `QuotaRejected` is a non-overlapping heartbeat delta of jobs durably failed with
`quota_exceeded` during worker measured-duration reconciliation. Creation requests rejected before
a Job can be committed have no durable database event and are deliberately not represented by that
metric; API/WAF access telemetry remains the evidence for those HTTP 429 responses. All aggregate
queries must succeed before the single `PutMetricData` call, and a beta task with a missing namespace
or a failed query exits its cycle instead of publishing false zeroes.

Worker scale-out and scale-to-zero alarms combine `RenderBacklog` with the SQS visible/in-flight
backlog, so Finish Review can wake a zero-task worker without making the internal render intent a
customer webhook. Terraform rejects an enabled beta API unless this publisher is also running; its
task role can publish only into that exact namespace. Treat a missing/stale metric or dispatcher
alarm as a render-availability incident.

Every render upload is version-pinned and journaled in PostgreSQL before the next output starts.
Failure, cancellation or a stolen fence deletes only those exact versions; a bounded
`FOR UPDATE SKIP LOCKED` worker janitor retries durable terminal/stale journal rows. Successful
five-file publication removes the journal in the same transaction that publishes Deliverables.
The worker role therefore has `DeleteObjectVersion` only on the tenant deliverable-attempt pattern,
with neither key-only delete nor bucket-list permission. There remains one deliberately explicit
crash window: a process can die after S3 acknowledges a Put but before its exact VersionId is
committed to the journal. The no-list least-privilege policy means that version cannot be safely
discovered automatically and must be found through versioned-bucket inventory/operator evidence.

CloudFront forwards browser cookies, `Origin` and `X-CSRF-Token` only to the Next behavior, with a
zero-TTL cache policy and a forced `private, no-store` response header. The public `/login` target
check proves the standalone server can render; dependency failures remain fail-closed at the BFF
boundary and must also be exercised by the pre-cutover canary before switching the origin.
Both ALB-bound origin-request policies forward the viewer `Host`; the regional ALB certificate must
therefore cover both `app_origin` and `api_origin` so CloudFront can validate origin TLS for the
canonical aliases. Verify those SANs in the reviewed credentialed plan/certificate inspection.

## Safe local validation (no AWS account)

Use a trusted Terraform binary. G9 was validated with checksum-verified Terraform `1.13.5` and the
HashiCorp-signed AWS provider locked in `.terraform.lock.hcl`.

```bash
terraform fmt -check -recursive .
terraform init -backend=false -input=false
AWS_EC2_METADATA_DISABLED=true terraform validate
AWS_EC2_METADATA_DISABLED=true terraform test
bash -n scripts/*.sh tests/portfolio_scripts_test.sh tests/fixtures/bin/*
tests/portfolio_scripts_test.sh
```

Initialization downloads the provider from the Terraform registry only. `validate` and the mocked
contract test require no AWS credentials and must not be replaced by a credentialed plan during the
G9-local gate.

## Inputs for the later owner-approved plan

Copy `terraform.tfvars.example` to the gitignored `portfolio.auto.tfvars`, replace every placeholder,
and make the file readable only by the owner. Required values are:

- the exact owner-approved 12-digit AWS account ID, used only by the provider's fail-closed
  `allowed_account_ids` guard;
- a stable non-secret bucket suffix (not an account ID);
- one full lowercase 40-hex commit SHA used for both image tags and job provenance;
- the SHA-256 digest of the portfolio token (never the plaintext token);
- an active 64-lowercase-hex origin value plus an accepted list containing one or two distinct
  values including the active value;
- the owner-approved Budget/SNS email recipient.

Terraform state is sensitive: it contains the token digest and both origin-header inputs, and after
apply it will reference RDS-managed credentials. State and plans are gitignored, but v0.1 has no
remote-state backend. Keep local state access-restricted and backed up securely; remote encrypted
state/locking is the accepted v0.2 hardening step.

After explicit authorization and only with the intended AWS identity:

```bash
terraform plan -out=portfolio.tfplan
terraform show -no-color portfolio.tfplan
```

The owner must review the exact account/role, region (`eu-west-2`), resource count, budget recipient,
secret inputs and estimated cost before separately authorizing any apply. A plan is not apply
authorization. Never commit the saved plan: it can contain sensitive values.

The provider must fail before planning if the live caller account differs from
`expected_aws_account_id`. Never disable account-ID discovery or remove `allowed_account_ids` to
bypass that stop.

## Fail-closed deployment sequencing boundary

The first reviewed plan and separately authorized apply keep both services at zero. After the exact
commit-SHA images are built and pushed, run the declared one-shot migration task with its dedicated
no-ingress security group and public IP, inspect its stopped-task exit code and migration logs, and
verify the database is at Alembic head. Only then may a second reviewed plan and separately authorized
apply set `api_desired_count = 1`. Verify `/api/readyz` before any smoke test. The exact commands,
failure path and rollback boundary are in `docs/runbooks/g9-portfolio-environment.md`. Until that
sequence passes, the ALB has no API target and the application must not be described as routable.

Origin-header rotation is also staged: `[A]` with active `A` → accepted `[A,B]` while active remains
`A` → active `B` while accepting `[A,B]` → accepted `[B]`. Every value is exact 64-character
lowercase hex; `*`, `?`, whitespace and control characters fail validation, so the ALB condition has
no wildcard semantics. Each transition requires its own reviewed plan/apply and CloudFront
propagation/verification before advancing.

The portfolio tier continues to use the AWS CloudFront default certificate and does not claim a
configurable viewer TLS minimum or custom DNS. Beta is stricter: it requires an existing
`us-east-1` ACM certificate covering both `app_origin` and `api_origin`, configures the CloudFront
viewer minimum as `TLSv1.2_2021`, and requires a workload-region ACM certificate with the same SANs
for CloudFront-to-ALB HTTPS under `ELBSecurityPolicy-TLS13-1-2-2021-06`. Terraform consumes those
certificate ARNs but does not create DNS or ACM resources; certificate issuance, validation and SAN
inspection remain operator prerequisites for a reviewed beta plan.

The committed `run-migration-task.sh` and `verify-api-enablement.sh` scripts are later authorized,
account-pinned verification tools. The first launches and mechanically verifies only the one-shot
migration; the second verifies an already-applied API enablement using ECS stability plus bounded
readiness retries. Neither invokes Terraform plan/apply, changes service counts or mutates state.

The reserved OpenAI secret has no Terraform-managed value. With the default
`enable_g12_openai = false`, neither task receives an OpenAI key reference and the worker execution
role has no permission to read it. The explicit G12 mode derives one shared processing contract for
the API and worker: provider `openai`, a 120-second source limit and one paid attempt (rather than
the fake baseline's provider `fake`, 300-second limit and three attempts). It injects the existing
secret into the worker only and conditionally grants that worker execution role
`secretsmanager:GetSecretValue` for exactly that secret. The API receives provider/limit
configuration but never the key. Add the secret value out-of-band so it never enters Terraform
configuration, plans or state; enabling G12 and running a paid job remain separately reviewable
operations. In the portfolio tier, worker desired count still defaults to zero and may never
exceed one.
In the separate `environment = "beta"` tier, the same opt-in OpenAI mode follows the published
60-minute API contract: the isolated child receives a hard 180-call ceiling (60 standard chunks,
at most three attempts each) and a 7,200-second subprocess deadline. Startup fails closed if those
bounds are lower than the configured duration. This permits a canary; it does not replace the
required real-provider duration/cost evaluation. Beta worker autoscaling remains bounded at two,
while the portfolio G12 service remains bounded at one.
