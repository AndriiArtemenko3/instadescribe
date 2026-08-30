resource "aws_kms_key" "web_sessions" {
  count = local.is_beta ? 1 : 0

  description             = "Encrypt opaque InstaDescribe beta web sessions"
  enable_key_rotation     = true
  deletion_window_in_days = 30
}

resource "aws_kms_alias" "web_sessions" {
  count = local.is_beta ? 1 : 0

  name          = "alias/${local.name}-web-sessions"
  target_key_id = aws_kms_key.web_sessions[0].key_id
}

resource "aws_dynamodb_table" "web_sessions" {
  count = local.is_beta ? 1 : 0

  name         = "${local.name}-web-sessions"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "session_id"

  attribute {
    name = "session_id"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.web_sessions[0].arn
  }

  point_in_time_recovery {
    enabled = true
  }
}

resource "aws_cognito_user_pool" "beta" {
  count = local.is_beta ? 1 : 0

  name                     = "${local.name}-users"
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  mfa_configuration        = "OPTIONAL"
  deletion_protection      = "ACTIVE"

  software_token_mfa_configuration {
    enabled = true
  }

  admin_create_user_config {
    allow_admin_create_user_only = true

    invite_message_template {
      email_subject = "Your InstaDescribe beta invitation"
      email_message = "Your InstaDescribe username is {username} and temporary password is {####}."
      sms_message   = "InstaDescribe username: {username}; temporary password: {####}"
    }
  }

  password_policy {
    minimum_length                   = 14
    require_lowercase                = true
    require_numbers                  = true
    require_symbols                  = true
    require_uppercase                = true
    temporary_password_validity_days = 7
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  user_attribute_update_settings {
    attributes_require_verification_before_update = ["email"]
  }

  # A random durable invitation UUID lets the API reconcile the narrow crash
  # window after AdminCreateUser without adopting a pre-existing account.
  schema {
    name                     = "invitation_id"
    attribute_data_type      = "String"
    developer_only_attribute = false
    mutable                  = false
    required                 = false

    string_attribute_constraints {
      min_length = 36
      max_length = 36
    }
  }
}

resource "aws_cognito_user_pool_client" "next_bff" {
  count = local.is_beta ? 1 : 0

  name         = "${local.name}-next-bff"
  user_pool_id = aws_cognito_user_pool.beta[0].id

  generate_secret                      = true
  prevent_user_existence_errors        = "ENABLED"
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = ["${var.app_origin}/auth/callback"]
  logout_urls                          = ["${var.app_origin}/login"]
  # The Next BFF performs the password exchange server-side (never in browser
  # JavaScript), then stores only an opaque encrypted session handle. Keep SRP
  # for a future hosted/browser flow and allow refresh-token rotation.
  explicit_auth_flows = [
    "ALLOW_USER_PASSWORD_AUTH",
    "ALLOW_USER_SRP_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
  ]

  access_token_validity  = 15
  id_token_validity      = 15
  refresh_token_validity = 30

  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }

  enable_token_revocation = true
}

resource "aws_cognito_user_pool_domain" "beta" {
  count = local.is_beta ? 1 : 0

  domain       = "${local.name}-${var.resource_suffix}"
  user_pool_id = aws_cognito_user_pool.beta[0].id
}

# Secret shells only. Values are inserted out-of-band and never committed or
# passed as Terraform variables. API-key digests use the pepper; the webhook
# dispatcher gets decrypt access to individual endpoint secrets later.
resource "aws_secretsmanager_secret" "api_key_pepper" {
  count = local.is_beta ? 1 : 0

  name                    = "${local.name}/api-key-pepper"
  description             = "Server-side HMAC pepper for service API-key digests"
  recovery_window_in_days = 30
}

# Shared only by the browser BFF and App API. Its raw value is populated
# out-of-band as canonical base64url for exactly 32 random bytes. Keeping this
# separate prevents the API from reading Cognito client or web-session secrets.
resource "aws_secretsmanager_secret" "browser_assertion" {
  count = local.is_beta ? 1 : 0

  name                    = "${local.name}/browser-assertion"
  description             = "Out-of-band BFF-to-App-API browser assertion HMAC secret; Terraform manages no value"
  recovery_window_in_days = 30
}
