#!/usr/bin/env bash
set -euo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORTFOLIO_DIR="$(cd "$TEST_DIR/.." && pwd)"
MIGRATION_RUNNER="$PORTFOLIO_DIR/scripts/run-migration-task.sh"
API_VERIFIER="$PORTFOLIO_DIR/scripts/verify-api-enablement.sh"
FIXTURE_BIN="$TEST_DIR/fixtures/bin"
TEST_TMP="$(mktemp -d)"
trap 'rm -rf -- "$TEST_TMP"' EXIT
TEST_ACCOUNT_ID="$(printf '1%.0s' {1..12})"
export MOCK_EXPECTED_ACCOUNT_ID="$TEST_ACCOUNT_ID"

fail() {
  printf 'portfolio script test failed: %s\n' "$*" >&2
  exit 1
}

assert_log_contains() {
  local pattern="$1"
  grep -F -- "$pattern" "$MOCK_COMMAND_LOG" >/dev/null || fail "command log missing: $pattern"
}

assert_log_excludes() {
  local pattern="$1"
  if grep -F -- "$pattern" "$MOCK_COMMAND_LOG" >/dev/null; then
    fail "command log unexpectedly contains: $pattern"
  fi
}

reset_case() {
  local name="$1"
  export MOCK_COMMAND_LOG="$TEST_TMP/${name}.commands"
  export MOCK_CURL_COUNT_FILE="$TEST_TMP/${name}.curl-count"
  : >"$MOCK_COMMAND_LOG"
}

run_migration_failure() {
  local scenario="$1"
  local forbidden_after="$2"
  reset_case "migration-${scenario}"
  export MOCK_SCENARIO="$scenario"
  export MOCK_API_DESIRED_COUNT=0
  export MOCK_WORKER_DESIRED_COUNT=0

  if EXPECTED_AWS_ACCOUNT_ID="$TEST_ACCOUNT_ID" PATH="$FIXTURE_BIN:/usr/bin:/bin" bash "$MIGRATION_RUNNER" >"$TEST_TMP/${scenario}.out" 2>&1; then
    fail "migration scenario unexpectedly passed: $scenario"
  fi

  assert_log_excludes "$forbidden_after"
  assert_log_excludes "terraform plan"
  assert_log_excludes "terraform apply"
  assert_log_excludes "services-stable"
}

run_migration_failure wrong_account "aws ecs run-task"
run_migration_failure run_task_command_failure "aws ecs wait"
run_migration_failure run_task_failures "aws ecs wait"
run_migration_failure empty_tasks "aws ecs wait"
run_migration_failure none_arn "aws ecs wait"
run_migration_failure missing_exit "aws logs tail"
run_migration_failure nonzero_exit "aws logs tail"
run_migration_failure unexpected_state "aws logs tail"

reset_case "migration-service-not-zero"
export MOCK_SCENARIO=success
export MOCK_API_DESIRED_COUNT=1
export MOCK_WORKER_DESIRED_COUNT=0
if EXPECTED_AWS_ACCOUNT_ID="$TEST_ACCOUNT_ID" PATH="$FIXTURE_BIN:/usr/bin:/bin" bash "$MIGRATION_RUNNER" >"$TEST_TMP/service-not-zero.out" 2>&1; then
  fail "migration ran while API desired count was nonzero"
fi
assert_log_excludes "aws sts get-caller-identity"
assert_log_excludes "aws ecs run-task"

reset_case "migration-success"
export MOCK_SCENARIO=success
export MOCK_API_DESIRED_COUNT=0
export MOCK_WORKER_DESIRED_COUNT=0
EXPECTED_AWS_ACCOUNT_ID="$TEST_ACCOUNT_ID" PATH="$FIXTURE_BIN:/usr/bin:/bin" bash "$MIGRATION_RUNNER" >"$TEST_TMP/migration-success.out" 2>&1
assert_log_contains "aws ecs run-task"
assert_log_contains '"securityGroups":["sg-migration"]'
assert_log_contains '"subnets":["subnet-a","subnet-b"]'
assert_log_contains "aws ecs wait tasks-stopped"
assert_log_contains "aws ecs describe-tasks"
assert_log_contains "aws logs tail /ecs/instascribe-portfolio/migration"
assert_log_excludes "terraform plan"
assert_log_excludes "terraform apply"
assert_log_excludes "services-stable"

