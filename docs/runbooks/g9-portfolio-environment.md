# G9.3/G10 portfolio environment operations and rollback

**Scope:** the explicit v0.1 `eu-west-2` Terraform environment only.
**Default:** intended maximum 72-hour environment window; API zero; worker zero (maximum one).
**Safety:** every credentialed plan, apply, image push, migration run, service enablement, secret
write, teardown and content deletion is a separate owner-authorized action. This runbook authorizes
none of them.

## Before any credentialed plan

- Resolve D1: exact AWS account and least-privilege Terraform role. Supply the live account ID only
  through the ignored mode-600 tfvars file so the provider's `allowed_account_ids` guard fails
  closed; do not put account IDs in tracked repository content.
- Resolve D2: approved Budget/SNS recipient; confirmation and test notification remain post-apply
  checks.
- Supply one full lowercase 40-hex `release_commit_sha`, a stable bucket suffix, the portfolio-token
  SHA-256 digest, and origin inputs through an access-restricted ignored tfvars file.
- Start origin protection in normal state: active value `A`, accepted values `[A]`; every value is
  exactly 64 lowercase hex. Terraform state and saved plans contain these sensitive values.
- Recalculate current AWS pricing before every plan, including charged Fargate
  ephemeral storage above the included 20 GiB. Historical estimates are evidence,
  not a current quote.
- Record the owner decision for the viewer TLS residual: the AWS CloudFront default certificate does
  not expose the configurable TLS 1.2 security-policy floor used by custom certificates. Accept that
  AWS-managed behavior for this bounded environment or block public deployment pending separately
  authorized DNS and a `us-east-1` ACM certificate. This Terraform creates neither DNS nor ACM.
- Keep the separately recorded R19 HTTP CloudFront-to-ALB origin-leg decision explicit.
- Use reviewed `aws`, Docker/buildx and `jq` installations; command availability/version checks are
  part of the G10 preflight, not evidence inferred by this document.

## Exact fail-closed first rollout

Do not combine these approval boundaries. A successful Terraform command is not permission for the
next command. Run the shown sequence from the reviewed repository root.

1. **Bootstrap plan (separate authorization):** confirm `api_desired_count = 0` and
   `worker_desired_count = 0`, then create a saved plan. Review the exact account/role, region,
   resource count, public-IP assignments, no NAT/DNS/ACM, private single-AZ RDS, origin values,
   release SHA and cost estimate. A provider account mismatch is a hard stop; never bypass the
   allowlist or disable account-ID discovery.

   ```bash
   terraform -chdir=infrastructure/terraform/portfolio plan -out=portfolio-bootstrap.tfplan
   terraform -chdir=infrastructure/terraform/portfolio show -no-color portfolio-bootstrap.tfplan
   ```

2. **Bootstrap apply (new, separate authorization):** apply only the reviewed saved plan. Confirm
   both service outputs remain zero. At this point the ALB has no API targets; do not claim the API is
   routable or healthy.

   ```bash
   terraform -chdir=infrastructure/terraform/portfolio apply portfolio-bootstrap.tfplan
   terraform -chdir=infrastructure/terraform/portfolio output -raw api_desired_count
   terraform -chdir=infrastructure/terraform/portfolio output -raw worker_desired_count
   ```

