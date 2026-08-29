variable "aws_region" {
  description = "Fixed v0.1 portfolio region."
  type        = string
  default     = "eu-west-2"

  validation {
    condition     = var.aws_region == "eu-west-2"
    error_message = "The accepted v0.1 plan fixes the portfolio environment in eu-west-2."
  }
}

variable "expected_aws_account_id" {
  description = "Owner-approved AWS account ID used by the provider's fail-closed account allowlist."
  type        = string
  sensitive   = true

  validation {
    condition     = can(regex("^[0-9]{12}$", var.expected_aws_account_id))
    error_message = "expected_aws_account_id must be exactly 12 decimal digits."
  }
}

variable "availability_zones" {
  description = "Two eu-west-2 AZs used for public ECS/ALB and isolated RDS subnets."
  type        = list(string)
  default     = ["eu-west-2a", "eu-west-2b"]

  validation {
    condition = (
      length(var.availability_zones) == 2 &&
      length(distinct(var.availability_zones)) == 2 &&
      alltrue([for az in var.availability_zones : startswith(az, "eu-west-2")])
    )
    error_message = "Provide exactly two distinct eu-west-2 availability zones."
  }
}

variable "project_name" {
  description = "Short resource-name prefix. The historical instascribe default is retained for portfolio state and maps to instadescribe only in a new beta state."
  type        = string
  default     = "instascribe"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,17}$", var.project_name))
    error_message = "project_name must be 3-18 lowercase letters, digits or hyphens so generated AWS names remain valid."
  }
}

variable "resource_suffix" {
  description = "Owner-selected stable lowercase suffix used to make S3 bucket names globally unique."
  type        = string

  validation {
    condition = (
      can(regex("^[a-z0-9]{6,12}$", var.resource_suffix)) &&
      !can(regex("(?i)(example|replace|change)", var.resource_suffix))
    )
    error_message = "Use a stable 6-12 character lowercase alphanumeric suffix, not a placeholder."
  }
}

variable "environment" {
  description = "Deployment tier. Portfolio preserves the released v0.1 posture; beta enables the isolated B2B safety profile."
  type        = string
  default     = "portfolio"

  validation {
    condition     = contains(["portfolio", "beta"], var.environment)
    error_message = "environment must be either portfolio or beta. Use separate Terraform state and a distinct resource_suffix for beta."
  }
}

variable "app_origin" {
  description = "Canonical browser origin used by the beta Cognito callback and direct-media CORS policy."
  type        = string
  default     = "https://app.instadescribe.com"

  validation {
    condition     = can(regex("^https://[A-Za-z0-9.-]+(?::[0-9]+)?$", var.app_origin))
    error_message = "app_origin must be one HTTPS origin with no path, query, fragment or trailing slash."
  }
}

variable "api_origin" {
  description = "Canonical public integration API origin."
  type        = string
  default     = "https://api.instadescribe.com"

  validation {
    condition     = can(regex("^https://[A-Za-z0-9.-]+(?::[0-9]+)?$", var.api_origin))
    error_message = "api_origin must be one HTTPS origin with no path, query, fragment or trailing slash."
  }
}

variable "alb_certificate_arn" {
  description = "ACM certificate in the workload region covering app_origin and api_origin for beta CloudFront-to-ALB TLS; null for portfolio."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = (
      var.environment == "portfolio" ||
      (var.alb_certificate_arn != null && can(regex("^arn:aws:acm:eu-west-2:[0-9]{12}:certificate/[A-Za-z0-9-]+$", var.alb_certificate_arn)))
    )
    error_message = "beta requires a valid eu-west-2 ACM certificate ARN for the ALB."
  }
}

variable "cloudfront_certificate_arn" {
  description = "ACM certificate in us-east-1 covering app_origin and api_origin; null for portfolio."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = (
      var.environment == "portfolio" ||
      (var.cloudfront_certificate_arn != null && can(regex("^arn:aws:acm:us-east-1:[0-9]{12}:certificate/[A-Za-z0-9-]+$", var.cloudfront_certificate_arn)))
    )
    error_message = "beta requires a valid us-east-1 ACM certificate ARN for CloudFront aliases."
  }
}

variable "vpc_cidr" {
  description = "Portfolio VPC CIDR."
  type        = string
  default     = "10.42.0.0/16"
}

