# Beta-only Next App Router runtime. Creation, warm-up and CloudFront cutover
# are intentionally separate inputs; the versioned Vite S3 origin is never
# removed and remains the one-step delivery rollback.

resource "aws_secretsmanager_secret" "next_bff_runtime" {
  count = local.is_beta ? 1 : 0

  name                    = "${local.name}/next-bff-runtime"
  description             = "Out-of-band JSON keys COGNITO_APP_CLIENT_SECRET and WEB_SESSION_HMAC_SECRET; Terraform manages no value"
  recovery_window_in_days = 30
}

resource "aws_ecr_repository" "next_app" {
  count = local.next_app_runtime_enabled ? 1 : 0

  name                 = "${local.name}-next-app"
  image_tag_mutability = "IMMUTABLE"

  encryption_configuration {
    encryption_type = "AES256"
  }

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "next_app" {
  count = local.next_app_runtime_enabled ? 1 : 0

  repository = aws_ecr_repository.next_app[0].name
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged Next images after seven days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 7
        }
        action = { type = "expire" }
      },
    ]
  })
}

resource "aws_cloudwatch_log_group" "next_app" {
  count = local.next_app_runtime_enabled ? 1 : 0

  name              = "/ecs/${local.name}/next-app"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "next_app_execution" {
  count = local.next_app_runtime_enabled ? 1 : 0

  name               = "${local.name}-next-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role" "next_app_task" {
  count = local.next_app_runtime_enabled ? 1 : 0

  name               = "${local.name}-next-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "next_app_execution" {
  count = local.next_app_runtime_enabled ? 1 : 0

  statement {
    sid       = "EcrAuthorization"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "PullNextImage"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [aws_ecr_repository.next_app[0].arn]
  }

  statement {
    sid    = "WriteNextLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.next_app[0].arn}:*"]
  }

  statement {
    sid     = "ReadNextRuntimeSecret"
    effect  = "Allow"
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      aws_secretsmanager_secret.next_bff_runtime[0].arn,
      aws_secretsmanager_secret.browser_assertion[0].arn,
    ]
  }
}

resource "aws_iam_role_policy" "next_app_execution" {
  count = local.next_app_runtime_enabled ? 1 : 0

  name   = "runtime"
  role   = aws_iam_role.next_app_execution[0].id
  policy = data.aws_iam_policy_document.next_app_execution[0].json
}

data "aws_iam_policy_document" "next_app_task" {
  count = local.next_app_runtime_enabled ? 1 : 0

  statement {
    sid       = "OpaqueSessionRecordsOnly"
    effect    = "Allow"
    actions   = local.next_app_dynamodb_actions
    resources = [aws_dynamodb_table.web_sessions[0].arn]

    condition {
      test     = "ForAllValues:StringLike"
      variable = "dynamodb:LeadingKeys"
      values   = local.next_app_session_key_prefixes
    }
  }

  statement {
    sid       = "SessionTokenEnvelopeOnly"
    effect    = "Allow"
    actions   = local.next_app_kms_actions
    resources = [aws_kms_key.web_sessions[0].arn]

    condition {
      test     = "StringEquals"
      variable = "kms:EncryptionContext:purpose"
      values   = ["session-tokens"]
    }

    condition {
      test     = "StringLike"
      variable = "kms:EncryptionContext:recordKey"
      values   = ["s#*"]
    }
  }

  statement {
    sid       = "AuthChallengeEnvelopeOnly"
    effect    = "Allow"
    actions   = local.next_app_kms_actions
    resources = [aws_kms_key.web_sessions[0].arn]

    condition {
      test     = "StringEquals"
      variable = "kms:EncryptionContext:purpose"
      values   = ["auth-challenge"]
    }

    condition {
      test     = "StringLike"
      variable = "kms:EncryptionContext:recordKey"
      values   = ["c#*"]
    }
  }

  # These user-pool APIs are the complete provider surface used by the BFF.
  # No Admin*, user mutation, list or service-account action is granted.
  statement {
    sid       = "CognitoBrowserSessionOnly"
    effect    = "Allow"
    actions   = local.next_app_cognito_actions
    resources = [aws_cognito_user_pool.beta[0].arn]
  }
}

resource "aws_iam_role_policy" "next_app_task" {
  count = local.next_app_runtime_enabled ? 1 : 0

  name   = "browser-session-only"
  role   = aws_iam_role.next_app_task[0].id
  policy = data.aws_iam_policy_document.next_app_task[0].json
}

resource "aws_security_group" "next_app" {
  count = local.next_app_runtime_enabled ? 1 : 0

  name        = "${local.name}-next-app"
  description = "Next BFF tasks; inbound only from the ALB"
  vpc_id      = aws_vpc.this.id

  tags = { Name = "${local.name}-next-app" }
}

resource "aws_vpc_security_group_ingress_rule" "next_app_from_alb" {
  count = local.next_app_runtime_enabled ? 1 : 0

  security_group_id            = aws_security_group.next_app[0].id
  description                  = "Next port from ALB"
  from_port                    = 3000
  to_port                      = 3000
  ip_protocol                  = "tcp"
  referenced_security_group_id = aws_security_group.alb.id
}

