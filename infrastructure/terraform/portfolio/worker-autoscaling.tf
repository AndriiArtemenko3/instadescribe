resource "aws_appautoscaling_target" "worker" {
  count = local.is_beta ? 1 : 0

  max_capacity       = 2
  min_capacity       = 0
  resource_id        = "service/${aws_ecs_cluster.this.name}/${aws_ecs_service.worker.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "worker_scale_out" {
  count = local.is_beta ? 1 : 0

  name               = "${local.name}-worker-scale-out"
  policy_type        = "StepScaling"
  resource_id        = aws_appautoscaling_target.worker[0].resource_id
  scalable_dimension = aws_appautoscaling_target.worker[0].scalable_dimension
  service_namespace  = aws_appautoscaling_target.worker[0].service_namespace

  step_scaling_policy_configuration {
    adjustment_type         = "ChangeInCapacity"
    cooldown                = 60
    metric_aggregation_type = "Maximum"

    step_adjustment {
      metric_interval_lower_bound = 0
      scaling_adjustment          = 1
    }
  }
}

resource "aws_appautoscaling_policy" "worker_scale_in" {
  count = local.is_beta ? 1 : 0

  name               = "${local.name}-worker-scale-in"
  policy_type        = "StepScaling"
  resource_id        = aws_appautoscaling_target.worker[0].resource_id
  scalable_dimension = aws_appautoscaling_target.worker[0].scalable_dimension
  service_namespace  = aws_appautoscaling_target.worker[0].service_namespace

  step_scaling_policy_configuration {
    adjustment_type         = "ExactCapacity"
    cooldown                = 300
    metric_aggregation_type = "Maximum"

    step_adjustment {
      metric_interval_upper_bound = 1
      scaling_adjustment          = 0
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "worker_backlog_present" {
  count = local.is_beta ? 1 : 0

  alarm_name          = "${local.name}-worker-backlog-present"
  alarm_description   = "Start or grow the bounded worker service when analysis or render work exists."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 0
  treat_missing_data  = "notBreaching"

  metric_query {
    id          = "backlog"
    expression  = "visible + inflight + render"
    label       = "Total work backlog"
    return_data = true
  }

  metric_query {
    id = "visible"
    metric {
      namespace   = "AWS/SQS"
      metric_name = "ApproximateNumberOfMessagesVisible"
      period      = 60
      stat        = "Maximum"
      dimensions  = { QueueName = aws_sqs_queue.work.name }
    }
  }

  metric_query {
    id = "inflight"
    metric {
      namespace   = "AWS/SQS"
      metric_name = "ApproximateNumberOfMessagesNotVisible"
      period      = 60
      stat        = "Maximum"
      dimensions  = { QueueName = aws_sqs_queue.work.name }
    }
  }

  metric_query {
    id = "render"
    metric {
      namespace   = local.worker_metrics_namespace
      metric_name = "RenderBacklog"
      period      = 60
      stat        = "Maximum"
    }
  }

  alarm_actions = [aws_appautoscaling_policy.worker_scale_out[0].arn]
}

resource "aws_cloudwatch_metric_alarm" "worker_backlog_empty" {
  count = local.is_beta ? 1 : 0

  alarm_name          = "${local.name}-worker-backlog-empty"
  alarm_description   = "Scale the beta worker service to zero only after five empty minutes."
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 5
  datapoints_to_alarm = 5
  threshold           = 1
  # A missing custom render metric is an observability failure, not proof that
  # the durable backlog is empty. Fail safe by retaining current capacity.
  treat_missing_data = "notBreaching"

  metric_query {
    id          = "backlog"
    expression  = "visible + inflight + render"
    label       = "Total work backlog"
    return_data = true
  }

  metric_query {
    id = "visible"
    metric {
      namespace   = "AWS/SQS"
      metric_name = "ApproximateNumberOfMessagesVisible"
      period      = 60
      stat        = "Maximum"
      dimensions  = { QueueName = aws_sqs_queue.work.name }
    }
  }

  metric_query {
    id = "inflight"
    metric {
      namespace   = "AWS/SQS"
      metric_name = "ApproximateNumberOfMessagesNotVisible"
      period      = 60
      stat        = "Maximum"
      dimensions  = { QueueName = aws_sqs_queue.work.name }
    }
  }

  metric_query {
    id = "render"
    metric {
      namespace   = local.worker_metrics_namespace
      metric_name = "RenderBacklog"
      period      = 60
      stat        = "Maximum"
    }
  }

  alarm_actions = [aws_appautoscaling_policy.worker_scale_in[0].arn]
}