variable "public_subnet_cidrs" {
  description = "Two public subnet CIDRs for the ALB and public-IP ECS tasks."
  type        = list(string)
  default     = ["10.42.0.0/24", "10.42.1.0/24"]

  validation {
    condition     = length(var.public_subnet_cidrs) == 2
    error_message = "Exactly two public subnet CIDRs are required."
  }
}

variable "database_subnet_cidrs" {
  description = "Two isolated subnet CIDRs for the single-AZ RDS subnet group."
  type        = list(string)
  default     = ["10.42.10.0/24", "10.42.11.0/24"]

  validation {
    condition     = length(var.database_subnet_cidrs) == 2
    error_message = "Exactly two isolated database subnet CIDRs are required."
  }
}

variable "release_commit_sha" {
  description = "One full lowercase Git commit SHA used for both image tags and application provenance."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{40}$", var.release_commit_sha))
    error_message = "release_commit_sha must be exactly one full 40-character lowercase hexadecimal Git SHA."
  }
}

variable "portfolio_token_sha256" {
  description = "SHA-256 hex digest of the owner-supplied portfolio token; never the plaintext token."
  type        = string
  sensitive   = true

  validation {
    condition     = can(regex("^[0-9a-fA-F]{64}$", var.portfolio_token_sha256))
    error_message = "portfolio_token_sha256 must be exactly 64 hexadecimal characters."
  }
}

variable "origin_verify_active_value" {
  description = "Active CloudFront-to-ALB origin-verification value: exactly 64 lowercase hex characters."
  type        = string
  sensitive   = true

  validation {
    condition     = can(regex("^[0-9a-f]{64}$", var.origin_verify_active_value))
    error_message = "origin_verify_active_value must be exactly 64 lowercase hexadecimal characters; whitespace, control characters and ALB wildcards are rejected."
  }
}

variable "origin_verify_accepted_values" {
  description = "One or two distinct ALB-accepted origin values; must contain the active CloudFront value during rotation."
  type        = list(string)
  sensitive   = true

  validation {
    condition = (
      contains([1, 2], length(var.origin_verify_accepted_values)) &&
      length(distinct(var.origin_verify_accepted_values)) == length(var.origin_verify_accepted_values) &&
      alltrue([for value in var.origin_verify_accepted_values : can(regex("^[0-9a-f]{64}$", value))]) &&
      contains(var.origin_verify_accepted_values, var.origin_verify_active_value)
    )
    error_message = "origin_verify_accepted_values must contain one or two distinct 64-character lowercase hex values and include origin_verify_active_value."
  }
}

variable "budget_alert_email" {
  description = "Required owner-approved recipient for the USD 25 AWS Budget alert."
  type        = string

  validation {
    condition = (
      can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", var.budget_alert_email)) &&
      !can(regex("(?i)(example\\.|invalid|replace|placeholder)", var.budget_alert_email))
    )
    error_message = "Provide the real approved alert recipient; placeholders are rejected."
  }
}

variable "budget_limit_usd" {
  description = "Owner-selected planning budget threshold in USD."
  type        = number
  default     = 25

  validation {
    condition     = var.budget_limit_usd == 25
    error_message = "The selected G9 planning threshold is USD 25."
  }
}

variable "api_desired_count" {
  description = "API is off for bootstrap/migration; set to one only in the separately reviewed enablement apply."
  type        = number
  default     = 0

  validation {
    condition     = contains([0, 1], var.api_desired_count)
    error_message = "api_desired_count must be 0 or 1; bootstrap defaults to zero and v0.1 never exceeds one API task."
  }
}

variable "enable_next_app_runtime" {
  description = "Provision the beta Next standalone ECR/ECS runtime while retaining the Vite S3 origin for staged warm-up and rollback."
  type        = bool
  default     = false

  validation {
    condition     = !var.enable_next_app_runtime || var.environment == "beta"
    error_message = "The Next runtime is beta-only; portfolio must retain the static Vite S3 deployment."
  }
}

variable "next_app_desired_count" {
  description = "Staged Next service count: create ECR/runtime at zero, push the immutable image, then warm exactly one task before origin cutover."
  type        = number
  default     = 0

  validation {
    condition = (
      contains([0, 1], var.next_app_desired_count) &&
      (var.enable_next_app_runtime || var.next_app_desired_count == 0)
    )
    error_message = "next_app_desired_count must be 0 or 1 and may be nonzero only when enable_next_app_runtime is true."
  }
}

