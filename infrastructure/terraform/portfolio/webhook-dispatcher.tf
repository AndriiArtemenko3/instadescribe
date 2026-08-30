# Beta-only outbox dispatcher. It reuses the immutable API image but has its
# own no-ingress service, execution role and narrowly scoped task role. The
# exact public event allowlist (which excludes render.requested) remains a
# code-level contract in app.services.webhook_dispatcher.

resource "aws_kms_key" "webhook_signing" {
  count = local.is_beta ? 1 : 0

  description             = "Encrypt out-of-band InstaDescribe beta webhook signing secrets"
  enable_key_rotation     = true
  deletion_window_in_days = 30
}

resource "aws_kms_alias" "webhook_signing" {
  count = local.is_beta ? 1 : 0

  name          = "alias/${local.name}-webhook-signing"
  target_key_id = aws_kms_key.webhook_signing[0].key_id
}

resource "aws_cloudwatch_log_group" "webhook_dispatcher" {
  count = local.is_beta ? 1 : 0

  name              = "/ecs/${local.name}/webhook-dispatcher"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "webhook_dispatcher_execution" {
  count = local.is_beta ? 1 : 0

  name               = "${local.name}-webhook-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role" "webhook_dispatcher_task" {
  count = local.is_beta ? 1 : 0

  name               = "${local.name}-webhook-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "webhook_dispatcher_execution" {
  count = local.is_beta ? 1 : 0

  statement {
    sid       = "EcrAuthorization"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "PullApiImage"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [aws_ecr_repository.api.arn]
  }

  statement {
    sid    = "WriteDispatcherLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.webhook_dispatcher[0].arn}:*"]
  }

  statement {
    sid       = "ReadDatabaseSecret"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_db_instance.postgres.master_user_secret[0].secret_arn]
  }
}

resource "aws_iam_role_policy" "webhook_dispatcher_execution" {
  count = local.is_beta ? 1 : 0

  name   = "runtime"
  role   = aws_iam_role.webhook_dispatcher_execution[0].id
  policy = data.aws_iam_policy_document.webhook_dispatcher_execution[0].json
}

data "aws_iam_policy_document" "webhook_dispatcher_task" {
  count = local.is_beta ? 1 : 0

  statement {
    sid       = "PublishRenderBacklogMetric"
    effect    = "Allow"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = [local.worker_metrics_namespace]
    }
  }

  statement {
    sid     = "DeleteExpiredSourceVersionsOnly"
    effect  = "Allow"
    actions = ["s3:DeleteObjectVersion"]
    resources = [
      local.source_video_object_arn,
      local.source_transcript_object_arn,
    ]
  }

  statement {
    sid       = "DeleteExpiredLegacySourceVersionsOnly"
    effect    = "Allow"
    actions   = ["s3:DeleteObjectVersion"]
    resources = [local.legacy_source_object_arn]
  }

  statement {
    sid       = "DeleteExpiredAnalysisArtifactVersionsOnly"
    effect    = "Allow"
    actions   = ["s3:DeleteObjectVersion"]
    resources = [local.attempt_artifact_object_arn]
  }

  statement {
    sid       = "DeleteExpiredDeliverableVersionsOnly"
    effect    = "Allow"
    actions   = ["s3:DeleteObjectVersion"]
    resources = [local.deliverable_object_arn]
  }

  statement {
    sid       = "ReadEndpointSigningSecretsOnly"
    effect    = "Allow"
    actions   = local.webhook_dispatcher_secret_actions
    resources = [local.webhook_signing_secret_arn_pattern]
  }

  statement {
    sid       = "DecryptEndpointSigningSecretsOnly"
    effect    = "Allow"
    actions   = local.webhook_dispatcher_kms_actions
    resources = [aws_kms_key.webhook_signing[0].arn]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["secretsmanager.${var.aws_region}.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "kms:EncryptionContext:SecretARN"
      values   = [local.webhook_signing_secret_arn_pattern]
    }
  }
}

resource "aws_iam_role_policy" "webhook_dispatcher_task" {
  count = local.is_beta ? 1 : 0

  name   = "dispatcher-runtime"
  role   = aws_iam_role.webhook_dispatcher_task[0].id
  policy = data.aws_iam_policy_document.webhook_dispatcher_task[0].json
}

resource "aws_security_group" "webhook_dispatcher" {
  count = local.is_beta ? 1 : 0

  name        = "${local.name}-webhook-dispatcher"
  description = "Webhook dispatcher tasks; no inbound rules"
  vpc_id      = aws_vpc.this.id

  tags = { Name = "${local.name}-webhook-dispatcher" }
}

resource "aws_vpc_security_group_egress_rule" "webhook_dispatcher_all" {
  count = local.is_beta ? 1 : 0

  security_group_id = aws_security_group.webhook_dispatcher[0].id
  description       = "PostgreSQL, AWS secret endpoints, DNS and operator-approved public HTTPS destinations"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_ingress_rule" "database_from_webhook_dispatcher" {
  count = local.is_beta ? 1 : 0

  security_group_id            = aws_security_group.database.id
  description                  = "PostgreSQL from the dedicated webhook dispatcher"
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
  referenced_security_group_id = aws_security_group.webhook_dispatcher[0].id
}

resource "aws_ecs_task_definition" "webhook_dispatcher" {
  count = local.is_beta ? 1 : 0

  family                   = "${local.name}-webhook-dispatcher"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.webhook_dispatcher_execution[0].arn
  task_role_arn            = aws_iam_role.webhook_dispatcher_task[0].arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  volume { name = "tmp" }

  container_definitions = jsonencode([local.webhook_dispatcher_container_definition])

  depends_on = [
    aws_iam_role_policy.webhook_dispatcher_execution,
    aws_iam_role_policy.webhook_dispatcher_task,
  ]
}

resource "aws_ecs_service" "webhook_dispatcher" {
  count = local.is_beta ? 1 : 0

  name            = "webhook-dispatcher"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.webhook_dispatcher[0].arn
  desired_count   = var.webhook_dispatcher_desired_count
  launch_type     = "FARGATE"

  platform_version                   = "LATEST"
  enable_execute_command             = false
  wait_for_steady_state              = false
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100
  enable_ecs_managed_tags            = true
  propagate_tags                     = "TASK_DEFINITION"

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.webhook_dispatcher[0].id]
    assign_public_ip = true
  }

  depends_on = [aws_ecs_cluster_capacity_providers.this]
}

resource "aws_cloudwatch_metric_alarm" "webhook_dispatcher_running" {
  count = local.is_beta && var.webhook_dispatcher_desired_count == 1 ? 1 : 0

  alarm_name          = "${local.name}-webhook-dispatcher-not-running"
  alarm_description   = "The enabled webhook dispatcher has fewer than one running task."
  namespace           = "ECS/ContainerInsights"
  metric_name         = "RunningTaskCount"
  dimensions          = { ClusterName = aws_ecs_cluster.this.name, ServiceName = aws_ecs_service.webhook_dispatcher[0].name }
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = local.beta_alarm_actions
}
