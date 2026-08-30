resource "aws_cloudfront_origin_access_control" "frontend" {
  name                              = "${local.name}-frontend"
  description                       = "Private S3 frontend origin"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_function" "spa_rewrite" {
  name    = "${local.name}-spa-rewrite"
  runtime = "cloudfront-js-2.0"
  comment = "Rewrite extensionless static routes to index.html; /api/* uses a separate behavior"
  publish = true
  code    = <<-JAVASCRIPT
    function handler(event) {
      var request = event.request;
      if (request.method === 'GET' && request.uri.indexOf('.') === -1) {
        request.uri = '/index.html';
      }
      return request;
    }
  JAVASCRIPT
}

resource "aws_cloudfront_function" "integration_api_rewrite" {
  count = local.is_beta ? 1 : 0

  name    = "${local.name}-integration-api-rewrite"
  runtime = "cloudfront-js-2.0"
  comment = "Map public /v1/* to the isolated FastAPI integration router"
  publish = true
  code    = <<-JAVASCRIPT
    function handler(event) {
      var request = event.request;
      if (request.uri.indexOf('/v1/') === 0) {
        request.uri = '/api/integrations/v1/' + request.uri.slice(4);
      }
      return request;
    }
  JAVASCRIPT
}

resource "aws_cloudfront_cache_policy" "static" {
  name        = "${local.name}-static"
  default_ttl = 3600
  max_ttl     = 31536000
  min_ttl     = 0

  parameters_in_cache_key_and_forwarded_to_origin {
    enable_accept_encoding_brotli = true
    enable_accept_encoding_gzip   = true

    cookies_config { cookie_behavior = "none" }
    headers_config { header_behavior = "none" }
    query_strings_config { query_string_behavior = "none" }
  }
}

resource "aws_cloudfront_cache_policy" "api_disabled" {
  name        = "${local.name}-api-disabled"
  default_ttl = 0
  max_ttl     = 0
  min_ttl     = 0

  parameters_in_cache_key_and_forwarded_to_origin {
    enable_accept_encoding_brotli = false
    enable_accept_encoding_gzip   = false

    cookies_config { cookie_behavior = "none" }
    headers_config { header_behavior = "none" }
    query_strings_config { query_string_behavior = "none" }
  }
}

# A zero-TTL policy is separate from the API policy so browser cookies remain
# part of the defensive cache key even if a future edit accidentally raises a
# TTL. The response policy below independently forces private/no-store.
resource "aws_cloudfront_cache_policy" "next_private_disabled" {
  count = local.next_app_runtime_enabled ? 1 : 0

  name        = "${local.name}-next-private-disabled"
  default_ttl = 0
  max_ttl     = 0
  min_ttl     = 0

  parameters_in_cache_key_and_forwarded_to_origin {
    enable_accept_encoding_brotli = true
    enable_accept_encoding_gzip   = true

    cookies_config { cookie_behavior = "all" }
    headers_config { header_behavior = "none" }
    query_strings_config { query_string_behavior = "all" }
  }
}

resource "aws_cloudfront_origin_request_policy" "api" {
  name = "${local.name}-api"

  cookies_config { cookie_behavior = "none" }

  headers_config {
    header_behavior = "whitelist"
    headers {
      items = [
        "Authorization",
        "Content-Type",
        "Host",
        "Idempotency-Key",
        "If-Match",
        "Origin",
        "Access-Control-Request-Headers",
        "Access-Control-Request-Method",
        "X-Request-Id",
        "X-InstaDescribe-Browser-Assertion",
        "X-Portfolio-Token",
      ]
    }
  }

  query_strings_config { query_string_behavior = "all" }
}

resource "aws_cloudfront_origin_request_policy" "next_app" {
  count = local.next_app_runtime_enabled ? 1 : 0

  name = "${local.name}-next-app"

  cookies_config { cookie_behavior = "all" }

  headers_config {
    header_behavior = "whitelist"
    headers {
      items = [
        "Accept",
        "Accept-Language",
        "Content-Type",
        "Host",
        "Idempotency-Key",
        "If-Match",
        "Next-Router-Prefetch",
        "Next-Router-Segment-Prefetch",
        "Next-Router-State-Tree",
        "Next-Url",
        "Origin",
        "Purpose",
        "RSC",
        "Sec-Fetch-Dest",
        "Sec-Fetch-Mode",
        "Sec-Fetch-Site",
        "Sec-Fetch-User",
        "Sec-Purpose",
        "User-Agent",
        "X-CSRF-Token",
      ]
    }
  }

  query_strings_config { query_string_behavior = "all" }
}

resource "aws_cloudfront_response_headers_policy" "next_private" {
  count = local.next_app_runtime_enabled ? 1 : 0

  name = "${local.name}-next-private"

  custom_headers_config {
    items {
      header   = "Cache-Control"
      override = true
      value    = "private, no-store, max-age=0, must-revalidate"
    }
    items {
      header   = "Pragma"
      override = true
      value    = "no-cache"
    }
  }

  security_headers_config {
    content_type_options { override = true }
    frame_options {
      frame_option = "DENY"
      override     = true
    }
    referrer_policy {
      referrer_policy = "no-referrer"
      override        = true
    }
    strict_transport_security {
      access_control_max_age_sec = 31536000
      include_subdomains         = true
      override                   = true
      preload                    = true
    }
  }
}

