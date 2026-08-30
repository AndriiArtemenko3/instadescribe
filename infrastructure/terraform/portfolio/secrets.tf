resource "aws_secretsmanager_secret" "portfolio_token_hash" {
  name                    = "${local.name}/portfolio-token-sha256"
  description             = "SHA-256 digest only; the plaintext portfolio token is never stored here"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "portfolio_token_hash" {
  secret_id     = aws_secretsmanager_secret.portfolio_token_hash.id
  secret_string = var.portfolio_token_sha256
}

resource "aws_secretsmanager_secret" "origin_verify_active" {
  name                    = "${local.name}/cloudfront-origin-active"
  description             = "Active 64-hex CloudFront-to-ALB origin verification value"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "origin_verify_active" {
  secret_id     = aws_secretsmanager_secret.origin_verify_active.id
  secret_string = var.origin_verify_active_value
}

resource "aws_secretsmanager_secret" "origin_verify_accepted" {
  name                    = "${local.name}/alb-origin-accepted"
  description             = "JSON list of one or two exact 64-hex values accepted by the ALB"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "origin_verify_accepted" {
  secret_id     = aws_secretsmanager_secret.origin_verify_accepted.id
  secret_string = jsonencode(var.origin_verify_accepted_values)
}

# Deliberately no secret version. G12 is a separate owner-authorized action;
# neither task definition reads this secret during the fake-provider gates.
resource "aws_secretsmanager_secret" "openai_api_key" {
  name                    = "${local.name}/openai-api-key"
  description             = "Reserved for the separately authorized G12 real-provider test"
  recovery_window_in_days = 7
}
