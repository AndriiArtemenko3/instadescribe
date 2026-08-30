locals {
  beta_alarm_actions = local.is_beta ? [aws_sns_topic.operational_alerts.arn] : []
}

resource "aws_cloudwatch_metric_alarm" "api_target_5xx" {
  count = local.is_beta ? 1 : 0

  alarm_name          = "${local.name}-api-target-5xx"
  alarm_description   = "FastAPI target returned five or more 5xx responses in five minutes."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  dimensions          = { LoadBalancer = aws_lb.api.arn_suffix, TargetGroup = aws_lb_target_group.api.arn_suffix }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 5
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.beta_alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "api_latency" {
  count = local.is_beta ? 1 : 0

  alarm_name          = "${local.name}-api-latency"
  alarm_description   = "FastAPI p95 target response time exceeded five seconds for ten minutes."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "TargetResponseTime"
  dimensions          = { LoadBalancer = aws_lb.api.arn_suffix, TargetGroup = aws_lb_target_group.api.arn_suffix }
  extended_statistic  = "p95"
  period              = 300
  evaluation_periods  = 2
  threshold           = 5
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.beta_alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "oldest_work_message" {
  count = local.is_beta ? 1 : 0

  alarm_name          = "${local.name}-oldest-work-message"
  alarm_description   = "The oldest queued job has waited more than fifteen minutes."
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateAgeOfOldestMessage"
  dimensions          = { QueueName = aws_sqs_queue.work.name }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 900
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.beta_alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "worker_cpu" {
  count = local.is_beta ? 1 : 0

  alarm_name          = "${local.name}-worker-cpu"
  alarm_description   = "A beta worker remained above 90 percent CPU for ten minutes."
  namespace           = "AWS/ECS"
  metric_name         = "CPUUtilization"
  dimensions          = { ClusterName = aws_ecs_cluster.this.name, ServiceName = aws_ecs_service.worker.name }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 2
  threshold           = 90
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.beta_alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "rds_free_storage" {
  count = local.is_beta ? 1 : 0

  alarm_name          = "${local.name}-rds-free-storage"
  alarm_description   = "PostgreSQL has less than five GiB of free storage."
  namespace           = "AWS/RDS"
  metric_name         = "FreeStorageSpace"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.postgres.identifier }
  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 5368709120
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = local.beta_alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "rds_connections" {
  count = local.is_beta ? 1 : 0

  alarm_name          = "${local.name}-rds-connections"
  alarm_description   = "PostgreSQL connection count exceeded the bounded beta threshold."
  namespace           = "AWS/RDS"
  metric_name         = "DatabaseConnections"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.postgres.identifier }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 75
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.beta_alarm_actions
}

# The API/worker emit these low-cardinality custom metrics. Defining the
# alarms here keeps the operational contract deployable before canary traffic;
# missing data is healthy because an idle beta has no events to report.
locals {
  beta_custom_alarms = {
    outbox_backlog = {
      metric      = "OutboxOldestSeconds"
      threshold   = 300
      description = "Transactional webhook outbox is more than five minutes behind."
    }
    webhook_exhausted = {
      metric      = "WebhookDeliveryExhausted"
      threshold   = 0
      description = "At least one terminal webhook exhausted its retry horizon."
    }
    expired_lease = {
      metric      = "ExpiredProcessingLeases"
      threshold   = 0
      description = "At least one processing job has an expired worker lease."
    }
    quota_rejected = {
      metric      = "QuotaRejected"
      threshold   = 10
      description = "More than ten requests were rejected by beta quotas in five minutes."
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "beta_custom" {
  for_each = local.is_beta ? local.beta_custom_alarms : {}

  alarm_name          = "${local.name}-${each.key}"
  alarm_description   = each.value.description
  namespace           = "InstaDescribe/Beta"
  metric_name         = each.value.metric
  dimensions          = { Environment = var.environment }
  statistic           = each.key == "outbox_backlog" ? "Maximum" : "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = each.value.threshold
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.beta_alarm_actions
}