3. **Images (separate deploy authorization):** build both `linux/amd64` production targets from the
   exact `release_commit_sha`, test them, push that full SHA as the immutable API and worker ECR tag,
   and record the returned registry manifest digests. Never use `latest` or a second provenance
   variable. Run from the reviewed repository root; each login/push remains covered by this separate
   deploy authorization.

   ```bash
   RELEASE_COMMIT_SHA=$(terraform -chdir=infrastructure/terraform/portfolio output -raw release_commit_sha)
   test "$(git rev-parse HEAD)" = "$RELEASE_COMMIT_SHA"
   API_REPOSITORY=$(terraform -chdir=infrastructure/terraform/portfolio output -raw api_ecr_repository_url)
   WORKER_REPOSITORY=$(terraform -chdir=infrastructure/terraform/portfolio output -raw worker_ecr_repository_url)
   API_SOURCE_DIGEST=$(services/api/.venv/bin/python services/worker/scripts/g8_source_digest.py api)
   WORKER_SOURCE_DIGEST=$(services/api/.venv/bin/python services/worker/scripts/g8_source_digest.py worker)
   docker buildx build --platform linux/amd64 --build-arg "SOURCE_DIGEST=$API_SOURCE_DIGEST" --tag "$API_REPOSITORY:$RELEASE_COMMIT_SHA" --file services/api/Dockerfile --load .
   docker buildx build --platform linux/amd64 --target production --build-arg "SOURCE_DIGEST=$WORKER_SOURCE_DIGEST" --tag "$WORKER_REPOSITORY:$RELEASE_COMMIT_SHA" --file services/worker/Dockerfile --load .
   INSTADESCRIBE_API_IMAGE="$API_REPOSITORY:$RELEASE_COMMIT_SHA" services/api/.venv/bin/python services/worker/scripts/g8_api_image_proof.py
   INSTADESCRIBE_WORKER_IMAGE="$WORKER_REPOSITORY:$RELEASE_COMMIT_SHA" services/api/.venv/bin/python services/worker/scripts/g8_image_proof.py
   ECR_REGISTRY=${API_REPOSITORY%%/*}
   test "${WORKER_REPOSITORY%%/*}" = "$ECR_REGISTRY"
   aws ecr get-login-password --region eu-west-2 | docker login --username AWS --password-stdin "$ECR_REGISTRY"
   docker push "$API_REPOSITORY:$RELEASE_COMMIT_SHA"
   docker push "$WORKER_REPOSITORY:$RELEASE_COMMIT_SHA"
   API_IMAGE_DIGEST=$(aws ecr describe-images --region eu-west-2 --repository-name "${API_REPOSITORY##*/}" --image-ids "imageTag=$RELEASE_COMMIT_SHA" --query 'imageDetails[0].imageDigest' --output text)
   WORKER_IMAGE_DIGEST=$(aws ecr describe-images --region eu-west-2 --repository-name "${WORKER_REPOSITORY##*/}" --image-ids "imageTag=$RELEASE_COMMIT_SHA" --query 'imageDetails[0].imageDigest' --output text)
   test -n "$API_IMAGE_DIGEST" && test "$API_IMAGE_DIGEST" != None
   test -n "$WORKER_IMAGE_DIGEST" && test "$WORKER_IMAGE_DIGEST" != None
   printf 'api=%s\nworker=%s\n' "$API_IMAGE_DIGEST" "$WORKER_IMAGE_DIGEST"
   ```

   Stop if HEAD differs, either source digest/proof/build/push fails, the two repositories do not
   share one registry, or either returned digest is empty/`None`. Preserve both registry digests;
   never substitute a local image ID as remote provenance.

4. **Migration run (separate authorization):** use the declared API-image task definition, dedicated
   no-ingress security group, both public subnets, and `assignPublicIp=ENABLED`. The task injects only
   DB host/port/name plus RDS-managed username/password secrets, builds an escaped SQLAlchemy URL,
   runs `alembic upgrade head`, then `alembic current --check-heads`. It exposes no port and creates
   no ECS service.

   ```bash
   EXPECTED_AWS_ACCOUNT_ID="$OWNER_APPROVED_AWS_ACCOUNT_ID" infrastructure/terraform/portfolio/scripts/run-migration-task.sh
   ```

   The runner requires the explicit 12-digit owner-approved account ID, rechecks local API/worker
   outputs are both zero, and calls STS before RunTask. It constructs the ECS network JSON with
   `jq`; rejects account mismatch, any RunTask/DescribeTasks failures, other-than-one task, empty or
   `None` ARN, a non-stopped task, other-than-one `migration` container and absent/non-numeric/nonzero
   exit code. The registered command is `alembic upgrade head && alembic current --check-heads`, so
   verified container exit `0` mechanically proves both commands. It then tails only the dedicated
   migration log group for evidence; it never requests or prints task environment/secret values.
   The runner stops there: it cannot plan/apply, alter state, enable the API or start smoke.

5. **API enablement plan (new, separate authorization):** change only
   `api_desired_count = 1` while keeping the worker at zero. Create and review a second saved plan;
   verify there is no migration replacement or unrelated drift.

   ```bash
   terraform -chdir=infrastructure/terraform/portfolio plan -out=portfolio-api-enable.tfplan
   terraform -chdir=infrastructure/terraform/portfolio show -no-color portfolio-api-enable.tfplan
   ```

