locals {
  is_beta = var.environment == "beta"

  # Preserve every existing v0.1 Terraform address and physical resource
  # name. The not-yet-deployed beta stack receives the canonical product
  # prefix when callers retain the historical project_name default.
  effective_project_name = local.is_beta && var.project_name == "instascribe" ? "instadescribe" : var.project_name
  name                   = "${local.effective_project_name}-${var.environment}"
  effective_database_name = (
    local.is_beta && var.database_name == "instascribe" ? "instadescribe" : var.database_name
  )
  effective_database_username = (
    local.is_beta && var.database_username == "instascribe_admin" ? "instadescribe_admin" : var.database_username
  )

  # A dispatcher-owned aggregate metric wakes the 0–2 worker service for DB
  # render work after the analysis SQS queue has drained.
  worker_metrics_namespace = "InstaDescribe/${local.name}"

  # One opt-in switch derives the complete server-authoritative processing
  # contract. Portfolio G12 remains a two-minute real-provider smoke. The
  # beta tier admits the published 60-minute presets with a duration-derived
  # 180-call ceiling and a bounded two-hour subprocess deadline.
  processing_provider          = var.enable_g12_openai ? "openai" : "fake"
  processing_max_duration_secs = local.is_beta ? 3600 : (var.enable_g12_openai ? 120 : 300)
  processing_job_max_attempts  = var.enable_g12_openai ? 1 : 3
  processing_max_provider_calls = (
    var.enable_g12_openai && local.is_beta ? 180 : 6
  )
  processing_subprocess_timeout_secs = (
    var.enable_g12_openai && local.is_beta ? 7200 : 1500
  )
  # Five-format rendering has its own wall-clock process-tree deadline. It is
  # intentionally independent of the analysis subprocess timeout because its
  # lease may be renewed while a long media encode is healthy.
  render_timeout_secs = local.is_beta ? 7200 : 1800

  # All deployment tags derive from this one fail-closed provenance input.
  api_image_tag       = var.release_commit_sha
  next_app_image_tag  = var.release_commit_sha
  worker_image_tag    = var.release_commit_sha
  migration_image_tag = var.release_commit_sha

  next_app_runtime_enabled = local.is_beta && var.enable_next_app_runtime
  next_app_origin_active   = local.next_app_runtime_enabled && var.app_delivery_origin == "next"

  next_app_dynamodb_actions = [
    "dynamodb:DeleteItem",
    "dynamodb:GetItem",
    "dynamodb:PutItem",
    "dynamodb:UpdateItem",
  ]
  next_app_session_key_prefixes = ["s#*", "c#*"]
  next_app_kms_actions = [
    "kms:Decrypt",
    "kms:GenerateDataKey",
  ]
  next_app_cognito_actions = [
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
  ]

  attempt_artifact_object_arn  = "${aws_s3_bucket.media.arn}/jobs/*/attempts/*"
  source_video_object_arn      = "${aws_s3_bucket.media.arn}/uploads/orgs/*/jobs/*/source/*"
  source_transcript_object_arn = "${aws_s3_bucket.media.arn}/uploads/orgs/*/jobs/*/transcript/*"
  legacy_source_object_arn     = "${aws_s3_bucket.media.arn}/uploads/????????-????-????-????-????????????/source/*"
  deliverable_object_arn       = "${aws_s3_bucket.media.arn}/deliverables/orgs/*/jobs/*/attempts/*"
  preview_object_arn           = "${aws_s3_bucket.media.arn}/previews/orgs/*/jobs/*/requests/*/attempts/*/narration.mp3"

  # Browser investigation POSTs sign exactly one of these object-tag tiers.
  # The tag key and 1..30 values are mirrored by the API's fail-closed
  # presigner validation and by the media-bucket lifecycle rules.
  investigation_retention_tag_key = "instadescribe-retention-days"
  investigation_retention_tiers = {
    for days in range(1, 31) : tostring(days) => days
  }

  common_tags = merge(
    {
      Project     = "InstaDescribe"
      Environment = var.environment
      ManagedBy   = "Terraform"
      Release     = local.is_beta ? "api-first-beta" : "v0.1-cloud-core"
      CostPosture = local.is_beta ? "bounded-queue-autoscaling" : "ephemeral-72h-worker-off"
    },
    var.tags,
  )

  container_environment = [
    { name = "AWS_DEFAULT_REGION", value = var.aws_region },
    { name = "INSTADESCRIBE_MEDIA_BUCKET", value = aws_s3_bucket.media.bucket },
    { name = "INSTADESCRIBE_WORK_QUEUE_URL", value = aws_sqs_queue.work.url },
    { name = "INSTADESCRIBE_PIPELINE_REVISION", value = var.release_commit_sha },
    { name = "INSTADESCRIBE_PROVIDER", value = local.processing_provider },
    { name = "INSTADESCRIBE_MAX_DURATION_SECS", value = tostring(local.processing_max_duration_secs) },
    { name = "INSTADESCRIBE_MAX_ATTEMPTS", value = tostring(local.processing_job_max_attempts) },
    { name = "INSTADESCRIBE_MAX_PROVIDER_CALLS", value = tostring(local.processing_max_provider_calls) },
    { name = "INSTADESCRIBE_MAX_UPLOAD_BYTES", value = tostring(local.is_beta ? 1073741824 : 262144000) },
    { name = "INSTADESCRIBE_DEPLOYMENT_TIER", value = var.environment },
    { name = "DATABASE_HOST", value = aws_db_instance.postgres.address },
    { name = "DATABASE_PORT", value = tostring(aws_db_instance.postgres.port) },
    { name = "DATABASE_NAME", value = local.effective_database_name },
  ]

  # Browser API publication only. The existing Fargate audio-description
  # worker intentionally receives neither this URL nor consume permission;
  # local-worker deployment remains a separately authorized post-foundation step.
  api_investigation_environment = local.is_beta ? [
    {
      name  = "INSTADESCRIBE_INVESTIGATION_QUEUE_URL"
      value = aws_sqs_queue.investigation[0].url
    },
  ] : []

  worker_environment = concat(local.container_environment, [
    { name = "INSTADESCRIBE_WORKER_ID", value = "ecs-${var.release_commit_sha}" },
    { name = "INSTADESCRIBE_LONG_POLL_SECS", value = "20" },
    { name = "INSTADESCRIBE_SUBPROCESS_TIMEOUT_SECS", value = tostring(local.processing_subprocess_timeout_secs) },
    { name = "INSTADESCRIBE_RENDER_TIMEOUT_SECS", value = tostring(local.render_timeout_secs) },
    { name = "INSTADESCRIBE_RENDER_HEARTBEAT_INTERVAL_SECS", value = "15" },
    { name = "INSTADESCRIBE_RETRY_VISIBILITY_DELAY_SECS", value = "30" },
  ])

  database_secrets = [
    {
      name      = "DATABASE_USERNAME"
      valueFrom = "${aws_db_instance.postgres.master_user_secret[0].secret_arn}:username::"
    },
    {
      name      = "DATABASE_PASSWORD"
      valueFrom = "${aws_db_instance.postgres.master_user_secret[0].secret_arn}:password::"
    },
  ]

  # The OpenAI value is deliberately absent from Terraform and its state. G12
  # adds a value out-of-band to the pre-existing secret shell. Only the worker
  # task receives that secret reference; the API receives processing config,
  # never provider credentials.
  worker_openai_secrets = var.enable_g12_openai ? [
    {
      name      = "OPENAI_API_KEY"
      valueFrom = aws_secretsmanager_secret.openai_api_key.arn
    },
  ] : []

  api_runtime_secrets = concat(
    local.database_secrets,
    [
      {
        name      = "PORTFOLIO_TOKEN_SHA256"
        valueFrom = aws_secretsmanager_secret.portfolio_token_hash.arn
      },
    ],
    local.is_beta ? [
      {
        name      = "INSTADESCRIBE_API_KEY_PEPPER"
        valueFrom = aws_secretsmanager_secret.api_key_pepper[0].arn
      },
      {
        name      = "BROWSER_ASSERTION_SECRET"
        valueFrom = aws_secretsmanager_secret.browser_assertion[0].arn
      },
    ] : [],
  )
  worker_runtime_secrets = concat(local.database_secrets, local.worker_openai_secrets)

  webhook_dispatcher_environment = local.is_beta ? [
    {
      name  = "INSTADESCRIBE_WEBHOOK_ALLOWED_HOSTS"
      value = jsonencode(var.webhook_allowed_hosts)
    },
  ] : []

  webhook_dispatcher_task_environment = local.is_beta ? concat(
    [
      { name = "AWS_DEFAULT_REGION", value = var.aws_region },
      { name = "INSTADESCRIBE_MEDIA_BUCKET", value = aws_s3_bucket.media.bucket },
      { name = "DATABASE_HOST", value = aws_db_instance.postgres.address },
      { name = "DATABASE_PORT", value = tostring(aws_db_instance.postgres.port) },
      { name = "DATABASE_NAME", value = local.effective_database_name },
      { name = "INSTADESCRIBE_DEPLOYMENT_TIER", value = "beta" },
      { name = "INSTADESCRIBE_METRICS_NAMESPACE", value = local.worker_metrics_namespace },
      { name = "PYTHONDONTWRITEBYTECODE", value = "1" },
      { name = "HOME", value = "/tmp" },
    ],
    local.webhook_dispatcher_environment,
  ) : []
  webhook_dispatcher_runtime_secrets = local.is_beta ? local.database_secrets : []
  webhook_signing_secret_name_prefix = "${local.name}/webhook-signing/"
  webhook_signing_secret_arn_pattern = "arn:aws:secretsmanager:${var.aws_region}:${nonsensitive(var.expected_aws_account_id)}:secret:${local.webhook_signing_secret_name_prefix}*"
  webhook_dispatcher_secret_actions  = ["secretsmanager:GetSecretValue"]
  webhook_dispatcher_kms_actions     = ["kms:Decrypt"]

  cognito_issuer = local.is_beta ? "https://cognito-idp.${var.aws_region}.amazonaws.com/${aws_cognito_user_pool.beta[0].id}" : null
  api_browser_auth_environment = local.is_beta ? [
    { name = "COGNITO_ISSUER", value = local.cognito_issuer },
    { name = "COGNITO_USER_POOL_ID", value = aws_cognito_user_pool.beta[0].id },
    { name = "COGNITO_APP_CLIENT_ID", value = aws_cognito_user_pool_client.next_bff[0].id },
    { name = "COGNITO_JWKS_URL", value = "${local.cognito_issuer}/.well-known/jwks.json" },
  ] : []

  next_app_environment = local.next_app_runtime_enabled ? [
    { name = "NODE_ENV", value = "production" },
    { name = "NEXT_TELEMETRY_DISABLED", value = "1" },
    { name = "HOSTNAME", value = "0.0.0.0" },
    { name = "PORT", value = "3000" },
    { name = "AWS_REGION", value = var.aws_region },
    { name = "APP_ORIGIN", value = var.app_origin },
    { name = "APP_API_ORIGIN", value = var.api_origin },
    { name = "COGNITO_USER_POOL_ID", value = aws_cognito_user_pool.beta[0].id },
    { name = "COGNITO_APP_CLIENT_ID", value = aws_cognito_user_pool_client.next_bff[0].id },
    { name = "WEB_SESSION_TABLE_NAME", value = aws_dynamodb_table.web_sessions[0].name },
    { name = "WEB_SESSION_KMS_KEY_ID", value = aws_kms_key.web_sessions[0].arn },
  ] : []

  # One shell, no Terraform-managed value. Before next_app_desired_count=1,
  # populate both JSON keys out-of-band: the generated Cognito client secret
  # and an independent base64url-encoded random 32-byte session HMAC key.
  next_app_runtime_secrets = local.next_app_runtime_enabled ? [
    {
      name      = "COGNITO_APP_CLIENT_SECRET"
      valueFrom = "${aws_secretsmanager_secret.next_bff_runtime[0].arn}:COGNITO_APP_CLIENT_SECRET::"
    },
    {
      name      = "WEB_SESSION_HMAC_SECRET"
      valueFrom = "${aws_secretsmanager_secret.next_bff_runtime[0].arn}:WEB_SESSION_HMAC_SECRET::"
    },
    {
      name      = "BROWSER_ASSERTION_SECRET"
      valueFrom = aws_secretsmanager_secret.browser_assertion[0].arn
    },
  ] : []

  worker_openai_secret_statement = {
    Sid      = "ReadOpenAIKeyForWorkerOnly"
    Effect   = "Allow"
    Action   = ["secretsmanager:GetSecretValue"]
    Resource = [aws_secretsmanager_secret.openai_api_key.arn]
  }
  worker_openai_secret_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [local.worker_openai_secret_statement]
  })

  # Build the SQLAlchemy URL without ever placing a raw generated password in
  # a URI template. RDS-managed passwords may contain reserved URI characters;
  # URL.create performs the required escaping and the shell quotes the result.
  database_url_shell = "export DATABASE_URL=\"$(python -c 'import os; from sqlalchemy import URL; print(URL.create(\"postgresql+psycopg\", username=os.environ[\"DATABASE_USERNAME\"], password=os.environ[\"DATABASE_PASSWORD\"], host=os.environ[\"DATABASE_HOST\"], port=int(os.environ[\"DATABASE_PORT\"]), database=os.environ[\"DATABASE_NAME\"]).render_as_string(hide_password=False))')\";"

  migration_environment = [
    { name = "DATABASE_HOST", value = aws_db_instance.postgres.address },
    { name = "DATABASE_PORT", value = tostring(aws_db_instance.postgres.port) },
    { name = "DATABASE_NAME", value = local.effective_database_name },
  ]

  migration_container_definition = {
    name      = "migration"
    image     = "${aws_ecr_repository.api.repository_url}:${local.migration_image_tag}"
    essential = true
    command = [
      "/bin/sh",
      "-c",
      "${local.database_url_shell} alembic -c /srv/alembic.ini upgrade head && exec alembic -c /srv/alembic.ini current --check-heads",
    ]
    environment = local.migration_environment
    secrets     = local.database_secrets
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.migration.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "migration"
      }
    }
    readonlyRootFilesystem = false
    user                   = "10001"
  }

  webhook_dispatcher_container_definition = local.is_beta ? {
    name      = "webhook-dispatcher"
    image     = "${aws_ecr_repository.api.repository_url}:${local.api_image_tag}"
    essential = true
    command = [
      "/bin/sh",
      "-c",
      "${local.database_url_shell} exec python /srv/scripts/dispatch_webhooks.py",
    ]
    environment = local.webhook_dispatcher_task_environment
    secrets     = local.webhook_dispatcher_runtime_secrets
    mountPoints = [
      { sourceVolume = "tmp", containerPath = "/tmp", readOnly = false },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.webhook_dispatcher[0].name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "webhook-dispatcher"
      }
    }
    linuxParameters        = { initProcessEnabled = true }
    readonlyRootFilesystem = true
    stopTimeout            = 30
    user                   = "10001"
  } : null
}
