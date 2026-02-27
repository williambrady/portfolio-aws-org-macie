# Macie Organization Deployment
# Manages Macie in the primary region across an AWS Organization

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  accounts_exist   = var.audit_account_id != ""
  audit_account_id = var.audit_account_id

  findings_bucket_name = local.accounts_exist ? module.s3_macie_findings[0].bucket_id : ""
  findings_kms_key_arn = local.accounts_exist ? module.kms_macie_findings[0].key_arn : ""

  # Dynamic tag key for bucket exclusions: {resource_prefix}_macie_exclude
  bucket_exclusion_tag_key = "${var.resource_prefix}_macie_exclude"

  common_tags = merge(
    {
      ManagedBy      = "portfolio-aws-org-macie"
      ResourcePrefix = var.resource_prefix
    },
    var.custom_tags
  )
}

# -----------------------------------------------------------------------------
# Deployment Logging - KMS Key + CloudWatch Log Group
# -----------------------------------------------------------------------------

module "kms_deployment_logs" {
  source = "./modules/kms"

  alias_name  = "${var.resource_prefix}-macie-deployment-logs"
  description = "Encryption key for Macie deployment CloudWatch logs"

  service_principals = [
    "logs.${data.aws_region.current.name}.amazonaws.com"
  ]
  service_principal_actions = [
    "kms:Encrypt*",
    "kms:Decrypt*",
    "kms:ReEncrypt*",
    "kms:GenerateDataKey*",
    "kms:Describe*"
  ]
  additional_policy_statements = [
    {
      Sid    = "AllowCloudWatchLogsEncryptionContext"
      Effect = "Allow"
      Principal = {
        Service = "logs.${data.aws_region.current.name}.amazonaws.com"
      }
      Action = [
        "kms:Encrypt*",
        "kms:Decrypt*",
        "kms:ReEncrypt*",
        "kms:GenerateDataKey*",
        "kms:Describe*"
      ]
      Resource = "*"
      Condition = {
        ArnLike = {
          "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/${var.resource_prefix}/*"
        }
      }
    }
  ]

  common_tags = local.common_tags
}

resource "aws_cloudwatch_log_group" "deployments" {
  name              = "/${var.resource_prefix}/deployments/${var.deployment_name}"
  retention_in_days = 365
  kms_key_id        = module.kms_deployment_logs.key_arn

  tags = local.common_tags
}

# -----------------------------------------------------------------------------
# Macie Findings Export - KMS Key (Audit Account)
# -----------------------------------------------------------------------------

module "kms_macie_findings" {
  source = "./modules/kms"
  count  = local.accounts_exist ? 1 : 0

  alias_name  = "${var.resource_prefix}-macie-findings"
  description = "Encryption key for Macie findings export bucket"

  service_principals = [
    "macie.amazonaws.com"
  ]
  service_principal_actions = [
    "kms:GenerateDataKey",
    "kms:Encrypt",
    "kms:Decrypt",
    "kms:ReEncrypt*",
    "kms:DescribeKey"
  ]

  common_tags = local.common_tags

  providers = {
    aws = aws.audit
  }
}

# -----------------------------------------------------------------------------
# Macie Findings Export - S3 Bucket (Audit Account)
# -----------------------------------------------------------------------------

module "s3_macie_findings" {
  source = "./modules/s3"
  count  = local.accounts_exist ? 1 : 0

  bucket_name = "${var.resource_prefix}-macie-findings-${var.audit_account_id}"
  kms_key_arn = module.kms_macie_findings[0].key_arn

  access_logging_enabled = var.access_logs_bucket_exists
  access_logging_bucket  = "${var.resource_prefix}-s3-access-logs-${var.audit_account_id}"

  bucket_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyNonSSL"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          "arn:aws:s3:::${var.resource_prefix}-macie-findings-${var.audit_account_id}",
          "arn:aws:s3:::${var.resource_prefix}-macie-findings-${var.audit_account_id}/*"
        ]
        Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      },
      {
        Sid    = "AllowMaciePutObject"
        Effect = "Allow"
        Principal = {
          Service = "macie.amazonaws.com"
        }
        Action   = "s3:PutObject"
        Resource = "arn:aws:s3:::${var.resource_prefix}-macie-findings-${var.audit_account_id}/*"
        Condition = {
          StringEquals = {
            "s3:x-amz-acl" = "bucket-owner-full-control"
          }
        }
      },
      {
        Sid    = "AllowMacieGetBucketLocation"
        Effect = "Allow"
        Principal = {
          Service = "macie.amazonaws.com"
        }
        Action   = "s3:GetBucketLocation"
        Resource = "arn:aws:s3:::${var.resource_prefix}-macie-findings-${var.audit_account_id}"
      }
    ]
  })

  lifecycle_rules = [
    {
      id     = "macie-findings-lifecycle"
      status = "Enabled"
      transitions = [
        {
          days          = 90
          storage_class = "STANDARD_IA"
        },
        {
          days          = 365
          storage_class = "GLACIER"
        }
      ]
      expiration_days = 730
    }
  ]

  common_tags = local.common_tags

  providers = {
    aws = aws.audit
  }
}

# -----------------------------------------------------------------------------
# Macie Organization - Enable + Delegate Admin (Management Account)
# -----------------------------------------------------------------------------

module "macie_org" {
  source = "./modules/macie-org"
  count  = local.accounts_exist ? 1 : 0

  audit_account_id = local.audit_account_id
}

# -----------------------------------------------------------------------------
# Wait for Delegated Admin Propagation
# -----------------------------------------------------------------------------
# After the management account delegates admin to the audit account, the
# delegation must propagate before the audit account can configure Macie
# as the delegated administrator. This sleep prevents race conditions.

resource "time_sleep" "after_delegation" {
  count = local.accounts_exist ? 1 : 0

  create_duration = "30s"

  depends_on = [module.macie_org]
}

# -----------------------------------------------------------------------------
# Macie Configuration (Audit Account - Delegated Admin)
# -----------------------------------------------------------------------------

module "macie_config" {
  source = "./modules/macie-config"
  count  = local.accounts_exist ? 1 : 0

  enable_findings_export = true
  findings_bucket_name   = local.findings_bucket_name
  findings_kms_key_arn   = local.findings_kms_key_arn

  bucket_exclusion_tag_key   = local.bucket_exclusion_tag_key
  bucket_exclusion_tag_value = var.bucket_exclusion_tag_value

  common_tags = local.common_tags

  depends_on = [
    time_sleep.after_delegation,
    module.s3_macie_findings,
  ]

  providers = {
    aws = aws.audit
  }
}
