terraform {
  # Variable validation below deliberately enforces relationships between
  # active and accepted origin-header inputs, supported from Terraform 1.9.
  required_version = ">= 1.9.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}
