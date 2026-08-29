data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "api_execution" {
  name               = "${local.name}-api-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role" "worker_execution" {
  name               = "${local.name}-worker-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role" "migration_execution" {
  name               = "${local.name}-migration-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "api_execution" {
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
    sid    = "WriteApiLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.api.arn}:*"]
  }

  statement {
    sid     = "ReadApiRuntimeSecrets"
    effect  = "Allow"
    actions = ["secretsmanager:GetSecretValue"]
    resources = concat(
      [
        aws_db_instance.postgres.master_user_secret[0].secret_arn,
        aws_secretsmanager_secret.portfolio_token_hash.arn,
      ],
      local.is_beta ? [
        aws_secretsmanager_secret.api_key_pepper[0].arn,
        aws_secretsmanager_secret.browser_assertion[0].arn,
      ] : [],
    )
  }
}

resource "aws_iam_role_policy" "api_execution" {
  name   = "runtime"
  role   = aws_iam_role.api_execution.id
  policy = data.aws_iam_policy_document.api_execution.json
}

data "aws_iam_policy_document" "worker_execution" {
  statement {
    sid       = "EcrAuthorization"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "PullWorkerImage"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [aws_ecr_repository.worker.arn]
  }

  statement {
    sid    = "WriteWorkerLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.worker.arn}:*"]
  }

  statement {
    sid       = "ReadWorkerDatabaseSecret"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_db_instance.postgres.master_user_secret[0].secret_arn]
  }
}

resource "aws_iam_role_policy" "worker_execution" {
  name   = "runtime"
  role   = aws_iam_role.worker_execution.id
  policy = data.aws_iam_policy_document.worker_execution.json
}

# ECS resolves task-definition secrets through the execution role before the
# container starts. This separate, conditional policy makes the default fake
# deployment provably keyless and scopes G12 access to the one existing secret
# shell. No API or application task role receives this permission.
resource "aws_iam_role_policy" "worker_openai_secret" {
  count = var.enable_g12_openai ? 1 : 0

  name   = "openai-api-key"
  role   = aws_iam_role.worker_execution.id
  policy = local.worker_openai_secret_policy
}

data "aws_iam_policy_document" "migration_execution" {
  statement {
    sid       = "EcrAuthorization"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "PullMigrationImage"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [aws_ecr_repository.api.arn]
  }

  statement {
    sid    = "WriteMigrationLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.migration.arn}:*"]
  }

  statement {
    sid       = "ReadMigrationDatabaseSecret"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_db_instance.postgres.master_user_secret[0].secret_arn]
  }
}

resource "aws_iam_role_policy" "migration_execution" {
  name   = "runtime"
  role   = aws_iam_role.migration_execution.id
  policy = data.aws_iam_policy_document.migration_execution.json
}

resource "aws_iam_role" "api_task" {
  name               = "${local.name}-api-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role" "worker_task" {
  name               = "${local.name}-worker-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

# Alembic only needs network access to PostgreSQL. This intentionally empty
# role prevents the one-shot migration container from inheriting API S3/SQS
# permissions.
resource "aws_iam_role" "migration_task" {
  name               = "${local.name}-migration-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "api_task" {
  dynamic "statement" {
    for_each = local.is_beta ? [1] : []

    content {
      sid    = "ProvisionInvitedHumanUsers"
      effect = "Allow"
      actions = [
        "cognito-idp:AdminCreateUser",
        "cognito-idp:AdminDeleteUser",
        "cognito-idp:AdminGetUser",
      ]
      resources = [aws_cognito_user_pool.beta[0].arn]
    }
  }

  statement {
    sid    = "CreateAndVerifySourceUploads"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:GetObjectVersion",
      "s3:PutObject",
    ]
    resources = ["${aws_s3_bucket.media.arn}/uploads/*"]
  }

  statement {
    sid    = "SignManifestArtifactReads"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:GetObjectVersion",
    ]
    resources = [local.attempt_artifact_object_arn]
  }

  statement {
    sid    = "SignTenantDeliverableReads"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:GetObjectVersion",
    ]
    resources = [local.deliverable_object_arn]
  }

  statement {
    sid    = "SignTenantTtsPreviewReads"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:GetObjectVersion",
    ]
    resources = [local.preview_object_arn]
  }

  statement {
    sid    = "PublishWork"
    effect = "Allow"
    actions = [
      "sqs:GetQueueUrl",
      "sqs:SendMessage",
    ]
    resources = [aws_sqs_queue.work.arn]
  }
}

resource "aws_iam_role_policy" "api_task" {
  name   = "media-and-publish"
  role   = aws_iam_role.api_task.id
  policy = data.aws_iam_policy_document.api_task.json
}

data "aws_iam_policy_document" "worker_task" {
  statement {
    sid    = "ReadExactSourceVersion"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:GetObjectVersion",
    ]
    resources = ["${aws_s3_bucket.media.arn}/uploads/*"]
  }

  statement {
    sid       = "WriteAttemptScopedArtifacts"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = [local.attempt_artifact_object_arn]

    condition {
      test     = "StringEquals"
      variable = "s3:x-amz-server-side-encryption"
      values   = ["AES256"]
    }
  }

  statement {
    sid    = "ReadAttemptScopedArtifactsForRender"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:GetObjectVersion",
    ]
    resources = [local.attempt_artifact_object_arn]
  }

  statement {
    sid       = "WriteTenantScopedDeliverables"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = [local.deliverable_object_arn]

    condition {
      test     = "StringEquals"
      variable = "s3:x-amz-server-side-encryption"
      values   = ["AES256"]
    }
  }

  statement {
    sid       = "DeleteOrphanedRenderAttemptVersions"
    effect    = "Allow"
    actions   = ["s3:DeleteObjectVersion"]
    resources = [local.deliverable_object_arn]
  }

  statement {
    sid       = "WriteTenantScopedTtsPreviews"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = [local.preview_object_arn]

    condition {
      test     = "StringEquals"
      variable = "s3:x-amz-server-side-encryption"
      values   = ["AES256"]
    }
  }

  statement {
    sid       = "DeleteExactTtsPreviewVersions"
    effect    = "Allow"
    actions   = ["s3:DeleteObjectVersion"]
    resources = [local.preview_object_arn]
  }

  statement {
    sid    = "ConsumeWork"
    effect = "Allow"
    actions = [
      "sqs:ChangeMessageVisibility",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
      "sqs:GetQueueUrl",
      "sqs:ReceiveMessage",
    ]
    resources = [aws_sqs_queue.work.arn]
  }
}

resource "aws_iam_role_policy" "worker_task" {
  name   = "media-and-consume"
  role   = aws_iam_role.worker_task.id
  policy = data.aws_iam_policy_document.worker_task.json
}
