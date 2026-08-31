mock_provider "aws" {}

mock_provider "aws" {
  alias = "us_east_1"
}

# Runtime definitions intentionally embed provider-computed identifiers. Make
# those identifiers deterministic during plan so the tests can inspect the
# exact ECS environment/secrets contract without applying any resources.
override_resource {
  target          = aws_cognito_user_pool.beta
  override_during = plan
  values = {
    id  = "eu-west-2_testpool"
    arn = "arn:aws:cognito-idp:eu-west-2:123456789012:userpool/eu-west-2_testpool"
  }
}

override_resource {
  target          = aws_cognito_user_pool_client.next_bff
  override_during = plan
  values = {
    id            = "test-browser-client"
    client_secret = "test-only-provider-computed-secret"
  }
}

override_resource {
  target          = aws_kms_key.web_sessions
  override_during = plan
  values = {
    arn    = "arn:aws:kms:eu-west-2:123456789012:key/11111111-1111-1111-1111-111111111111"
    key_id = "11111111-1111-1111-1111-111111111111"
  }
}

override_resource {
  target          = aws_kms_key.webhook_signing
  override_during = plan
  values = {
    arn    = "arn:aws:kms:eu-west-2:123456789012:key/22222222-2222-2222-2222-222222222222"
    key_id = "22222222-2222-2222-2222-222222222222"
  }
}

override_resource {
  target          = aws_secretsmanager_secret.next_bff_runtime
  override_during = plan
  values = {
    arn = "arn:aws:secretsmanager:eu-west-2:123456789012:secret:instadescribe-beta-next-bff-runtime"
  }
}

override_resource {
  target          = aws_secretsmanager_secret.browser_assertion
  override_during = plan
  values = {
    arn = "arn:aws:secretsmanager:eu-west-2:123456789012:secret:instadescribe-beta-browser-assertion"
  }
}

override_resource {
  target          = aws_ecr_repository.next_app
  override_during = plan
  values = {
    arn            = "arn:aws:ecr:eu-west-2:123456789012:repository/instadescribe-beta-next-app"
    repository_url = "123456789012.dkr.ecr.eu-west-2.amazonaws.com/instadescribe-beta-next-app"
  }
}

override_resource {
  target          = aws_s3_bucket.media
  override_during = plan
  values = {
    arn    = "arn:aws:s3:::instascribe-media-test"
    bucket = "instascribe-media-test"
  }
}

override_resource {
  target          = aws_sqs_queue.investigation
  override_during = plan
  values = {
    id   = "https://sqs.eu-west-2.amazonaws.com/123456789012/instadescribe-beta-investigation"
    url  = "https://sqs.eu-west-2.amazonaws.com/123456789012/instadescribe-beta-investigation"
    arn  = "arn:aws:sqs:eu-west-2:123456789012:instadescribe-beta-investigation"
    name = "instadescribe-beta-investigation"
  }
}

override_resource {
  target          = aws_sqs_queue.investigation_dlq
  override_during = plan
  values = {
    id   = "https://sqs.eu-west-2.amazonaws.com/123456789012/instadescribe-beta-investigation-dlq"
    url  = "https://sqs.eu-west-2.amazonaws.com/123456789012/instadescribe-beta-investigation-dlq"
    arn  = "arn:aws:sqs:eu-west-2:123456789012:instadescribe-beta-investigation-dlq"
    name = "instadescribe-beta-investigation-dlq"
  }
}

override_resource {
  target          = aws_cloudfront_cache_policy.next_private_disabled
  override_during = plan
  values = {
    id = "next-private-cache-policy"
  }
}

override_resource {
  target          = aws_cloudfront_origin_request_policy.next_app
  override_during = plan
  values = {
    id = "next-origin-request-policy"
  }
}

override_resource {
  target          = aws_cloudfront_response_headers_policy.next_private
  override_during = plan
  values = {
    id = "next-private-response-policy"
  }
}

override_data {
  target = data.aws_ec2_managed_prefix_list.cloudfront_origin_facing
  values = { id = "pl-12345678" }
}

override_data {
  target = data.aws_iam_policy_document.ecs_tasks_assume
  values = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
}

override_data {
  target = data.aws_iam_policy_document.api_execution
  values = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
}

override_data {
  target = data.aws_iam_policy_document.worker_execution
  values = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
}

override_data {
  target = data.aws_iam_policy_document.migration_execution
  values = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
}

override_data {
  target = data.aws_iam_policy_document.api_task
  values = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
}

override_data {
  target = data.aws_iam_policy_document.worker_task
  values = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
}

override_data {
  target = data.aws_iam_policy_document.next_app_execution
  values = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
}

override_data {
  target = data.aws_iam_policy_document.next_app_task
  values = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
}

override_data {
  target = data.aws_iam_policy_document.webhook_dispatcher_execution
  values = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
}

override_data {
  target = data.aws_iam_policy_document.webhook_dispatcher_task
  values = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
}

override_data {
  target = data.aws_iam_policy_document.frontend_bucket
  values = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
}

override_data {
  target = data.aws_iam_policy_document.media_bucket
  values = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
}

variables {
  expected_aws_account_id       = "123456789012"
  resource_suffix               = "g9local"
  release_commit_sha            = "1111111111111111111111111111111111111111"
  portfolio_token_sha256        = "0000000000000000000000000000000000000000000000000000000000000000"
  origin_verify_active_value    = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  origin_verify_accepted_values = ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]
  budget_alert_email            = "alerts@instadescribe.test"
  api_desired_count             = 0
  worker_desired_count          = 0
  enable_g12_openai             = false
}

