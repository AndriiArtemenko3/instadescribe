output "application_url" {
  description = "CloudFront URL for the selected Vite S3 or beta Next app delivery origin and same-origin JSON routes."
  value       = "https://${aws_cloudfront_distribution.app.domain_name}"
}

output "api_health_url" {
  description = "Public health alias routed through CloudFront to the protected ALB origin."
  value       = "https://${aws_cloudfront_distribution.app.domain_name}/api/healthz"
}

output "api_ready_url" {
  description = "Public readiness alias; verifies configuration and the database."
  value       = "https://${aws_cloudfront_distribution.app.domain_name}/api/readyz"
}

output "api_ecr_repository_url" {
  value = aws_ecr_repository.api.repository_url
}

output "worker_ecr_repository_url" {
  value = aws_ecr_repository.worker.repository_url
}

output "frontend_bucket" {
  value = aws_s3_bucket.frontend.bucket
}

output "media_bucket" {
  value = aws_s3_bucket.media.bucket
}

output "work_queue_url" {
  value = aws_sqs_queue.work.url
}

output "dlq_url" {
  value = aws_sqs_queue.dlq.url
}

output "investigation_queue" {
  description = "Beta-only queue identity for an explicitly operated local worker; null in the legacy portfolio stack."
  value = local.is_beta ? {
    url = aws_sqs_queue.investigation[0].url
    arn = aws_sqs_queue.investigation[0].arn
  } : null
}

output "investigation_dlq" {
  description = "Beta-only investigation dead-letter queue identity; null in the legacy portfolio stack."
  value = local.is_beta ? {
    url = aws_sqs_queue.investigation_dlq[0].url
    arn = aws_sqs_queue.investigation_dlq[0].arn
  } : null
}

output "rds_endpoint" {
  value = aws_db_instance.postgres.endpoint
}

output "rds_master_secret_arn" {
  value     = aws_db_instance.postgres.master_user_secret[0].secret_arn
  sensitive = true
}

output "portfolio_token_secret_arn" {
  value = aws_secretsmanager_secret.portfolio_token_hash.arn
}

output "openai_api_key_secret_arn" {
  description = "OpenAI secret shell; any value/version is managed out-of-band and never by Terraform."
  value       = aws_secretsmanager_secret.openai_api_key.arn
}

output "release_commit_sha" {
  description = "Single immutable provenance value used for API and worker image tags and job revision stamping."
  value       = var.release_commit_sha
}

output "ecs_cluster_name" {
  description = "Cluster used by the separately authorized one-shot migration run."
  value       = aws_ecs_cluster.this.name
}

output "migration_task_definition_arn" {
  description = "Immutable API-image Alembic task definition; run it before enabling the API service."
  value       = aws_ecs_task_definition.migration.arn
}

output "migration_log_group" {
  value = aws_cloudwatch_log_group.migration.name
}

output "migration_network_configuration" {
  description = "Inputs for ecs run-task; the migration has no inbound rule and needs a public IP for ECR/log/secret access."
  value = {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.migration.id]
    assign_public_ip = "ENABLED"
  }
}

output "api_desired_count" {
  description = "Zero during bootstrap/migration; one only after a successful migration and a separately reviewed apply."
  value       = aws_ecs_service.api.desired_count
}

output "api_service_name" {
  description = "ECS service verified after the separately authorized API enablement apply."
  value       = aws_ecs_service.api.name
}

output "worker_desired_count" {
  description = "Initial count for portfolio; runtime value is autoscaling-managed in beta."
  value       = aws_ecs_service.worker.desired_count
}

output "worker_autoscaling" {
  description = "Bounded queue-driven worker scaling contract."
  value = {
    enabled      = local.is_beta
    min_capacity = local.is_beta ? aws_appautoscaling_target.worker[0].min_capacity : 0
    max_capacity = local.is_beta ? aws_appautoscaling_target.worker[0].max_capacity : 0
  }
}

output "beta_identity" {
  description = "Non-secret Cognito/session resources for the Next BFF; null in portfolio mode."
  value = local.is_beta ? {
    user_pool_id             = aws_cognito_user_pool.beta[0].id
    user_pool_client_id      = aws_cognito_user_pool_client.next_bff[0].id
    cognito_domain           = aws_cognito_user_pool_domain.beta[0].domain
    session_table            = aws_dynamodb_table.web_sessions[0].name
    session_kms_key_arn      = aws_kms_key.web_sessions[0].arn
    next_runtime_secret      = aws_secretsmanager_secret.next_bff_runtime[0].arn
    browser_assertion_secret = aws_secretsmanager_secret.browser_assertion[0].arn
    api_key_pepper_arn       = aws_secretsmanager_secret.api_key_pepper[0].arn
  } : null
}

output "app_delivery_origin" {
  description = "Explicit browser-app origin switch; vite_static is the retained rollback in every portfolio plan."
  value       = var.app_delivery_origin
}

output "next_app_runtime" {
  description = "Beta-only Next standalone runtime and health contract; null unless explicitly provisioned."
  value = local.next_app_runtime_enabled ? {
    desired_count  = aws_ecs_service.next_app[0].desired_count
    service_name   = aws_ecs_service.next_app[0].name
    repository_url = aws_ecr_repository.next_app[0].repository_url
    image_tag      = local.next_app_image_tag
    log_group      = aws_cloudwatch_log_group.next_app[0].name
    target_group   = aws_lb_target_group.next_app[0].arn
    health_path    = "/login"
    bff_path       = "/api/bff/*"
  } : null
}

output "webhook_dispatcher_runtime" {
  description = "Beta-only no-ingress webhook dispatcher and out-of-band signing-secret boundary; null in portfolio."
  value = local.is_beta ? {
    desired_count         = aws_ecs_service.webhook_dispatcher[0].desired_count
    service_name          = aws_ecs_service.webhook_dispatcher[0].name
    log_group             = aws_cloudwatch_log_group.webhook_dispatcher[0].name
    signing_kms_key_arn   = aws_kms_key.webhook_signing[0].arn
    signing_secret_prefix = local.webhook_signing_secret_name_prefix
  } : null
}

output "beta_cognito_client_secret" {
  description = "Sensitive Next BFF client secret; wire to a runtime secret store, never frontend build variables."
  value       = local.is_beta ? aws_cognito_user_pool_client.next_bff[0].client_secret : null
  sensitive   = true
}

output "processing_contract" {
  description = "Non-secret server-authoritative provider, source-duration limit and paid-attempt bound derived from the G12 switch."
  value = {
    provider          = local.processing_provider
    max_duration_secs = local.processing_max_duration_secs
    max_attempts      = local.processing_job_max_attempts
  }
}