6. **API enablement apply (new, separate authorization):** apply only that reviewed plan. Because
   Terraform deliberately has `wait_for_steady_state = false`, the apply result alone is not a
   readiness result. After the apply completes, invoke the read-only verifier under the same
   approved account boundary. It first uses the bounded ECS `services-stable` waiter, then makes up
   to 12 readiness attempts at 10-second intervals through CloudFront.

   ```bash
   terraform -chdir=infrastructure/terraform/portfolio apply portfolio-api-enable.tfplan
   EXPECTED_AWS_ACCOUNT_ID="$OWNER_APPROVED_AWS_ACCOUNT_ID" infrastructure/terraform/portfolio/scripts/verify-api-enablement.sh
   ```

   The verifier requires local API=1/worker=0 outputs and the expected account, but never changes a
   service, invokes plan/apply or mutates Terraform state. Waiter failure or readiness timeout is a
   hard failure; delayed readiness within the bound is not treated as an immediate false failure.
   Optional `READINESS_MAX_ATTEMPTS` and `READINESS_INTERVAL_SECONDS` overrides accept only canonical
   one/two-digit decimal integers from `1` through `60`: signs, whitespace, leading zeroes, zero,
   values above 60 and arbitrarily large strings fail before Terraform or AWS is invoked.

No API is deliberately routable before step 6, and no smoke test starts before readiness passes.

## Failure and rollback boundaries

- Bootstrap failure: do not push images or run migration; diagnose against the reviewed plan.
- Image failure: leave both services at zero; do not run a mutable or substitute tag.
- Migration failure/uncertainty: leave API and worker at zero, preserve stopped-task/log evidence,
  fix forward, obtain a new migration-run authorization, and rerun the idempotent upgrade. Never
  enable the API and never auto-downgrade. A downgrade is a separate data-loss review.
- API waiter/readiness failure: immediately plan a return to `api_desired_count = 0`; review and
  separately authorize that rollback apply. Preserve logs and migration evidence. Do not proceed to
  frontend or worker smoke.
- Application rollback after a healthy release uses previous tested immutable image tags/frontend
  versions. It does not roll back the database automatically and does not stop infrastructure cost.

## Origin-header rotation

Each arrow is a separately reviewed plan/apply, with CloudFront deployment completion and a positive
CloudFront request plus negative direct-origin check before advancing:

1. active `A`, accepted `[A]`;
2. active `A`, accepted `[A,B]`;
3. active `B`, accepted `[A,B]`;
4. active `B`, accepted `[B]`.

Never skip the overlap. Values are exact matches only; wildcard (`*`/`?`), uppercase, whitespace,
control, duplicate, third and active-missing inputs fail mocked Terraform tests. Rotate after state,
plan or distribution-config exposure.

## Worker control and G11 storage measurement

Keep `worker_desired_count = 0`. A controlled enablement requires its own plan/apply authorization
and bounded test window. The task reserves a provisional 40 GiB of ephemeral storage: 20 GiB is the
included Fargate allocation and the additional 20 GiB is billed. During native G11, record task
platform, input envelope, image-pull time, peak filesystem consumption and free-space floor. If 40
GiB is insufficient, raise it through review or narrow the public input claim; never imply that the
entire 250 MiB/5-minute envelope was proven by the current single representative fixture.

## S3 lifecycle and teardown truth

The media rules make current versions eligible for expiration after three lifecycle days. S3 day
calculation uses UTC rounding and lifecycle processing is asynchronous. With versioning, current
expiration normally creates a delete marker and makes the prior version noncurrent; its separate
three-day noncurrent window begins then. Therefore this is not a 72-hour deletion or retention
guarantee, and objects can remain materially longer.

Environment teardown is separately authorized and must inventory and manually empty all current and
noncurrent S3 versions, delete markers and multipart uploads before bucket destruction. Content
deletion is irreversible and needs target-level approval. Also check ECR layers, RDS snapshots and
backups, logs, Secrets Manager recovery-window secrets, ALB/ENIs/public IPv4, CloudFront, SNS/Budget,
and out-of-state resources. Do not claim zero cost until the residual inventory and later billing
view agree.
