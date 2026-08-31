resource "aws_ecs_cluster" "this" {
  name = local.name

  setting {
    name  = "containerInsights"
    value = local.is_beta ? "enabled" : "disabled"
  }
}

resource "aws_ecs_cluster_capacity_providers" "this" {
  cluster_name       = aws_ecs_cluster.this.name
  capacity_providers = ["FARGATE"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    base              = 0
    weight            = 1
  }
}

resource "aws_ecs_task_definition" "api" {
  family                   = "${local.name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.api_execution.arn
  task_role_arn            = aws_iam_role.api_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    {
      name      = "api"
      image     = "${aws_ecr_repository.api.repository_url}:${local.api_image_tag}"
      essential = true
      command = [
        "/bin/sh",
        "-c",
        "${local.database_url_shell} exec uvicorn app.main:app --host 0.0.0.0 --port 8000",
      ]
      portMappings = [
        {
          name          = "http"
          containerPort = 8000
          hostPort      = 8000
          protocol      = "tcp"
        },
      ]
      environment = concat(
        local.container_environment,
        local.api_investigation_environment,
        local.api_browser_auth_environment,
        local.webhook_dispatcher_environment,
        [
          { name = "INSTADESCRIBE_ENABLE_DOCS", value = "0" },
          { name = "INSTADESCRIBE_REVIEW_BASE_URL", value = var.app_origin },
          {
            name  = "INSTADESCRIBE_ALLOWED_ORIGINS"
            value = jsonencode(["https://${aws_cloudfront_distribution.app.domain_name}"])
          },
        ],
      )
      secrets = local.api_runtime_secrets
      healthCheck = {
        command     = ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 20
      }
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.api.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "api"
        }
      }
      readonlyRootFilesystem = false
      user                   = "10001"
    },
  ])

  depends_on = [
    aws_iam_role_policy.api_execution,
    aws_secretsmanager_secret_version.portfolio_token_hash,
    aws_secretsmanager_secret.api_key_pepper,
  ]
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${local.name}-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "2048"
  memory                   = "8192"
  execution_role_arn       = aws_iam_role.worker_execution.arn
  task_role_arn            = aws_iam_role.worker_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    {
      name      = "worker"
      image     = "${aws_ecr_repository.worker.repository_url}:${local.worker_image_tag}"
      essential = true
      command = [
        "/bin/sh",
        "-c",
        "${local.database_url_shell} exec python -m instadescribe_worker.main",
      ]
      environment = local.worker_environment
      secrets     = local.worker_runtime_secrets
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.worker.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "worker"
        }
      }
      readonlyRootFilesystem = false
      user                   = "10001"
    },
  ])

  ephemeral_storage {
    size_in_gib = 40
  }

  depends_on = [
    aws_iam_role_policy.worker_execution,
    aws_iam_role_policy.worker_openai_secret,
  ]
}

resource "aws_ecs_task_definition" "migration" {
  family                   = "${local.name}-migration"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.migration_execution.arn
  task_role_arn            = aws_iam_role.migration_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([local.migration_container_definition])

  depends_on = [aws_iam_role_policy.migration_execution]
}

resource "aws_ecs_service" "api" {
  name            = "api"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.api_desired_count
  launch_type     = "FARGATE"

  platform_version                   = "LATEST"
  health_check_grace_period_seconds  = 60
  enable_execute_command             = false
  wait_for_steady_state              = false
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.api.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  depends_on = [
    aws_ecs_cluster_capacity_providers.this,
    aws_lb_listener_rule.cloudfront_origin,
  ]
}

resource "aws_ecs_service" "worker" {
  name            = "worker"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = var.worker_desired_count
  launch_type     = "FARGATE"

  platform_version                   = "LATEST"
  enable_execute_command             = false
  wait_for_steady_state              = false
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.worker.id]
    assign_public_ip = true
  }

  depends_on = [aws_ecs_cluster_capacity_providers.this]

  lifecycle {
    # In beta, Application Auto Scaling owns desired_count. The portfolio
    # environment still receives its initial explicit 0/1 count on creation.
    ignore_changes = [desired_count]
  }
}
