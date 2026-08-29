#!/usr/bin/env bash
set -euo pipefail

fail() {
  printf 'API enablement verification failed: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command is unavailable: $1"
}

require_value() {
  local name="$1"
  local value="$2"
  [[ -n "$value" && "$value" != "None" && "$value" != "null" ]] || fail "$name is empty or unknown"
}

[[ -n "${EXPECTED_AWS_ACCOUNT_ID:-}" ]] || fail "EXPECTED_AWS_ACCOUNT_ID is required"
[[ "$EXPECTED_AWS_ACCOUNT_ID" =~ ^[0-9]{12}$ ]] || fail "EXPECTED_AWS_ACCOUNT_ID must be exactly 12 digits"

AWS_REGION="${AWS_REGION:-eu-west-2}"
[[ "$AWS_REGION" == "eu-west-2" ]] || fail "AWS_REGION must remain eu-west-2 for this environment"

READINESS_MAX_ATTEMPTS="${READINESS_MAX_ATTEMPTS:-12}"
READINESS_INTERVAL_SECONDS="${READINESS_INTERVAL_SECONDS:-10}"
READINESS_BOUND_PATTERN='^([1-9]|[1-5][0-9]|60)$'
[[ "$READINESS_MAX_ATTEMPTS" =~ $READINESS_BOUND_PATTERN ]] || fail "READINESS_MAX_ATTEMPTS must be a canonical decimal integer from 1 to 60"
[[ "$READINESS_INTERVAL_SECONDS" =~ $READINESS_BOUND_PATTERN ]] || fail "READINESS_INTERVAL_SECONDS must be a canonical decimal integer from 1 to 60"

require_command aws
require_command curl
require_command jq
require_command terraform

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TERRAFORM_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

terraform_raw() {
  terraform -chdir="$TERRAFORM_DIR" output -raw "$1"
}

API_DESIRED_COUNT="$(terraform_raw api_desired_count)" || fail "cannot read api_desired_count"
WORKER_DESIRED_COUNT="$(terraform_raw worker_desired_count)" || fail "cannot read worker_desired_count"
[[ "$API_DESIRED_COUNT" == "1" ]] || fail "api_desired_count must already be 1 after the authorized enablement apply"
[[ "$WORKER_DESIRED_COUNT" == "0" ]] || fail "worker_desired_count must remain 0 during API verification"

CALLER_JSON="$(aws sts get-caller-identity --output json)" || fail "GetCallerIdentity failed"
CALLER_ACCOUNT="$(jq -er '.Account | select(type == "string" and test("^[0-9]{12}$"))' <<<"$CALLER_JSON")" || fail "GetCallerIdentity returned no valid account"
[[ "$CALLER_ACCOUNT" == "$EXPECTED_AWS_ACCOUNT_ID" ]] || fail "AWS account does not match EXPECTED_AWS_ACCOUNT_ID"

CLUSTER_NAME="$(terraform_raw ecs_cluster_name)" || fail "cannot read ecs_cluster_name"
SERVICE_NAME="$(terraform_raw api_service_name)" || fail "cannot read api_service_name"
READY_URL="$(terraform_raw api_ready_url)" || fail "cannot read api_ready_url"
require_value "ecs_cluster_name" "$CLUSTER_NAME"
require_value "api_service_name" "$SERVICE_NAME"
require_value "api_ready_url" "$READY_URL"
[[ "$READY_URL" == https://* ]] || fail "api_ready_url must use HTTPS"

# This script verifies an already-authorized apply. It never changes desired
# count, invokes Terraform plan/apply, or mutates state.
aws ecs wait services-stable \
  --region "$AWS_REGION" \
  --cluster "$CLUSTER_NAME" \
  --services "$SERVICE_NAME" || fail "API service did not reach ECS stable state"

attempt=1
while (( attempt <= READINESS_MAX_ATTEMPTS )); do
  if curl --fail --silent --show-error --max-time 10 "$READY_URL" >/dev/null; then
    printf 'API enablement verified: ECS service stable and readiness succeeded on attempt %d\n' "$attempt"
    exit 0
  fi

  if (( attempt == READINESS_MAX_ATTEMPTS )); then
    break
  fi

  sleep "$READINESS_INTERVAL_SECONDS"
  attempt=$((attempt + 1))
done

fail "readiness did not succeed within ${READINESS_MAX_ATTEMPTS} attempts"