run "bootstrap_services_off" {
  command = plan

  assert {
    condition = (
      local.name == "instascribe-portfolio" &&
      local.effective_database_name == "instascribe" &&
      local.effective_database_username == "instascribe_admin"
    )
    error_message = "The legacy portfolio stack must retain its existing physical resource and database names."
  }

  assert {
    condition     = aws_ecs_service.api.desired_count == 0 && aws_ecs_service.worker.desired_count == 0
    error_message = "Bootstrap must leave both ECS services at zero."
  }

  assert {
    condition     = aws_ecs_task_definition.worker.cpu == "2048" && aws_ecs_task_definition.worker.memory == "8192"
    error_message = "The worker must retain the provisional 2-vCPU/8-GiB sizing."
  }

  assert {
    condition     = aws_ecs_task_definition.worker.ephemeral_storage[0].size_in_gib == 40
    error_message = "The worker requires an explicit provisional 40-GiB ephemeral-storage allocation."
  }

  assert {
    condition = (
      local.migration_container_definition.command == [
        "/bin/sh",
        "-c",
        "${local.database_url_shell} alembic -c /srv/alembic.ini upgrade head && exec alembic -c /srv/alembic.ini current --check-heads",
      ] &&
      local.migration_container_definition.essential &&
      strcontains(local.database_url_shell, "from sqlalchemy import URL") &&
      strcontains(local.database_url_shell, "render_as_string(hide_password=False)") &&
      length(local.migration_container_definition.environment) == 3 &&
      local.migration_container_definition.environment[0].name == "DATABASE_HOST" &&
      local.migration_container_definition.environment[1].name == "DATABASE_PORT" &&
      local.migration_container_definition.environment[2].name == "DATABASE_NAME" &&
      length(local.migration_container_definition.secrets) == 2 &&
      local.migration_container_definition.secrets[0].name == "DATABASE_USERNAME" &&
      local.migration_container_definition.secrets[1].name == "DATABASE_PASSWORD" &&
      local.migration_container_definition.logConfiguration.options.awslogs-stream-prefix == "migration" &&
      !contains(keys(local.migration_container_definition), "portMappings")
    )
    error_message = "Migration command, DB environment, RDS-managed secret injection or no-port contract drifted."
  }

  assert {
    condition     = local.container_environment[3].value == var.release_commit_sha
    error_message = "Application job provenance must use the single release commit SHA."
  }

  assert {
    condition = (
      local.api_image_tag == var.release_commit_sha &&
      local.worker_image_tag == var.release_commit_sha &&
      local.migration_image_tag == var.release_commit_sha
    )
    error_message = "API, worker and migration image tags must all derive from the single release commit SHA."
  }

  assert {
    condition     = aws_db_instance.postgres.multi_az == false && aws_db_instance.postgres.publicly_accessible == false
    error_message = "RDS must be single-AZ and private in v0.1."
  }

  assert {
    condition = (
      aws_sqs_queue.work.visibility_timeout_seconds == 1800 &&
      local.processing_job_max_attempts == 3
    )
    error_message = "The queue visibility and retry contracts must remain fixed."
  }

  assert {
    condition = (
      length(aws_sqs_queue.investigation) == 0 &&
      length(aws_sqs_queue.investigation_dlq) == 0 &&
      length(aws_sqs_queue_redrive_allow_policy.investigation_dlq) == 0 &&
      length(aws_cloudwatch_metric_alarm.investigation_dlq_visible) == 0 &&
      length(local.api_investigation_environment) == 0 &&
      length([
        for statement in data.aws_iam_policy_document.api_task.statement : statement
        if statement.sid == "PublishLocalInvestigations"
      ]) == 0 &&
      !contains([for item in local.worker_environment : item.name], "INSTADESCRIBE_INVESTIGATION_QUEUE_URL")
    )
    error_message = "The legacy portfolio stack must not provision, publish or expose the beta investigation queue."
  }

  assert {
    condition = (
      var.enable_g12_openai == false &&
      local.processing_provider == "fake" &&
      local.processing_max_duration_secs == 300 &&
      local.processing_job_max_attempts == 3 &&
      local.processing_max_provider_calls == 6 &&
      local.processing_subprocess_timeout_secs == 1500 &&
      local.render_timeout_secs == 1800 &&
      one([for item in local.worker_environment : item.value if item.name == "INSTADESCRIBE_RENDER_TIMEOUT_SECS"]) == "1800" &&
      one([for item in local.worker_environment : item.value if item.name == "INSTADESCRIBE_RENDER_HEARTBEAT_INTERVAL_SECS"]) == "15" &&
      local.container_environment[4] == { name = "INSTADESCRIBE_PROVIDER", value = "fake" } &&
      local.container_environment[5] == { name = "INSTADESCRIBE_MAX_DURATION_SECS", value = "300" } &&
      local.container_environment[6] == { name = "INSTADESCRIBE_MAX_ATTEMPTS", value = "3" } &&
      local.container_environment[7] == { name = "INSTADESCRIBE_MAX_PROVIDER_CALLS", value = "6" } &&
      length(local.worker_openai_secrets) == 0 &&
      length(aws_iam_role_policy.worker_openai_secret) == 0 &&
      !contains([for secret in local.api_runtime_secrets : secret.name], "OPENAI_API_KEY") &&
      !contains([for secret in local.worker_runtime_secrets : secret.name], "OPENAI_API_KEY")
    )
    error_message = "The default fake-provider plan must retain 300 seconds/three attempts and contain no OpenAI injection or permission."
  }

  assert {
    condition     = aws_s3_bucket_versioning.media.versioning_configuration[0].status == "Enabled"
    error_message = "Exact-version source reads require media-bucket versioning."
  }

  assert {
    condition = (
      !contains(one(aws_s3_bucket_cors_configuration.media.cors_rule).allowed_headers, "x-amz-tagging") &&
      length(aws_s3_bucket_lifecycle_configuration.media.rule) == 3 &&
      length([
        for statement in data.aws_iam_policy_document.api_task.statement : statement
        if statement.sid == "TagInvestigationSourceRetention"
      ]) == 0
    )
    error_message = "The legacy portfolio upload CORS, lifecycle-rule count and API permissions must remain untagged."
  }

  assert {
    condition = (
      local.attempt_artifact_object_arn == "arn:aws:s3:::instascribe-media-test/jobs/*/attempts/*" &&
      local.legacy_source_object_arn == "arn:aws:s3:::instascribe-media-test/uploads/????????-????-????-????-????????????/source/*" &&
      local.source_video_object_arn == "arn:aws:s3:::instascribe-media-test/uploads/orgs/*/jobs/*/source/*" &&
      local.source_transcript_object_arn == "arn:aws:s3:::instascribe-media-test/uploads/orgs/*/jobs/*/transcript/*" &&
      local.deliverable_object_arn == "arn:aws:s3:::instascribe-media-test/deliverables/orgs/*/jobs/*/attempts/*" &&
      local.preview_object_arn == "arn:aws:s3:::instascribe-media-test/previews/orgs/*/jobs/*/requests/*/attempts/*/narration.mp3" &&
      local.attempt_artifact_object_arn != "arn:aws:s3:::instascribe-media-test/jobs/*" &&
      local.legacy_source_object_arn != "arn:aws:s3:::instascribe-media-test/uploads/*" &&
      local.legacy_source_object_arn != "arn:aws:s3:::instascribe-media-test/uploads/*/source/*" &&
      local.source_video_object_arn != "arn:aws:s3:::instascribe-media-test/uploads/*" &&
      local.source_transcript_object_arn != "arn:aws:s3:::instascribe-media-test/uploads/*" &&
      local.deliverable_object_arn != "arn:aws:s3:::instascribe-media-test/deliverables/*" &&
      local.preview_object_arn != "arn:aws:s3:::instascribe-media-test/previews/*"
    )
    error_message = "Worker/API lifecycle access must stay on exact attempt-artifact and tenant-deliverable object prefixes."
  }

  assert {
    condition = (
      one([
        for statement in data.aws_iam_policy_document.api_task.statement :
        contains(statement.resources, local.deliverable_object_arn) &&
        length(statement.actions) == 2 &&
        alltrue([for action in statement.actions : contains(["s3:GetObject", "s3:GetObjectVersion"], action)])
        if statement.sid == "SignTenantDeliverableReads"
      ]) &&
      one([
        for statement in data.aws_iam_policy_document.worker_task.statement :
        contains(statement.resources, local.attempt_artifact_object_arn) &&
        length(statement.actions) == 2 &&
        alltrue([for action in statement.actions : contains(["s3:GetObject", "s3:GetObjectVersion"], action)])
        if statement.sid == "ReadAttemptScopedArtifactsForRender"
      ]) &&
      one([
        for statement in data.aws_iam_policy_document.worker_task.statement :
        contains(statement.resources, local.deliverable_object_arn) &&
        length(statement.actions) == 1 &&
        contains(statement.actions, "s3:PutObject") &&
        length(statement.condition) == 1 &&
        one([
          for condition in statement.condition :
          condition.variable == "s3:x-amz-server-side-encryption" &&
          contains(condition.values, "AES256")
        ])
        if statement.sid == "WriteTenantScopedDeliverables"
      ]) &&
      one([
        for statement in data.aws_iam_policy_document.worker_task.statement :
        length(statement.actions) == 1 &&
        contains(statement.actions, "s3:DeleteObjectVersion") &&
        length(statement.resources) == 1 &&
        contains(statement.resources, local.deliverable_object_arn)
        if statement.sid == "DeleteOrphanedRenderAttemptVersions"
      ]) &&
      one([
        for statement in data.aws_iam_policy_document.api_task.statement :
        contains(statement.resources, local.preview_object_arn) &&
        length(statement.actions) == 2 &&
        alltrue([for action in statement.actions : contains(["s3:GetObject", "s3:GetObjectVersion"], action)])
        if statement.sid == "SignTenantTtsPreviewReads"
      ]) &&
      one([
        for statement in data.aws_iam_policy_document.worker_task.statement :
        contains(statement.resources, local.preview_object_arn) &&
        length(statement.actions) == 1 &&
        contains(statement.actions, "s3:PutObject") &&
        length(statement.condition) == 1 &&
        one([
          for condition in statement.condition :
          condition.variable == "s3:x-amz-server-side-encryption" &&
          contains(condition.values, "AES256")
        ])
        if statement.sid == "WriteTenantScopedTtsPreviews"
      ]) &&
      one([
        for statement in data.aws_iam_policy_document.worker_task.statement :
        length(statement.actions) == 1 &&
        contains(statement.actions, "s3:DeleteObjectVersion") &&
        length(statement.resources) == 1 &&
        contains(statement.resources, local.preview_object_arn)
        if statement.sid == "DeleteExactTtsPreviewVersions"
      ]) &&
      alltrue([
        for statement in data.aws_iam_policy_document.worker_task.statement :
        !contains(statement.actions, "s3:DeleteObject") &&
        !contains(statement.actions, "s3:ListBucket") &&
        !contains(statement.actions, "s3:ListBucketVersions")
      ])
    )
    error_message = "API media access must be Get-only; worker writes and exact-version cleanup must stay on exact render/preview prefixes without key-only/list access."
  }

  assert {
    condition     = aws_cloudwatch_metric_alarm.dlq_visible.threshold == 0
    error_message = "The mandatory DLQ alarm must fire when visible messages exceed zero."
  }

  assert {
    condition = (
      !local.next_app_runtime_enabled &&
      !local.next_app_origin_active &&
      length(aws_ecs_service.next_app) == 0 &&
      length(aws_ecr_repository.next_app) == 0 &&
      length(aws_lb_target_group.next_app) == 0 &&
      length(aws_secretsmanager_secret.next_bff_runtime) == 0 &&
      length(aws_secretsmanager_secret.browser_assertion) == 0 &&
      length(aws_ecs_service.webhook_dispatcher) == 0 &&
      length(aws_kms_key.webhook_signing) == 0 &&
      aws_cloudfront_distribution.app.default_root_object == "index.html" &&
      aws_cloudfront_distribution.app.default_cache_behavior[0].target_origin_id == "frontend-s3" &&
      length(aws_cloudfront_distribution.app.default_cache_behavior[0].function_association) == 1 &&
      contains([for origin in aws_cloudfront_distribution.app.origin : origin.origin_id], "frontend-s3")
    )
    error_message = "Portfolio must remain static Vite-only and must not provision the beta Next runtime or secret shell."
  }
}

