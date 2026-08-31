resource "aws_sqs_queue" "dlq" {
  name                      = "${local.name}-dlq"
  message_retention_seconds = local.is_beta ? 1209600 : 345600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "work" {
  name                       = "${local.name}-work"
  visibility_timeout_seconds = 1800
  message_retention_seconds  = local.is_beta ? 345600 : 86400
  receive_wait_time_seconds  = 20
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = local.processing_job_max_attempts
  })
}

resource "aws_sqs_queue_redrive_allow_policy" "dlq" {
  queue_url = aws_sqs_queue.dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.work.arn]
  })
}

resource "aws_cloudwatch_metric_alarm" "dlq_visible" {
  alarm_name          = "${local.name}-dlq-visible"
  alarm_description   = "Mandatory v0.1 alarm: at least one message is visible in the DLQ"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = aws_sqs_queue.dlq.name
  }

  alarm_actions = [aws_sns_topic.operational_alerts.arn]
  ok_actions    = [aws_sns_topic.operational_alerts.arn]
}

# The investigation queue exists only in the not-yet-deployed beta stack.
# Legacy portfolio state and its v0.1 physical queue names remain untouched.
resource "aws_sqs_queue" "investigation_dlq" {
  count = local.is_beta ? 1 : 0

  name                      = "${local.name}-investigation-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "investigation" {
  count = local.is_beta ? 1 : 0

  name                       = "${local.name}-investigation"
  visibility_timeout_seconds = 1800
  message_retention_seconds  = 345600
  receive_wait_time_seconds  = 20
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.investigation_dlq[0].arn
    maxReceiveCount     = 3
  })
}

resource "aws_sqs_queue_redrive_allow_policy" "investigation_dlq" {
  count = local.is_beta ? 1 : 0

  queue_url = aws_sqs_queue.investigation_dlq[0].id
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.investigation[0].arn]
  })
}

resource "aws_cloudwatch_metric_alarm" "investigation_dlq_visible" {
  count = local.is_beta ? 1 : 0

  alarm_name          = "${local.name}-investigation-dlq-visible"
  alarm_description   = "At least one local-investigation message is visible in its DLQ"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = aws_sqs_queue.investigation_dlq[0].name
  }

  alarm_actions = [aws_sns_topic.operational_alerts.arn]
  ok_actions    = [aws_sns_topic.operational_alerts.arn]
}
