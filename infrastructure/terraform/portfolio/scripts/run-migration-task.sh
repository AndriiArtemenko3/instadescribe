#!/usr/bin/env bash
set -euo pipefail

fail() {
  printf 'migration verification failed: %s\n' "$*" >&2
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
MIGRATION_LOG_SINCE="${MIGRATION_LOG_SINCE:-30m}"
[[ "$MIGRATION_LOG_SINCE" =~ ^[1-9][0-9]*[smhdw]$ ]] || fail "MIGRATION_LOG_SINCE must be a positive AWS CLI relative duration"

require_command aws
require_command jq
require_command terraform

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TERRAFORM_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

terraform_raw() {
  terraform -chdir="$TERRAFORM_DIR" output -raw "$1"
}

API_DESIRED_COUNT="$(terraform_raw api_desired_count)" || fail "cannot read api_desired_count"
WORKER_DESIRED_COUNT="$(terraform_raw worker_desired_count)" || fail "cannot read worker_desired_count"
[[ "$API_DESIRED_COUNT" == "0" ]] || fail "api_desired_count must be 0 before migration"
[[ "$WORKER_DESIRED_COUNT" == "0" ]] || fail "worker_desired_count must be 0 before migration"

CALLER_JSON="$(aws sts get-caller-identity --output json)" || fail "GetCallerIdentity failed"
CALLER_ACCOUNT="$(jq -er '.Account | select(type == "string" and test("^[0-9]{12}$"))' <<<"$CALLER_JSON")" || fail "GetCallerIdentity returned no valid account"
[[ "$CALLER_ACCOUNT" == "$EXPECTED_AWS_ACCOUNT_ID" ]] || fail "AWS account does not match EXPECTED_AWS_ACCOUNT_ID"

CLUSTER_NAME="$(terraform_raw ecs_cluster_name)" || fail "cannot read ecs_cluster_name"
TASK_DEFINITION="$(terraform_raw migration_task_definition_arn)" || fail "cannot read migration_task_definition_arn"
LOG_GROUP="$(terraform_raw migration_log_group)" || fail "cannot read migration_log_group"
require_value "ecs_cluster_name" "$CLUSTER_NAME"
require_value "migration_task_definition_arn" "$TASK_DEFINITION"
require_value "migration_log_group" "$LOG_GROUP"
[[ "$TASK_DEFINITION" =~ ^arn:aws:ecs:eu-west-2:${EXPECTED_AWS_ACCOUNT_ID}:task-definition/.+:[0-9]+$ ]] || fail "migration task definition ARN does not match the expected account and region"

NETWORK_SOURCE="$(terraform -chdir="$TERRAFORM_DIR" output -json migration_network_configuration)" || fail "cannot read migration network output"
NETWORK_CONFIGURATION="$(jq -ce '
  if (
    (.assign_public_ip == "ENABLED") and
    ((.subnets | type) == "array") and
    ((.subnets | length) == 2) and
    (all(.subnets[]; (type == "string") and (length > 0))) and
    ((.security_groups | type) == "array") and
    ((.security_groups | length) == 1) and
    (all(.security_groups[]; (type == "string") and (length > 0)))
  ) then
    {
      awsvpcConfiguration: {
        subnets: .subnets,
        securityGroups: .security_groups,
        assignPublicIp: .assign_public_ip
      }
    }
  else
    error("invalid migration network configuration")
  end
' <<<"$NETWORK_SOURCE")" || fail "migration network configuration is invalid"

RUN_TASK_JSON="$(aws ecs run-task \
  --region "$AWS_REGION" \
  --cluster "$CLUSTER_NAME" \
  --task-definition "$TASK_DEFINITION" \
  --launch-type FARGATE \
  --network-configuration "$NETWORK_CONFIGURATION" \
  --output json)" || fail "RunTask call failed"

if ! jq -e '
  ((.failures | type) == "array") and
  ((.failures | length) == 0) and
  ((.tasks | type) == "array") and
  ((.tasks | length) == 1) and
  ((.tasks[0].taskArn | type) == "string") and
  (.tasks[0].taskArn != "") and
  (.tasks[0].taskArn != "None") and
  (.tasks[0].taskArn != "null")
' >/dev/null <<<"$RUN_TASK_JSON"; then
  fail "RunTask must return zero failures and exactly one nonempty task ARN"
fi

TASK_ARN="$(jq -er '.tasks[0].taskArn' <<<"$RUN_TASK_JSON")" || fail "cannot read migration task ARN"
require_value "migration task ARN" "$TASK_ARN"
[[ "$TASK_ARN" =~ ^arn:aws:ecs:eu-west-2:${EXPECTED_AWS_ACCOUNT_ID}:task/.+ ]] || fail "migration task ARN does not match the expected account and region"

aws ecs wait tasks-stopped \
  --region "$AWS_REGION" \
  --cluster "$CLUSTER_NAME" \
  --tasks "$TASK_ARN" || fail "migration task did not reach a stopped state"

DESCRIBE_JSON="$(aws ecs describe-tasks \
  --region "$AWS_REGION" \
  --cluster "$CLUSTER_NAME" \
  --tasks "$TASK_ARN" \
  --output json)" || fail "DescribeTasks failed"

if ! jq -e '
  . as $response
  | ($response.tasks[0]) as $task
  | ([$task.containers[]? | select(.name == "migration")]) as $migration
  | (($response.failures | type) == "array")
    and (($response.failures | length) == 0)
    and (($response.tasks | type) == "array")
    and (($response.tasks | length) == 1)
    and ($task.lastStatus == "STOPPED")
    and ($task.desiredStatus == "STOPPED")
    and (($migration | length) == 1)
    and ($migration[0].lastStatus == "STOPPED")
    and (($migration[0].exitCode | type) == "number")
    and ($migration[0].exitCode == 0)
' >/dev/null <<<"$DESCRIBE_JSON"; then
  fail "migration task must be stopped with exactly one migration container and exitCode 0"
fi

# The registered task command is fail-closed:
#   alembic upgrade head && alembic current --check-heads
# Therefore the verified container exitCode 0 proves both commands completed.
# Only the dedicated log stream is tailed; task definitions, environment and
# secret values are never requested or printed by this runner.
aws logs tail "$LOG_GROUP" \
  --region "$AWS_REGION" \
  --since "$MIGRATION_LOG_SINCE" \
  --format short || fail "migration log retrieval failed"

printf 'migration verified: task stopped with exitCode 0 and Alembic heads applied\n'