run "api_enabled_after_migration" {
  command = plan

  variables {
    api_desired_count = 1
  }

  assert {
    condition     = aws_ecs_service.api.desired_count == 1 && aws_ecs_service.worker.desired_count == 0
    error_message = "The second apply may enable exactly one API while the worker remains off."
  }
}

run "beta_safety_profile" {
  command = plan

  variables {
    environment                = "beta"
    worker_desired_count       = 0
    alb_certificate_arn        = "arn:aws:acm:eu-west-2:123456789012:certificate/11111111-1111-1111-1111-111111111111"
    cloudfront_certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/22222222-2222-2222-2222-222222222222"
  }

  assert {
    condition = (
      local.name == "instadescribe-beta" &&
      local.effective_database_name == "instadescribe" &&
      local.effective_database_username == "instadescribe_admin"
    )
    error_message = "The undeployed beta stack must use canonical InstaDescribe resource and database names."
  }

  assert {
    condition = (
      length(aws_appautoscaling_target.worker) == 1 &&
      aws_appautoscaling_target.worker[0].min_capacity == 0 &&
      aws_appautoscaling_target.worker[0].max_capacity == 2
    )
    error_message = "Beta must provision bounded queue-driven worker autoscaling from zero to two tasks."
  }

  assert {
    condition = (
      aws_sqs_queue.work.message_retention_seconds == 345600 &&
      aws_sqs_queue.dlq.message_retention_seconds == 1209600
    )
    error_message = "Beta work and DLQ retention must be four and fourteen days."
  }

  assert {
    condition = (
      length(aws_sqs_queue.investigation) == 1 &&
      length(aws_sqs_queue.investigation_dlq) == 1 &&
      aws_sqs_queue.investigation[0].visibility_timeout_seconds == 1800 &&
      aws_sqs_queue.investigation[0].message_retention_seconds == 345600 &&
      aws_sqs_queue.investigation[0].receive_wait_time_seconds == 20 &&
      aws_sqs_queue.investigation[0].sqs_managed_sse_enabled &&
      aws_sqs_queue.investigation_dlq[0].message_retention_seconds == 1209600 &&
      aws_sqs_queue.investigation_dlq[0].sqs_managed_sse_enabled &&
      jsondecode(aws_sqs_queue.investigation[0].redrive_policy).deadLetterTargetArn == aws_sqs_queue.investigation_dlq[0].arn &&
      jsondecode(aws_sqs_queue.investigation[0].redrive_policy).maxReceiveCount == 3 &&
      jsondecode(aws_sqs_queue_redrive_allow_policy.investigation_dlq[0].redrive_allow_policy).redrivePermission == "byQueue" &&
      jsondecode(aws_sqs_queue_redrive_allow_policy.investigation_dlq[0].redrive_allow_policy).sourceQueueArns == [aws_sqs_queue.investigation[0].arn] &&
      length(aws_cloudwatch_metric_alarm.investigation_dlq_visible) == 1
    )
    error_message = "Beta needs one encrypted, long-polled investigation queue with a 3-attempt redrive contract and isolated 14-day DLQ."
  }

  assert {
    condition = (
      [for item in local.api_investigation_environment : item.name] == ["INSTADESCRIBE_INVESTIGATION_QUEUE_URL"] &&
      local.api_investigation_environment[0].value == aws_sqs_queue.investigation[0].url
    )
    error_message = "The beta API task must receive the exact investigation queue URL."
  }

  assert {
    condition     = !contains([for item in local.worker_environment : item.name], "INSTADESCRIBE_INVESTIGATION_QUEUE_URL")
    error_message = "The existing Fargate AD worker must not receive the investigation queue URL."
  }

  assert {
    condition = one([
      for statement in data.aws_iam_policy_document.api_task.statement :
      length(statement.actions) == 2 &&
      contains(statement.actions, "sqs:GetQueueUrl") &&
      contains(statement.actions, "sqs:SendMessage") &&
      toset(statement.resources) == toset([aws_sqs_queue.investigation[0].arn])
      if statement.sid == "PublishLocalInvestigations"
    ])
    error_message = "Only the beta API may publish to the exact investigation queue ARN."
  }

  assert {
    condition = (
      contains(one(aws_s3_bucket_cors_configuration.media.cors_rule).allowed_headers, "Content-Type") &&
      contains(one(aws_s3_bucket_cors_configuration.media.cors_rule).allowed_methods, "POST") &&
      !contains(one(aws_s3_bucket_cors_configuration.media.cors_rule).allowed_headers, "x-amz-tagging") &&
      length(aws_s3_bucket_lifecycle_configuration.media.rule) == 33 &&
      one([
        for rule in aws_s3_bucket_lifecycle_configuration.media.rule :
        rule.status == "Enabled" &&
        one(rule.filter).prefix == "uploads/" &&
        one(rule.expiration).days == 30 &&
        one(rule.noncurrent_version_expiration).noncurrent_days == 30
        if rule.id == "expire-abandoned-uploads"
      ]) &&
      alltrue([
        for days in range(1, 31) : one([
          for rule in aws_s3_bucket_lifecycle_configuration.media.rule :
          rule.status == "Enabled" &&
          one(one(rule.filter).and).prefix == "uploads/orgs/" &&
          one(one(rule.filter).and).tags[local.investigation_retention_tag_key] == tostring(days) &&
          one(rule.expiration).days == days &&
          one(rule.noncurrent_version_expiration).noncurrent_days == days &&
          length(rule.abort_incomplete_multipart_upload) == 0
          if rule.id == "expire-investigation-source-${days}d"
        ])
      ])
    )
    error_message = "Beta investigation POST Object uploads need exact multipart CORS support, a 30-day untagged fallback and all 1..30-day tagged current/noncurrent lifecycle tiers."
  }

  assert {
    condition = (
      one([
        for statement in data.aws_iam_policy_document.api_task.statement :
        length(statement.actions) == 1 &&
        contains(statement.actions, "s3:PutObjectTagging") &&
        length(statement.resources) == 1 &&
        contains(statement.resources, local.source_video_object_arn) &&
        length([
          for condition in statement.condition : condition
          if condition.test == "ForAllValues:StringEquals" &&
          condition.variable == "s3:RequestObjectTagKeys" &&
          toset(condition.values) == toset([local.investigation_retention_tag_key])
        ]) == 1 &&
        length([
          for condition in statement.condition : condition
          if condition.test == "StringEquals" &&
          condition.variable == "s3:RequestObjectTag/${local.investigation_retention_tag_key}" &&
          toset(condition.values) == toset([for days in range(1, 31) : tostring(days)])
        ]) == 1
        if statement.sid == "TagInvestigationSourceRetention"
      ]) &&
      alltrue([
        for statement in data.aws_iam_policy_document.api_task.statement :
        !contains(statement.actions, "s3:DeleteObjectTagging")
      ])
    )
    error_message = "Only the beta API may apply the exact investigation retention tag key/tiers to tenant source-video uploads; no API statement may delete tags."
  }

  assert {
    condition = (
      aws_db_instance.postgres.backup_retention_period == 7 &&
      aws_db_instance.postgres.deletion_protection &&
      !aws_db_instance.postgres.skip_final_snapshot &&
      !aws_db_instance.postgres.delete_automated_backups
    )
    error_message = "Beta must retain seven days of backups and fail closed on database deletion."
  }

  assert {
    condition = (
      length(aws_cognito_user_pool.beta) == 1 &&
      aws_cognito_user_pool.beta[0].admin_create_user_config[0].allow_admin_create_user_only &&
      aws_cognito_user_pool.beta[0].mfa_configuration == "OPTIONAL" &&
      length([for item in aws_cognito_user_pool.beta[0].schema : item if item.name == "invitation_id" && !item.mutable && !item.required]) == 1 &&
      length(aws_dynamodb_table.web_sessions) == 1 &&
      aws_dynamodb_table.web_sessions[0].ttl[0].enabled &&
      aws_dynamodb_table.web_sessions[0].point_in_time_recovery[0].enabled
    )
    error_message = "Beta must provision invitation-only Cognito and a TTL/PITR server-session table."
  }

  assert {
    condition = (
      one([for item in local.container_environment : item.value if item.name == "INSTADESCRIBE_MAX_UPLOAD_BYTES"]) == "1073741824" &&
      one([for item in local.container_environment : item.value if item.name == "INSTADESCRIBE_MAX_DURATION_SECS"]) == "3600" &&
      one([for item in local.container_environment : item.value if item.name == "INSTADESCRIBE_DEPLOYMENT_TIER"]) == "beta" &&
      local.render_timeout_secs == 7200 &&
      one([for item in local.worker_environment : item.value if item.name == "INSTADESCRIBE_RENDER_TIMEOUT_SECS"]) == "7200" &&
      one([for item in local.worker_environment : item.value if item.name == "INSTADESCRIBE_RENDER_HEARTBEAT_INTERVAL_SECS"]) == "15" &&
      contains([for secret in local.api_runtime_secrets : secret.name], "INSTADESCRIBE_API_KEY_PEPPER") &&
      contains([for secret in local.api_runtime_secrets : secret.name], "BROWSER_ASSERTION_SECRET")
    )
    error_message = "Beta must expose published media limits and inject only its API-key pepper plus browser assertion secret."
  }

  assert {
    condition = (
      length(aws_lb_listener.https) == 1 &&
      one([
        for origin in aws_cloudfront_distribution.app.origin :
        try(origin.custom_origin_config[0].origin_protocol_policy, "")
        if origin.origin_id == "fastapi-alb"
      ]) == "https-only" &&
      length(aws_wafv2_web_acl.beta) == 1 &&
      length(aws_cloudfront_distribution.app.aliases) == 2
    )
    error_message = "Beta must use TLS to the ALB, attach the edge WAF and serve the canonical app/API aliases."
  }

  assert {
    condition = (
      one([for setting in aws_ecs_cluster.this.setting : setting.value if setting.name == "containerInsights"]) == "enabled" &&
      length(aws_cloudwatch_metric_alarm.api_target_5xx) == 1 &&
      length(aws_cloudwatch_metric_alarm.api_latency) == 1 &&
      length(aws_cloudwatch_metric_alarm.oldest_work_message) == 1 &&
      length(aws_cloudwatch_metric_alarm.rds_free_storage) == 1 &&
      length(aws_cloudwatch_metric_alarm.beta_custom) == 4
    )
    error_message = "Beta operational alarms and container insights must be provisioned before customer canary traffic."
  }

  assert {
    condition = (
      !local.next_app_runtime_enabled &&
      length(aws_secretsmanager_secret.next_bff_runtime) == 1 &&
      length(aws_secretsmanager_secret.browser_assertion) == 1 &&
      length(aws_ecs_service.next_app) == 0 &&
      aws_cloudfront_distribution.app.default_cache_behavior[0].target_origin_id == "frontend-s3" &&
      aws_cloudfront_distribution.app.default_root_object == "index.html" &&
      length(aws_cloudfront_distribution.app.default_cache_behavior[0].function_association) == 1
    )
    error_message = "Beta defaults must create only the empty server-secret shells while retaining the static Vite delivery path."
  }

  assert {
    condition = (
      [for item in local.api_browser_auth_environment : item.name] == [
        "COGNITO_ISSUER",
        "COGNITO_USER_POOL_ID",
        "COGNITO_APP_CLIENT_ID",
        "COGNITO_JWKS_URL",
      ] &&
      one([for item in local.api_browser_auth_environment : item.value if item.name == "COGNITO_USER_POOL_ID"]) == aws_cognito_user_pool.beta[0].id &&
      one([for item in local.api_browser_auth_environment : item.value if item.name == "COGNITO_JWKS_URL"]) == "${local.cognito_issuer}/.well-known/jwks.json" &&
      startswith(local.cognito_issuer, "https://cognito-idp.eu-west-2.amazonaws.com/") &&
      !contains([for item in local.api_browser_auth_environment : item.name], "COGNITO_APP_CLIENT_SECRET") &&
      length(local.webhook_dispatcher_environment) == 1 &&
      local.webhook_dispatcher_environment[0].name == "INSTADESCRIBE_WEBHOOK_ALLOWED_HOSTS" &&
      local.webhook_dispatcher_environment[0].value == "[]"
    )
    error_message = "The beta API must receive only the exact Cognito JWT verifier contract and a fail-closed empty webhook-host allowlist."
  }

  assert {
    condition = one([
      for statement in data.aws_iam_policy_document.api_task.statement :
      length(statement.actions) == 3 &&
      contains(statement.actions, "cognito-idp:AdminCreateUser") &&
      contains(statement.actions, "cognito-idp:AdminDeleteUser") &&
      contains(statement.actions, "cognito-idp:AdminGetUser") &&
      length(statement.resources) == 1 &&
      contains(statement.resources, aws_cognito_user_pool.beta[0].arn)
      if statement.sid == "ProvisionInvitedHumanUsers"
    ])
    error_message = "The API task may reconcile invited users only through the three required Admin calls on the exact beta pool."
  }

  assert {
    condition = (
      length(aws_ecs_task_definition.webhook_dispatcher) == 1 &&
      length(aws_ecs_service.webhook_dispatcher) == 1 &&
      aws_ecs_service.webhook_dispatcher[0].desired_count == 0 &&
      length(aws_cloudwatch_log_group.webhook_dispatcher) == 1 &&
      length(aws_kms_key.webhook_signing) == 1 &&
      length(aws_cloudwatch_metric_alarm.webhook_dispatcher_running) == 0 &&
      length(aws_ecs_service.webhook_dispatcher[0].load_balancer) == 0 &&
      length(aws_vpc_security_group_ingress_rule.database_from_webhook_dispatcher) == 1
    )
    error_message = "Beta must provision an off-by-default, logged, no-load-balancer/no-ingress dispatcher with only its PostgreSQL path."
  }

  assert {
    condition = (
      local.webhook_dispatcher_container_definition.command == [
        "/bin/sh",
        "-c",
        "${local.database_url_shell} exec python /srv/scripts/dispatch_webhooks.py",
      ] &&
      [for item in local.webhook_dispatcher_container_definition.environment : item.name] == [
        "AWS_DEFAULT_REGION",
        "INSTADESCRIBE_MEDIA_BUCKET",
        "DATABASE_HOST",
        "DATABASE_PORT",
        "DATABASE_NAME",
        "INSTADESCRIBE_DEPLOYMENT_TIER",
        "INSTADESCRIBE_METRICS_NAMESPACE",
        "PYTHONDONTWRITEBYTECODE",
        "HOME",
        "INSTADESCRIBE_WEBHOOK_ALLOWED_HOSTS",
      ] &&
      [for item in local.webhook_dispatcher_container_definition.secrets : item.name] == [
        "DATABASE_USERNAME",
        "DATABASE_PASSWORD",
      ] &&
      local.webhook_dispatcher_container_definition.readonlyRootFilesystem &&
      local.webhook_dispatcher_container_definition.user == "10001" &&
      !contains(keys(local.webhook_dispatcher_container_definition), "portMappings")
    )
    error_message = "The dispatcher must exec the bounded API-image script with only DB, media-bucket, metrics and allowlist configuration and no listener."
  }

  assert {
    condition = (
      local.webhook_dispatcher_secret_actions == ["secretsmanager:GetSecretValue"] &&
      local.webhook_dispatcher_kms_actions == ["kms:Decrypt"] &&
      local.webhook_signing_secret_name_prefix == "instadescribe-beta/webhook-signing/" &&
      local.webhook_signing_secret_arn_pattern == "arn:aws:secretsmanager:eu-west-2:123456789012:secret:instadescribe-beta/webhook-signing/*" &&
      alltrue([
        for action in concat(local.webhook_dispatcher_secret_actions, local.webhook_dispatcher_kms_actions) :
        !startswith(action, "s3:") && !startswith(action, "sqs:") && !startswith(action, "cognito-idp:")
      ])
    )
    error_message = "The dispatcher signing-secret capability must stay limited to one namespace and context-bound KMS decrypt."
  }


  assert {
    condition = (
      one([
        for statement in data.aws_iam_policy_document.webhook_dispatcher_task[0].statement :
        length(statement.actions) == 1 &&
        contains(statement.actions, "secretsmanager:GetSecretValue") &&
        length(statement.resources) == 1 &&
        contains(statement.resources, local.webhook_signing_secret_arn_pattern)
        if statement.sid == "ReadEndpointSigningSecretsOnly"
      ]) &&
      one([
        for statement in data.aws_iam_policy_document.webhook_dispatcher_task[0].statement :
        length(statement.actions) == 1 &&
        contains(statement.actions, "kms:Decrypt") &&
        length(statement.resources) == 1 &&
        contains(statement.resources, aws_kms_key.webhook_signing[0].arn) &&
        length(statement.condition) == 2 &&
        contains([for condition in statement.condition : condition.variable], "kms:ViaService") &&
        contains([for condition in statement.condition : condition.variable], "kms:EncryptionContext:SecretARN")
        if statement.sid == "DecryptEndpointSigningSecretsOnly"
      ])
    )
    error_message = "The rendered dispatcher task policy must preserve exact GetSecretValue and via-service/context-bound KMS statements."
  }

  assert {
    condition = (
      one([
        for statement in data.aws_iam_policy_document.webhook_dispatcher_task[0].statement :
        length(statement.actions) == 1 &&
        contains(statement.actions, "s3:DeleteObjectVersion") &&
        length(statement.resources) == 2 &&
        contains(statement.resources, local.source_video_object_arn) &&
        contains(statement.resources, local.source_transcript_object_arn)
        if statement.sid == "DeleteExpiredSourceVersionsOnly"
      ]) &&
      one([
        for statement in data.aws_iam_policy_document.webhook_dispatcher_task[0].statement :
        length(statement.actions) == 1 &&
        contains(statement.actions, "s3:DeleteObjectVersion") &&
        length(statement.resources) == 1 &&
        contains(statement.resources, local.legacy_source_object_arn)
        if statement.sid == "DeleteExpiredLegacySourceVersionsOnly"
      ]) &&
      one([
        for statement in data.aws_iam_policy_document.webhook_dispatcher_task[0].statement :
        length(statement.actions) == 1 &&
        contains(statement.actions, "s3:DeleteObjectVersion") &&
        length(statement.resources) == 1 &&
        contains(statement.resources, local.attempt_artifact_object_arn)
        if statement.sid == "DeleteExpiredAnalysisArtifactVersionsOnly"
      ]) &&
      one([
        for statement in data.aws_iam_policy_document.webhook_dispatcher_task[0].statement :
        length(statement.actions) == 1 &&
        contains(statement.actions, "s3:DeleteObjectVersion") &&
        length(statement.resources) == 1 &&
        contains(statement.resources, local.deliverable_object_arn)
        if statement.sid == "DeleteExpiredDeliverableVersionsOnly"
      ]) &&
      alltrue([
        for statement in data.aws_iam_policy_document.webhook_dispatcher_task[0].statement :
        !contains(statement.actions, "s3:DeleteObject") &&
        !contains(statement.actions, "s3:ListBucket") &&
        !contains(statement.actions, "s3:ListBucketVersions")
      ])
    )
    error_message = "Retention must delete only exact tenant/legacy source, analysis-attempt and deliverable versions and must never receive key-only or bucket-list access."
  }
}

