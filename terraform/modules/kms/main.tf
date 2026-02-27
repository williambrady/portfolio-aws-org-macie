# KMS Key Module
# Reusable module for creating KMS keys with configurable policies

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# -----------------------------------------------------------------------------
# Data Sources
# -----------------------------------------------------------------------------

data "aws_caller_identity" "current" {}

# -----------------------------------------------------------------------------
# KMS Key
# -----------------------------------------------------------------------------

resource "aws_kms_key" "main" {
  description             = var.description
  deletion_window_in_days = var.deletion_window_in_days
  enable_key_rotation     = var.enable_key_rotation

  policy = var.policy != null ? var.policy : jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      # Default admin policy - always included
      [
        {
          Sid    = "EnableIAMPolicies"
          Effect = "Allow"
          Principal = {
            AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
          }
          Action   = "kms:*"
          Resource = "*"
        }
      ],
      # Service principal access - only when service principals are specified
      length(var.service_principals) > 0 ? [
        {
          Sid    = "AllowServicePrincipals"
          Effect = "Allow"
          Principal = {
            Service = var.service_principals
          }
          Action   = var.service_principal_actions
          Resource = "*"
        }
      ] : [],
      # Cross-account access via organization
      var.allow_organization_access ? [
        {
          Sid    = "AllowOrganizationAccounts"
          Effect = "Allow"
          Principal = {
            AWS = "*"
          }
          Action = [
            "kms:Decrypt",
            "kms:GenerateDataKey"
          ]
          Resource = "*"
          Condition = {
            StringEquals = {
              "aws:PrincipalOrgID" = var.organization_id
            }
          }
        }
      ] : [],
      # Additional cross-account access - only when additional accounts are specified
      length(var.additional_account_ids) > 0 ? [
        {
          Sid    = "AllowAdditionalAccounts"
          Effect = "Allow"
          Principal = {
            AWS = [for id in var.additional_account_ids : "arn:aws:iam::${id}:root"]
          }
          Action = [
            "kms:Encrypt",
            "kms:Decrypt",
            "kms:ReEncrypt*",
            "kms:GenerateDataKey*",
            "kms:DescribeKey"
          ]
          Resource = "*"
        }
      ] : [],
      # Additional policy statements
      var.additional_policy_statements
    )
  })

  tags = merge(var.common_tags, {
    Name = var.alias_name
  })
}

# -----------------------------------------------------------------------------
# KMS Alias
# -----------------------------------------------------------------------------

resource "aws_kms_alias" "main" {
  name          = "alias/${var.alias_name}"
  target_key_id = aws_kms_key.main.key_id
}