resource "aws_cloudfront_distribution" "app" {
  enabled         = true
  is_ipv6_enabled = true
  comment         = "InstaDescribe ephemeral v0.1 portfolio evidence environment"
  price_class     = "PriceClass_100"
  http_version    = "http2and3"
  aliases = local.is_beta ? [
    trimprefix(var.app_origin, "https://"),
    trimprefix(var.api_origin, "https://"),
  ] : []
  web_acl_id = local.is_beta ? aws_wafv2_web_acl.beta[0].arn : null

  origin {
    domain_name              = aws_s3_bucket.frontend.bucket_regional_domain_name
    origin_id                = "frontend-s3"
    origin_access_control_id = aws_cloudfront_origin_access_control.frontend.id
  }

  origin {
    domain_name = aws_lb.api.dns_name
    origin_id   = "fastapi-alb"

    custom_header {
      name  = "X-InstaDescribe-Origin-Verify"
      value = aws_secretsmanager_secret_version.origin_verify_active.secret_string
    }

    custom_origin_config {
      http_port                = 80
      https_port               = 443
      origin_protocol_policy   = local.is_beta ? "https-only" : "http-only"
      origin_ssl_protocols     = ["TLSv1.2"]
      origin_read_timeout      = 60
      origin_keepalive_timeout = 5
    }
  }

  dynamic "origin" {
    for_each = local.next_app_runtime_enabled ? [1] : []

    content {
      domain_name = aws_lb.api.dns_name
      origin_id   = "next-app-alb"

      custom_header {
        name  = "X-InstaDescribe-Origin-Verify"
        value = aws_secretsmanager_secret_version.origin_verify_active.secret_string
      }

      custom_header {
        name  = "X-InstaDescribe-Origin-Target"
        value = "next-app"
      }

      custom_origin_config {
        http_port                = 80
        https_port               = 443
        origin_protocol_policy   = "https-only"
        origin_ssl_protocols     = ["TLSv1.2"]
        origin_read_timeout      = 30
        origin_keepalive_timeout = 5
      }
    }
  }

  default_root_object = local.next_app_origin_active ? null : "index.html"

  default_cache_behavior {
    target_origin_id       = local.next_app_origin_active ? "next-app-alb" : "frontend-s3"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods = local.next_app_origin_active ? [
      "DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT",
    ] : ["GET", "HEAD", "OPTIONS"]
    cached_methods = ["GET", "HEAD"]
    compress       = true
    cache_policy_id = local.next_app_origin_active ? (
      aws_cloudfront_cache_policy.next_private_disabled[0].id
    ) : aws_cloudfront_cache_policy.static.id
    origin_request_policy_id = local.next_app_origin_active ? (
      aws_cloudfront_origin_request_policy.next_app[0].id
    ) : null
    response_headers_policy_id = local.next_app_origin_active ? (
      aws_cloudfront_response_headers_policy.next_private[0].id
    ) : null

    dynamic "function_association" {
      for_each = local.next_app_origin_active ? [] : [1]

      content {
        event_type   = "viewer-request"
        function_arn = aws_cloudfront_function.spa_rewrite.arn
      }
    }
  }

  # Only the same-origin JSON BFF is routed to Next. The broader /api/*
  # behavior remains FastAPI, and media bytes continue to use direct signed
  # S3 URLs rather than traversing either application runtime.
  dynamic "ordered_cache_behavior" {
    for_each = local.next_app_runtime_enabled ? [1] : []

    content {
      path_pattern               = "/api/bff/*"
      target_origin_id           = "next-app-alb"
      viewer_protocol_policy     = "https-only"
      allowed_methods            = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
      cached_methods             = ["GET", "HEAD"]
      compress                   = true
      cache_policy_id            = aws_cloudfront_cache_policy.next_private_disabled[0].id
      origin_request_policy_id   = aws_cloudfront_origin_request_policy.next_app[0].id
      response_headers_policy_id = aws_cloudfront_response_headers_policy.next_private[0].id
    }
  }

  ordered_cache_behavior {
    path_pattern             = "/api/*"
    target_origin_id         = "fastapi-alb"
    viewer_protocol_policy   = "redirect-to-https"
    allowed_methods          = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods           = ["GET", "HEAD"]
    compress                 = false
    cache_policy_id          = aws_cloudfront_cache_policy.api_disabled.id
    origin_request_policy_id = aws_cloudfront_origin_request_policy.api.id
  }


  dynamic "ordered_cache_behavior" {
    for_each = local.is_beta ? [1] : []

    content {
      path_pattern             = "/v1/*"
      target_origin_id         = "fastapi-alb"
      viewer_protocol_policy   = "https-only"
      allowed_methods          = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
      cached_methods           = ["GET", "HEAD"]
      compress                 = true
      cache_policy_id          = aws_cloudfront_cache_policy.api_disabled.id
      origin_request_policy_id = aws_cloudfront_origin_request_policy.api.id

      function_association {
        event_type   = "viewer-request"
        function_arn = aws_cloudfront_function.integration_api_rewrite[0].arn
      }
    }
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }

  viewer_certificate {
    cloudfront_default_certificate = !local.is_beta
    acm_certificate_arn            = local.is_beta ? var.cloudfront_certificate_arn : null
    ssl_support_method             = local.is_beta ? "sni-only" : null
    minimum_protocol_version       = local.is_beta ? "TLSv1.2_2021" : "TLSv1"
  }
}

data "aws_iam_policy_document" "frontend_bucket" {
  statement {
    sid       = "AllowCloudFrontOACRead"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.frontend.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.app.arn]
    }
  }

  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.frontend.arn, "${aws_s3_bucket.frontend.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  policy = data.aws_iam_policy_document.frontend_bucket.json

  depends_on = [aws_s3_bucket_public_access_block.frontend]
}
