locals {
  frontend_bucket_name = "${local.name}-frontend-${var.resource_suffix}"
  media_bucket_name    = "${local.name}-media-${var.resource_suffix}"
}

resource "aws_s3_bucket" "frontend" {
  bucket = local.frontend_bucket_name
}

resource "aws_s3_bucket_public_access_block" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "frontend" {
  bucket = aws_s3_bucket.frontend.id

  rule {
    id     = "ephemeral-noncurrent-static"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 3
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  depends_on = [aws_s3_bucket_versioning.frontend]
}

resource "aws_s3_bucket" "media" {
  bucket = local.media_bucket_name
}

resource "aws_s3_bucket_public_access_block" "media" {
  bucket = aws_s3_bucket.media.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "media" {
  bucket = aws_s3_bucket.media.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "media" {
  bucket = aws_s3_bucket.media.id

  rule {
    bucket_key_enabled = false

    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "media" {
  bucket = aws_s3_bucket.media.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_cors_configuration" "media" {
  bucket = aws_s3_bucket.media.id

  cors_rule {
    # POST Object carries tags in the signed multipart `tagging` field, not an
    # x-amz-tagging HTTP header. Keep the browser CORS surface exact.
    allowed_headers = ["Content-Type", "x-amz-server-side-encryption", "x-amz-date", "authorization"]
    allowed_methods = ["GET", "HEAD", "POST"]
    allowed_origins = local.is_beta ? [var.app_origin] : ["https://${aws_cloudfront_distribution.app.domain_name}"]
    expose_headers  = ["ETag", "x-amz-version-id", "Accept-Ranges", "Content-Range"]
    max_age_seconds = 300
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "media" {
  bucket = aws_s3_bucket.media.id

  rule {
    id     = "expire-abandoned-uploads"
    status = "Enabled"

    filter { prefix = "uploads/" }

    expiration {
      days = local.is_beta ? var.beta_source_retention_days : var.media_lifecycle_days
    }

    noncurrent_version_expiration {
      noncurrent_days = local.is_beta ? var.beta_source_retention_days : var.media_lifecycle_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  # The beta's broad uploads rule above remains a 30-day fail-safe for
  # abandoned legacy/untagged objects. An investigation upload additionally
  # carries exactly one immutable presigned tier. Matching rules make both
  # current and noncurrent versions eligible at the analyst-selected 1..30
  # day boundary, including versions created by reusing a still-live POST
  # after another version was pinned in PostgreSQL. The exact-version janitor
  # remains authoritative for every version it did pin.
  dynamic "rule" {
    for_each = local.is_beta ? local.investigation_retention_tiers : {}

    content {
      id     = "expire-investigation-source-${rule.key}d"
      status = "Enabled"

      filter {
        and {
          prefix = "uploads/orgs/"
          tags = {
            (local.investigation_retention_tag_key) = rule.key
          }
        }
      }

      expiration {
        days = rule.value
      }

      noncurrent_version_expiration {
        noncurrent_days = rule.value
      }
    }
  }

  rule {
    id     = "expire-generated-evidence-media"
    status = "Enabled"

    filter { prefix = "jobs/" }

    expiration {
      days = local.is_beta ? var.beta_source_retention_days : var.media_lifecycle_days
    }

    noncurrent_version_expiration {
      noncurrent_days = local.is_beta ? var.beta_source_retention_days : var.media_lifecycle_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  rule {
    id     = "expire-beta-deliverables"
    status = local.is_beta ? "Enabled" : "Disabled"

    filter { prefix = "deliverables/" }

    expiration { days = var.beta_deliverable_retention_days }

    noncurrent_version_expiration {
      noncurrent_days = var.beta_deliverable_retention_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  depends_on = [aws_s3_bucket_versioning.media]
}

data "aws_iam_policy_document" "media_bucket" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.media.arn, "${aws_s3_bucket.media.arn}/*"]

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

resource "aws_s3_bucket_policy" "media" {
  bucket = aws_s3_bucket.media.id
  policy = data.aws_iam_policy_document.media_bucket.json

  depends_on = [aws_s3_bucket_public_access_block.media]
}