variable "app_delivery_origin" {
  description = "Explicit browser-app cutover switch. vite_static preserves the versioned S3 rollback; next requires one warmed beta task."
  type        = string
  default     = "vite_static"

  validation {
    condition = (
      contains(["vite_static", "next"], var.app_delivery_origin) &&
      (
        var.app_delivery_origin == "vite_static" ||
        (
          var.environment == "beta" &&
          var.enable_next_app_runtime &&
          var.next_app_desired_count == 1
        )
      )
    )
    error_message = "app_delivery_origin must be vite_static, or next only in beta with the runtime enabled and exactly one warmed task."
  }
}

variable "worker_desired_count" {
  description = "Initial worker count. Portfolio may use 0/1 for controlled evidence; beta must start at 0 and is managed by queue-depth autoscaling."
  type        = number
  default     = 0

  validation {
    condition = (
      var.environment == "portfolio"
      ? contains([0, 1], var.worker_desired_count)
      : var.worker_desired_count == 0
    )
    error_message = "portfolio worker_desired_count must be 0 or 1; beta must start at 0 because Application Auto Scaling owns desired count."
  }
}

variable "enable_g12_openai" {
  description = "Explicit owner-controlled G12 switch: configure both application services for OpenAI while keeping the API key worker-only."
  type        = bool
  default     = false
}

variable "webhook_allowed_hosts" {
  description = "Exact lowercase DNS names an operator permits for beta webhook delivery; no URLs, ports, wildcards or endpoint secrets."
  type        = list(string)
  default     = []

  validation {
    condition = (
      (var.environment == "beta" || length(var.webhook_allowed_hosts) == 0) &&
      length(distinct(var.webhook_allowed_hosts)) == length(var.webhook_allowed_hosts) &&
      alltrue([
        for host in var.webhook_allowed_hosts :
        length(host) <= 253 &&
        can(regex("^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", host)) &&
        !can(regex("^([0-9]{1,3}\\.){3}[0-9]{1,3}$", host))
      ])
    )
    error_message = "webhook_allowed_hosts is beta-only and must contain distinct exact lowercase DNS names without schemes, ports, paths, IP literals or wildcards."
  }
}

variable "webhook_dispatcher_desired_count" {
  description = "Dedicated beta webhook/outbox and render-backlog publisher count. An enabled beta API requires one continuously running task."
  type        = number
  default     = 0

  validation {
    condition = (
      contains([0, 1], var.webhook_dispatcher_desired_count) &&
      (var.environment != "beta" || var.api_desired_count == 0 || var.webhook_dispatcher_desired_count == 1) &&
      (
        var.webhook_dispatcher_desired_count == 0 ||
        (var.environment == "beta" && length(var.webhook_allowed_hosts) > 0)
      )
    )
    error_message = "webhook_dispatcher_desired_count must be 0 or 1, must be one whenever the beta API is enabled, and may be one only with a non-empty exact-host allowlist."
  }
}

variable "database_name" {
  description = "PostgreSQL database name. The historical default remains unchanged for portfolio state and maps to instadescribe only in a new beta state."
  type        = string
  default     = "instascribe"
}

variable "database_username" {
  description = "RDS master username; the historical default is preserved for portfolio state and maps to instadescribe_admin only in a new beta state."
  type        = string
  default     = "instascribe_admin"
}

variable "media_lifecycle_days" {
  description = "S3 current-version expiration eligibility and subsequent noncurrent-version window; not a deletion guarantee."
  type        = number
  default     = 3

  validation {
    condition     = var.media_lifecycle_days == 3
    error_message = "The selected evidence environment uses a 3-day S3 lifecycle eligibility interval."
  }
}

variable "beta_source_retention_days" {
  description = "B2B beta current/noncurrent source and intermediate-object retention."
  type        = number
  default     = 30

  validation {
    condition     = var.beta_source_retention_days == 30
    error_message = "The accepted beta contract fixes source/intermediate retention at 30 days."
  }
}

variable "beta_deliverable_retention_days" {
  description = "B2B beta current/noncurrent deliverable retention."
  type        = number
  default     = 90

  validation {
    condition     = var.beta_deliverable_retention_days == 90
    error_message = "The accepted beta contract fixes deliverable retention at 90 days."
  }
}

variable "log_retention_days" {
  description = "CloudWatch log retention for the ephemeral evidence environment."
  type        = number
  default     = 14
}

variable "tags" {
  description = "Additional non-secret tags."
  type        = map(string)
  default     = {}
}