resource "aws_vpc_security_group_egress_rule" "alb_to_next_app" {
  count = local.next_app_runtime_enabled ? 1 : 0

  security_group_id            = aws_security_group.alb.id
  description                  = "ALB target traffic to Next"
  from_port                    = 3000
  to_port                      = 3000
  ip_protocol                  = "tcp"
  referenced_security_group_id = aws_security_group.next_app[0].id
}

resource "aws_vpc_security_group_egress_rule" "next_app_all" {
  count = local.next_app_runtime_enabled ? 1 : 0

  security_group_id = aws_security_group.next_app[0].id
  description       = "AWS Cognito/DynamoDB/KMS/Secrets endpoints and canonical HTTPS App API"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_lb_target_group" "next_app" {
  count = local.next_app_runtime_enabled ? 1 : 0

  name        = "${local.name}-web"
  port        = 3000
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.this.id

  deregistration_delay = 30

  health_check {
    enabled             = true
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 30
    timeout             = 5
    matcher             = "200"
    path                = "/login"
    protocol            = "HTTP"
    port                = "traffic-port"
  }

  tags = { Name = "${local.name}-web" }
}

resource "aws_lb_listener_rule" "next_app" {
  count = local.next_app_runtime_enabled ? 1 : 0

  listener_arn = aws_lb_listener.https[0].arn
  priority     = 90

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.next_app[0].arn
  }

  condition {
    http_header {
      http_header_name = "X-InstaDescribe-Origin-Verify"
      values           = jsondecode(aws_secretsmanager_secret_version.origin_verify_accepted.secret_string)
    }
  }

  condition {
    http_header {
      http_header_name = "X-InstaDescribe-Origin-Target"
      values           = ["next-app"]
    }
  }
}

resource "aws_ecs_task_definition" "next_app" {
  count = local.next_app_runtime_enabled ? 1 : 0

  family                   = "${local.name}-next-app"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.next_app_execution[0].arn
  task_role_arn            = aws_iam_role.next_app_task[0].arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  volume { name = "next-cache" }
  volume { name = "tmp" }

  container_definitions = jsonencode([
    {
      name      = "next-app"
      image     = "${aws_ecr_repository.next_app[0].repository_url}:${local.next_app_image_tag}"
      essential = true
      portMappings = [
        {
          name          = "http"
          containerPort = 3000
          hostPort      = 3000
          protocol      = "tcp"
          appProtocol   = "http"
        },
      ]
      environment = local.next_app_environment
      secrets     = local.next_app_runtime_secrets
      mountPoints = [
        { sourceVolume = "next-cache", containerPath = "/workspace/App/.next/cache", readOnly = false },
        { sourceVolume = "tmp", containerPath = "/tmp", readOnly = false },
      ]
      healthCheck = {
        command = [
          "CMD",
          "node",
          "-e",
          "fetch('http://127.0.0.1:3000/login').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))",
        ]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 30
      }
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.next_app[0].name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "next-app"
        }
      }
      linuxParameters        = { initProcessEnabled = true }
      readonlyRootFilesystem = true
      stopTimeout            = 30
      user                   = "10001"
    },
  ])

  depends_on = [
    aws_iam_role_policy.next_app_execution,
    aws_iam_role_policy.next_app_task,
  ]
}

resource "aws_ecs_service" "next_app" {
  count = local.next_app_runtime_enabled ? 1 : 0

  name            = "next-app"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.next_app[0].arn
  desired_count   = var.next_app_desired_count
  launch_type     = "FARGATE"

  platform_version                   = "LATEST"
  health_check_grace_period_seconds  = 60
  enable_execute_command             = false
  wait_for_steady_state              = false
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  enable_ecs_managed_tags            = true
  propagate_tags                     = "TASK_DEFINITION"

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.next_app[0].id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.next_app[0].arn
    container_name   = "next-app"
    container_port   = 3000
  }

  depends_on = [
    aws_ecs_cluster_capacity_providers.this,
    aws_lb_listener_rule.next_app,
  ]
}

resource "aws_cloudwatch_metric_alarm" "next_app_unhealthy" {
  count = local.next_app_runtime_enabled ? 1 : 0

  alarm_name          = "${local.name}-next-unhealthy"
  alarm_description   = "At least one Next target is unhealthy for two minutes."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "UnHealthyHostCount"
  dimensions          = { LoadBalancer = aws_lb.api.arn_suffix, TargetGroup = aws_lb_target_group.next_app[0].arn_suffix }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = var.next_app_desired_count == 0 ? "notBreaching" : "breaching"
  alarm_actions       = local.beta_alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "next_app_target_5xx" {
  count = local.next_app_runtime_enabled ? 1 : 0

  alarm_name          = "${local.name}-next-target-5xx"
  alarm_description   = "Next returned five or more target 5xx responses in five minutes."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  dimensions          = { LoadBalancer = aws_lb.api.arn_suffix, TargetGroup = aws_lb_target_group.next_app[0].arn_suffix }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 5
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.beta_alarm_actions
}
