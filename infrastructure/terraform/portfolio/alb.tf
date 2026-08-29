resource "aws_lb" "api" {
  name               = "${local.name}-api"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  enable_deletion_protection = false
  drop_invalid_header_fields = true
  idle_timeout               = 60

  tags = { Name = "${local.name}-api" }
}

resource "aws_lb_target_group" "api" {
  name        = "${local.name}-api"
  port        = 8000
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
    path                = "/healthz"
    protocol            = "HTTP"
    port                = "traffic-port"
  }

  tags = { Name = "${local.name}-api" }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.api.arn
  port              = 80
  protocol          = "HTTP"

  # Direct requests and CloudFront requests without the private origin header
  # fail closed. Network ingress is independently limited by the managed
  # CloudFront origin-facing prefix list in vpc.tf.
  default_action {
    type = "fixed-response"

    fixed_response {
      content_type = "application/json"
      message_body = "{\"detail\":\"forbidden\"}"
      status_code  = "403"
    }
  }
}

resource "aws_lb_listener" "https" {
  count = local.is_beta ? 1 : 0

  load_balancer_arn = aws_lb.api.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.alb_certificate_arn

  default_action {
    type = "fixed-response"

    fixed_response {
      content_type = "application/json"
      message_body = "{\"detail\":\"forbidden\"}"
      status_code  = "403"
    }
  }
}

resource "aws_lb_listener_rule" "cloudfront_origin" {
  listener_arn = local.is_beta ? aws_lb_listener.https[0].arn : aws_lb_listener.http.arn
  priority     = 100

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }

  condition {
    http_header {
      http_header_name = "X-InstaDescribe-Origin-Verify"
      values           = jsondecode(aws_secretsmanager_secret_version.origin_verify_accepted.secret_string)
    }
  }
}