run "beta_webhook_dispatcher_enabled" {
  command = plan

  variables {
    environment                      = "beta"
    alb_certificate_arn              = "arn:aws:acm:eu-west-2:123456789012:certificate/11111111-1111-1111-1111-111111111111"
    cloudfront_certificate_arn       = "arn:aws:acm:us-east-1:123456789012:certificate/22222222-2222-2222-2222-222222222222"
    webhook_allowed_hosts            = ["hooks.customer.test"]
    webhook_dispatcher_desired_count = 1
    api_desired_count                = 1
  }

  assert {
    condition = (
      aws_ecs_service.webhook_dispatcher[0].desired_count == 1 &&
      aws_ecs_service.webhook_dispatcher[0].deployment_minimum_healthy_percent == 0 &&
      aws_ecs_service.webhook_dispatcher[0].deployment_maximum_percent == 100 &&
      length(aws_cloudwatch_metric_alarm.webhook_dispatcher_running) == 1 &&
      local.webhook_dispatcher_environment[0].value == "[\"hooks.customer.test\"]" &&
      one([for item in local.webhook_dispatcher_task_environment : item.value if item.name == "INSTADESCRIBE_METRICS_NAMESPACE"]) == local.worker_metrics_namespace
    )
    error_message = "An enabled beta API must retain one monitored dispatcher/render-backlog publisher in the exact deployment namespace."
  }

  assert {
    condition = (
      one([for query in aws_cloudwatch_metric_alarm.worker_backlog_present[0].metric_query : query.expression if query.id == "backlog"]) == "visible + inflight + render" &&
      one([for query in aws_cloudwatch_metric_alarm.worker_backlog_empty[0].metric_query : query.expression if query.id == "backlog"]) == "visible + inflight + render" &&
      aws_cloudwatch_metric_alarm.worker_backlog_empty[0].treat_missing_data == "notBreaching" &&
      one([for query in aws_cloudwatch_metric_alarm.worker_backlog_present[0].metric_query : query.metric[0].namespace if query.id == "render"]) == local.worker_metrics_namespace &&
      one([for query in aws_cloudwatch_metric_alarm.worker_backlog_empty[0].metric_query : query.metric[0].namespace if query.id == "render"]) == local.worker_metrics_namespace
    )
    error_message = "Worker scale-out and scale-to-zero must include the durable PostgreSQL render backlog metric."
  }

  assert {
    condition = one([
      for statement in data.aws_iam_policy_document.webhook_dispatcher_task[0].statement :
      length(statement.actions) == 1 &&
      contains(statement.actions, "cloudwatch:PutMetricData") &&
      length(statement.resources) == 1 &&
      contains(statement.resources, "*") &&
      length(statement.condition) == 1 &&
      one(statement.condition).variable == "cloudwatch:namespace" &&
      length(one(statement.condition).values) == 1 &&
      contains(one(statement.condition).values, local.worker_metrics_namespace)
      if statement.sid == "PublishRenderBacklogMetric"
    ])
    error_message = "The dispatcher may publish metrics only into the deployment-owned CloudWatch namespace."
  }
}