reset_case "api-delayed"
export MOCK_SCENARIO=readiness_delayed
export MOCK_API_DESIRED_COUNT=1
export MOCK_WORKER_DESIRED_COUNT=0
EXPECTED_AWS_ACCOUNT_ID="$TEST_ACCOUNT_ID" READINESS_MAX_ATTEMPTS=4 READINESS_INTERVAL_SECONDS=1 PATH="$FIXTURE_BIN:/usr/bin:/bin" bash "$API_VERIFIER" >"$TEST_TMP/api-delayed.out" 2>&1
assert_log_contains "aws ecs wait services-stable"
[[ "$(<"$MOCK_CURL_COUNT_FILE")" == "3" ]] || fail "delayed readiness did not retry to the third attempt"
assert_log_excludes "terraform plan"
assert_log_excludes "terraform apply"
assert_log_excludes "aws ecs run-task"

reset_case "api-timeout"
export MOCK_SCENARIO=readiness_timeout
export MOCK_API_DESIRED_COUNT=1
export MOCK_WORKER_DESIRED_COUNT=0
if EXPECTED_AWS_ACCOUNT_ID="$TEST_ACCOUNT_ID" READINESS_MAX_ATTEMPTS=3 READINESS_INTERVAL_SECONDS=1 PATH="$FIXTURE_BIN:/usr/bin:/bin" bash "$API_VERIFIER" >"$TEST_TMP/api-timeout.out" 2>&1; then
  fail "readiness timeout unexpectedly passed"
fi
[[ "$(<"$MOCK_CURL_COUNT_FILE")" == "3" ]] || fail "readiness timeout did not use the exact attempt bound"
assert_log_excludes "terraform plan"
assert_log_excludes "terraform apply"

reset_case "api-service-failure"
export MOCK_SCENARIO=service_wait_failure
export MOCK_API_DESIRED_COUNT=1
export MOCK_WORKER_DESIRED_COUNT=0
if EXPECTED_AWS_ACCOUNT_ID="$TEST_ACCOUNT_ID" READINESS_MAX_ATTEMPTS=3 READINESS_INTERVAL_SECONDS=1 PATH="$FIXTURE_BIN:/usr/bin:/bin" bash "$API_VERIFIER" >"$TEST_TMP/api-service-failure.out" 2>&1; then
  fail "service waiter failure unexpectedly passed"
fi
assert_log_excludes "curl "
assert_log_excludes "terraform plan"
assert_log_excludes "terraform apply"

run_api_numeric_failure() {
  local name="$1"
  local attempts="$2"
  local interval="$3"
  reset_case "api-numeric-${name}"
  export MOCK_SCENARIO=readiness_success
  export MOCK_API_DESIRED_COUNT=1
  export MOCK_WORKER_DESIRED_COUNT=0

  if EXPECTED_AWS_ACCOUNT_ID="$TEST_ACCOUNT_ID" READINESS_MAX_ATTEMPTS="$attempts" READINESS_INTERVAL_SECONDS="$interval" PATH="$FIXTURE_BIN:/usr/bin:/bin" bash "$API_VERIFIER" >"$TEST_TMP/api-numeric-${name}.out" 2>&1; then
    fail "invalid readiness numeric form unexpectedly passed: $name"
  fi

  assert_log_excludes "terraform "
  assert_log_excludes "aws "
  assert_log_excludes "curl "
}

HUGE_DECIMAL="$(printf '9%.0s' {1..200})"
run_api_numeric_failure attempts-huge "$HUGE_DECIMAL" 10
run_api_numeric_failure attempts-plus +1 10
run_api_numeric_failure attempts-minus -1 10
run_api_numeric_failure attempts-leading-space " 1" 10
run_api_numeric_failure attempts-leading-zero 01 10
run_api_numeric_failure attempts-zero 0 10
run_api_numeric_failure attempts-above-bound 61 10
run_api_numeric_failure interval-huge 12 "$HUGE_DECIMAL"
run_api_numeric_failure interval-plus 12 +1
run_api_numeric_failure interval-minus 12 -1
run_api_numeric_failure interval-trailing-space 12 "1 "
run_api_numeric_failure interval-leading-zero 12 01
run_api_numeric_failure interval-zero 12 0
run_api_numeric_failure interval-above-bound 12 61

reset_case "api-numeric-upper-bound"
export MOCK_SCENARIO=readiness_success
export MOCK_API_DESIRED_COUNT=1
export MOCK_WORKER_DESIRED_COUNT=0
EXPECTED_AWS_ACCOUNT_ID="$TEST_ACCOUNT_ID" READINESS_MAX_ATTEMPTS=60 READINESS_INTERVAL_SECONDS=60 PATH="$FIXTURE_BIN:/usr/bin:/bin" bash "$API_VERIFIER" >"$TEST_TMP/api-numeric-upper-bound.out" 2>&1
assert_log_contains "aws ecs wait services-stable"
[[ "$(<"$MOCK_CURL_COUNT_FILE")" == "1" ]] || fail "upper-bound readiness verification did not succeed once"

printf 'portfolio verification scripts: all deterministic mock cases passed\n'