run "beta_next_runtime_warm_static" {
  command = plan

  variables {
    environment                = "beta"
    alb_certificate_arn        = "arn:aws:acm:eu-west-2:123456789012:certificate/11111111-1111-1111-1111-111111111111"
    cloudfront_certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/22222222-2222-2222-2222-222222222222"
    enable_next_app_runtime    = true
    next_app_desired_count     = 1
    app_delivery_origin        = "vite_static"
    webhook_allowed_hosts      = ["hooks.customer.test"]
  }

  assert {
    condition = (
      local.next_app_runtime_enabled &&
      !local.next_app_origin_active &&
      length(aws_ecr_repository.next_app) == 1 &&
      length(aws_ecs_task_definition.next_app) == 1 &&
      length(aws_ecs_service.next_app) == 1 &&
      aws_ecs_service.next_app[0].desired_count == 1 &&
      length(aws_lb_target_group.next_app) == 1 &&
      length(aws_cloudwatch_log_group.next_app) == 1 &&
      length(aws_secretsmanager_secret.next_bff_runtime) == 1 &&
      length(aws_secretsmanager_secret.browser_assertion) == 1
    )
    error_message = "The warm beta stage must provision one isolated Next runtime while keeping cutover separately controlled."
  }

  assert {
    condition = (
      aws_cloudfront_distribution.app.default_cache_behavior[0].target_origin_id == "frontend-s3" &&
      aws_cloudfront_distribution.app.default_root_object == "index.html" &&
      length(aws_cloudfront_distribution.app.default_cache_behavior[0].function_association) == 1 &&
      contains([for origin in aws_cloudfront_distribution.app.origin : origin.origin_id], "frontend-s3") &&
      contains([for origin in aws_cloudfront_distribution.app.origin : origin.origin_id], "next-app-alb") &&
      aws_cloudfront_distribution.app.ordered_cache_behavior[0].path_pattern == "/api/bff/*" &&
      aws_cloudfront_distribution.app.ordered_cache_behavior[1].path_pattern == "/api/*" &&
      one([
        for behavior in aws_cloudfront_distribution.app.ordered_cache_behavior : behavior.target_origin_id
        if behavior.path_pattern == "/api/bff/*"
      ]) == "next-app-alb" &&
      one([
        for behavior in aws_cloudfront_distribution.app.ordered_cache_behavior : behavior.target_origin_id
        if behavior.path_pattern == "/api/*"
      ]) == "fastapi-alb" &&
      contains(aws_cloudfront_origin_request_policy.api.headers_config[0].headers[0].items, "Host") &&
      contains(aws_cloudfront_origin_request_policy.api.headers_config[0].headers[0].items, "X-InstaDescribe-Browser-Assertion") &&
      contains(aws_cloudfront_origin_request_policy.next_app[0].headers_config[0].headers[0].items, "Host") &&
      contains(aws_cloudfront_origin_request_policy.next_app[0].headers_config[0].headers[0].items, "Origin") &&
      contains(aws_cloudfront_origin_request_policy.next_app[0].headers_config[0].headers[0].items, "X-CSRF-Token") &&
      contains(aws_cloudfront_origin_request_policy.next_app[0].headers_config[0].headers[0].items, "Idempotency-Key") &&
      contains(aws_cloudfront_origin_request_policy.next_app[0].headers_config[0].headers[0].items, "If-Match")
    )
    error_message = "The warm stage must preserve Vite, isolate BFF/FastAPI behaviors and forward canonical Host for ALB TLS validation."
  }

  assert {
    condition = (
      [for item in local.next_app_environment : item.name] == [
        "NODE_ENV",
        "NEXT_TELEMETRY_DISABLED",
        "HOSTNAME",
        "PORT",
        "AWS_REGION",
        "APP_ORIGIN",
        "APP_API_ORIGIN",
        "COGNITO_USER_POOL_ID",
        "COGNITO_APP_CLIENT_ID",
        "WEB_SESSION_TABLE_NAME",
        "WEB_SESSION_KMS_KEY_ID",
      ] &&
      one([for item in local.next_app_environment : item.value if item.name == "APP_ORIGIN"]) == var.app_origin &&
      one([for item in local.next_app_environment : item.value if item.name == "APP_API_ORIGIN"]) == var.api_origin &&
      [for item in local.next_app_runtime_secrets : item.name] == [
        "COGNITO_APP_CLIENT_SECRET",
        "WEB_SESSION_HMAC_SECRET",
        "BROWSER_ASSERTION_SECRET",
      ] &&
      !contains([for item in local.next_app_environment : item.name], "INSTADESCRIBE_API_KEY") &&
      !contains([for item in local.next_app_runtime_secrets : item.name], "INSTADESCRIBE_API_KEY")
    )
    error_message = "The Next task must receive the exact server-only BFF environment and secret set, with no integration API key."
  }

  assert {
    condition = (
      local.next_app_dynamodb_actions == [
        "dynamodb:DeleteItem",
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem",
      ] &&
      local.next_app_session_key_prefixes == ["s#*", "c#*"] &&
      local.next_app_kms_actions == ["kms:Decrypt", "kms:GenerateDataKey"] &&
      local.next_app_cognito_actions == [
        "cognito-idp:AssociateSoftwareToken",
        "cognito-idp:ConfirmForgotPassword",
        "cognito-idp:ForgotPassword",
        "cognito-idp:GetUser",
        "cognito-idp:GlobalSignOut",
        "cognito-idp:InitiateAuth",
        "cognito-idp:RespondToAuthChallenge",
        "cognito-idp:RevokeToken",
        "cognito-idp:SetUserMFAPreference",
        "cognito-idp:VerifySoftwareToken",
      ] &&
      alltrue([for action in concat(local.next_app_dynamodb_actions, local.next_app_kms_actions, local.next_app_cognito_actions) : !startswith(action, "s3:")])
    )
    error_message = "The Next task policy allowlists must remain exact and must never grant media/S3 access."
  }

  assert {
    condition = (
      aws_lb_target_group.next_app[0].health_check[0].path == "/login" &&
      aws_lb_target_group.next_app[0].health_check[0].matcher == "200" &&
      jsondecode(aws_ecs_task_definition.next_app[0].container_definitions)[0].readonlyRootFilesystem &&
      jsondecode(aws_ecs_task_definition.next_app[0].container_definitions)[0].user == "10001" &&
      length(jsondecode(aws_ecs_task_definition.next_app[0].container_definitions)[0].mountPoints) == 2 &&
      jsondecode(aws_ecs_task_definition.next_app[0].container_definitions)[0].mountPoints[0].containerPath == "/workspace/App/.next/cache" &&
      jsondecode(aws_ecs_task_definition.next_app[0].container_definitions)[0].mountPoints[1].containerPath == "/tmp" &&
      aws_cloudfront_cache_policy.next_private_disabled[0].default_ttl == 0 &&
      aws_cloudfront_cache_policy.next_private_disabled[0].max_ttl == 0 &&
      one([
        for item in aws_cloudfront_response_headers_policy.next_private[0].custom_headers_config[0].items : item.value
        if item.header == "Cache-Control"
      ]) == "private, no-store, max-age=0, must-revalidate" &&
      contains([for rule in aws_wafv2_web_acl.beta[0].rule : rule.name], "next-auth-rate-limit")
    )
    error_message = "The Next runtime must retain readiness, read-only-root, private no-store and edge auth-rate-limit controls."
  }

  assert {
    condition = (
      length(local.webhook_dispatcher_environment) == 1 &&
      local.webhook_dispatcher_environment[0].name == "INSTADESCRIBE_WEBHOOK_ALLOWED_HOSTS" &&
      local.webhook_dispatcher_environment[0].value == "[\"hooks.customer.test\"]" &&
      !contains([for item in local.next_app_environment : item.name], "INSTADESCRIBE_WEBHOOK_ALLOWED_HOSTS")
    )
    error_message = "The operator webhook-host allowlist belongs only to the beta API/dispatcher configuration, never the Next task."
  }
}

run "beta_next_origin_cutover" {
  command = plan

  variables {
    environment                = "beta"
    alb_certificate_arn        = "arn:aws:acm:eu-west-2:123456789012:certificate/11111111-1111-1111-1111-111111111111"
    cloudfront_certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/22222222-2222-2222-2222-222222222222"
    enable_next_app_runtime    = true
    next_app_desired_count     = 1
    app_delivery_origin        = "next"
  }

  assert {
    condition = (
      local.next_app_origin_active &&
      aws_cloudfront_distribution.app.default_cache_behavior[0].target_origin_id == "next-app-alb" &&
      aws_cloudfront_distribution.app.default_root_object == null &&
      contains(aws_cloudfront_distribution.app.default_cache_behavior[0].allowed_methods, "POST") &&
      length(aws_cloudfront_distribution.app.default_cache_behavior[0].function_association) == 0 &&
      contains([for origin in aws_cloudfront_distribution.app.origin : origin.origin_id], "frontend-s3") &&
      aws_s3_bucket_versioning.frontend.versioning_configuration[0].status == "Enabled"
    )
    error_message = "Cutover must target Next without deleting or rewriting the versioned Vite S3 rollback origin."
  }

  assert {
    condition = (
      aws_cloudfront_distribution.app.default_cache_behavior[0].cache_policy_id == aws_cloudfront_cache_policy.next_private_disabled[0].id &&
      aws_cloudfront_distribution.app.default_cache_behavior[0].origin_request_policy_id == aws_cloudfront_origin_request_policy.next_app[0].id &&
      aws_cloudfront_distribution.app.default_cache_behavior[0].response_headers_policy_id == aws_cloudfront_response_headers_policy.next_private[0].id
    )
    error_message = "Private Next HTML must use the zero-TTL cookie-forwarding request and no-store response policies."
  }
}

run "g12_openai_worker_only" {
  command = plan

  variables {
    enable_g12_openai = true
  }

  assert {
    condition = (
      local.processing_provider == "openai" &&
      local.processing_max_duration_secs == 120 &&
      local.processing_job_max_attempts == 1 &&
      local.processing_max_provider_calls == 6 &&
      local.processing_subprocess_timeout_secs == 1500 &&
      local.container_environment[4] == { name = "INSTADESCRIBE_PROVIDER", value = "openai" } &&
      local.container_environment[5] == { name = "INSTADESCRIBE_MAX_DURATION_SECS", value = "120" } &&
      local.container_environment[6] == { name = "INSTADESCRIBE_MAX_ATTEMPTS", value = "1" } &&
      local.container_environment[7] == { name = "INSTADESCRIBE_MAX_PROVIDER_CALLS", value = "6" }
    )
    error_message = "G12 must derive provider openai, a 120-second input bound and one paid attempt across the queue/job contract."
  }

  assert {
    condition = (
      length(local.worker_openai_secrets) == 1 &&
      local.worker_openai_secrets[0].name == "OPENAI_API_KEY" &&
      length(local.worker_runtime_secrets) == 3 &&
      !contains([for secret in local.api_runtime_secrets : secret.name], "OPENAI_API_KEY") &&
      contains([for secret in local.worker_runtime_secrets : secret.name], "OPENAI_API_KEY")
    )
    error_message = "G12 must inject the existing OpenAI secret into the worker only; the API must never receive it."
  }

  assert {
    condition = (
      length(aws_iam_role_policy.worker_openai_secret) == 1 &&
      local.worker_openai_secret_statement.Action == ["secretsmanager:GetSecretValue"] &&
      length(local.worker_openai_secret_statement.Resource) == 1
    )
    error_message = "G12 must grant only one worker execution policy with GetSecretValue scoped to one secret."
  }

  assert {
    condition     = aws_ecs_service.worker.desired_count == 0
    error_message = "Enabling the G12 configuration must not start the worker; the separately controlled desired count remains zero by default."
  }
}

run "beta_openai_duration_budget" {
  command = plan

  variables {
    environment                = "beta"
    enable_g12_openai          = true
    alb_certificate_arn        = "arn:aws:acm:eu-west-2:123456789012:certificate/11111111-1111-1111-1111-111111111111"
    cloudfront_certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/22222222-2222-2222-2222-222222222222"
  }

  assert {
    condition = (
      local.processing_provider == "openai" &&
      local.processing_max_duration_secs == 3600 &&
      local.processing_job_max_attempts == 1 &&
      local.processing_max_provider_calls == 180 &&
      local.processing_subprocess_timeout_secs == 7200 &&
      local.render_timeout_secs == 7200 &&
      one([for item in local.worker_environment : item.value if item.name == "INSTADESCRIBE_RENDER_TIMEOUT_SECS"]) == "7200" &&
      one([for item in local.container_environment : item.value if item.name == "INSTADESCRIBE_MAX_PROVIDER_CALLS"]) == "180"
    )
    error_message = "Beta OpenAI must provision enough bounded calls and execution time for the published 60-minute standard preset."
  }
}

run "origin_overlap_active_a" {
  command = plan

  variables {
    origin_verify_accepted_values = [
      "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    ]
  }

  assert {
    condition = (
      nonsensitive(var.origin_verify_active_value) == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" &&
      length(nonsensitive(var.origin_verify_accepted_values)) == 2 &&
      nonsensitive(var.origin_verify_accepted_values)[0] == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" &&
      nonsensitive(var.origin_verify_accepted_values)[1] == "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    )
    error_message = "The overlap step must keep CloudFront active A while ALB accepts exact A and B."
  }
}

run "origin_overlap_active_b" {
  command = plan

  variables {
    origin_verify_active_value = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    origin_verify_accepted_values = [
      "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    ]
  }

  assert {
    condition     = nonsensitive(var.origin_verify_active_value) == "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    error_message = "The cutover step must make B active while ALB still accepts A and B."
  }
}

run "origin_final_b" {
  command = plan

  variables {
    origin_verify_active_value    = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    origin_verify_accepted_values = ["bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]
  }

  assert {
    condition = (
      length(nonsensitive(var.origin_verify_accepted_values)) == 1 &&
      nonsensitive(var.origin_verify_accepted_values)[0] == "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    )
    error_message = "The final rotation step must drop A and accept only exact B."
  }
}

run "project_name_maximum" {
  command = plan

  variables {
    project_name = "abcdefghijklmnopqr"
  }

  assert {
    condition     = length(aws_lb.api.name) <= 32 && length(aws_lb_target_group.api.name) <= 32
    error_message = "The maximum accepted project name must keep ALB names within 32 characters."
  }
}

run "beta_next_project_name_maximum" {
  command = plan

  variables {
    project_name               = "abcdefghijklmnopqr"
    environment                = "beta"
    alb_certificate_arn        = "arn:aws:acm:eu-west-2:123456789012:certificate/11111111-1111-1111-1111-111111111111"
    cloudfront_certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/22222222-2222-2222-2222-222222222222"
    enable_next_app_runtime    = true
  }

  assert {
    condition = (
      length(aws_ecr_repository.next_app[0].name) <= 256 &&
      length(aws_lb_target_group.next_app[0].name) <= 32 &&
      length(aws_iam_role.next_app_execution[0].name) <= 64 &&
      length(aws_iam_role.next_app_task[0].name) <= 64
    )
    error_message = "The maximum accepted project name must keep every Next runtime resource name within its AWS limit."
  }
}

run "reject_api_two" {
  command         = plan
  expect_failures = [var.api_desired_count]

  variables { api_desired_count = 2 }
}

run "reject_worker_two" {
  command         = plan
  expect_failures = [var.worker_desired_count]

  variables { worker_desired_count = 2 }
}

run "reject_next_runtime_in_portfolio" {
  command         = plan
  expect_failures = [var.enable_next_app_runtime]

  variables { enable_next_app_runtime = true }
}

run "reject_next_desired_without_runtime" {
  command         = plan
  expect_failures = [var.next_app_desired_count]

  variables { next_app_desired_count = 1 }
}

run "reject_next_cutover_without_warm_task" {
  command         = plan
  expect_failures = [var.app_delivery_origin]

  variables {
    environment                = "beta"
    alb_certificate_arn        = "arn:aws:acm:eu-west-2:123456789012:certificate/11111111-1111-1111-1111-111111111111"
    cloudfront_certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/22222222-2222-2222-2222-222222222222"
    enable_next_app_runtime    = true
    next_app_desired_count     = 0
    app_delivery_origin        = "next"
  }
}

run "reject_webhook_hosts_in_portfolio" {
  command         = plan
  expect_failures = [var.webhook_allowed_hosts]

  variables { webhook_allowed_hosts = ["hooks.customer.test"] }
}

run "reject_webhook_host_url" {
  command         = plan
  expect_failures = [var.webhook_allowed_hosts]

  variables {
    environment                = "beta"
    alb_certificate_arn        = "arn:aws:acm:eu-west-2:123456789012:certificate/11111111-1111-1111-1111-111111111111"
    cloudfront_certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/22222222-2222-2222-2222-222222222222"
    webhook_allowed_hosts      = ["https://hooks.customer.test/path"]
  }
}

run "reject_webhook_host_wildcard" {
  command         = plan
  expect_failures = [var.webhook_allowed_hosts]

  variables {
    environment                = "beta"
    alb_certificate_arn        = "arn:aws:acm:eu-west-2:123456789012:certificate/11111111-1111-1111-1111-111111111111"
    cloudfront_certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/22222222-2222-2222-2222-222222222222"
    webhook_allowed_hosts      = ["*.customer.test"]
  }
}

run "reject_webhook_host_ip_literal" {
  command         = plan
  expect_failures = [var.webhook_allowed_hosts]

  variables {
    environment                = "beta"
    alb_certificate_arn        = "arn:aws:acm:eu-west-2:123456789012:certificate/11111111-1111-1111-1111-111111111111"
    cloudfront_certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/22222222-2222-2222-2222-222222222222"
    webhook_allowed_hosts      = ["127.0.0.1"]
  }
}

run "reject_webhook_dispatcher_in_portfolio" {
  command         = plan
  expect_failures = [var.webhook_dispatcher_desired_count]

  variables { webhook_dispatcher_desired_count = 1 }
}

run "reject_webhook_dispatcher_without_hosts" {
  command         = plan
  expect_failures = [var.webhook_dispatcher_desired_count]

  variables {
    environment                      = "beta"
    alb_certificate_arn              = "arn:aws:acm:eu-west-2:123456789012:certificate/11111111-1111-1111-1111-111111111111"
    cloudfront_certificate_arn       = "arn:aws:acm:us-east-1:123456789012:certificate/22222222-2222-2222-2222-222222222222"
    webhook_dispatcher_desired_count = 1
  }
}

run "reject_beta_api_without_render_backlog_publisher" {
  command         = plan
  expect_failures = [var.webhook_dispatcher_desired_count]

  variables {
    environment                = "beta"
    alb_certificate_arn        = "arn:aws:acm:eu-west-2:123456789012:certificate/11111111-1111-1111-1111-111111111111"
    cloudfront_certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/22222222-2222-2222-2222-222222222222"
    api_desired_count          = 1
  }
}

run "reject_webhook_dispatcher_two" {
  command         = plan
  expect_failures = [var.webhook_dispatcher_desired_count]

  variables { webhook_dispatcher_desired_count = 2 }
}

run "reject_project_name_nineteen" {
  command         = plan
  expect_failures = [var.project_name]

  variables { project_name = "abcdefghijklmnopqrs" }
}

run "reject_malformed_expected_aws_account_id" {
  command         = plan
  expect_failures = [var.expected_aws_account_id]

  variables { expected_aws_account_id = "1234-not-an-account" }
}

run "reject_short_release_sha" {
  command         = plan
  expect_failures = [var.release_commit_sha]

  variables { release_commit_sha = "1111111" }
}

run "reject_uppercase_release_sha" {
  command         = plan
  expect_failures = [var.release_commit_sha]

  variables { release_commit_sha = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" }
}

run "reject_origin_wildcard" {
  command         = plan
  expect_failures = [var.origin_verify_active_value]

  variables {
    origin_verify_active_value    = "*aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    origin_verify_accepted_values = ["*aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]
  }
}

run "reject_origin_whitespace" {
  command         = plan
  expect_failures = [var.origin_verify_active_value]

  variables {
    origin_verify_active_value    = " aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    origin_verify_accepted_values = [" aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]
  }
}

run "reject_origin_uppercase" {
  command         = plan
  expect_failures = [var.origin_verify_active_value]

  variables {
    origin_verify_active_value    = "Aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    origin_verify_accepted_values = ["Aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]
  }
}

run "reject_origin_missing_active" {
  command         = plan
  expect_failures = [var.origin_verify_accepted_values]

  variables {
    origin_verify_accepted_values = ["bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]
  }
}

run "reject_origin_duplicates" {
  command         = plan
  expect_failures = [var.origin_verify_accepted_values]

  variables {
    origin_verify_accepted_values = [
      "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    ]
  }
}

run "reject_three_origin_values" {
  command         = plan
  expect_failures = [var.origin_verify_accepted_values]

  variables {
    origin_verify_accepted_values = [
      "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
    ]
  }
}
