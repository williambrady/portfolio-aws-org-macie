# AWS Macie Organization Configuration Module
# Configures Macie in the audit account (delegated administrator):
# - Enables Macie account
# - Organization-wide auto-enable for new members
# - Classification export to S3 with KMS encryption
# - Weekly classification job (ccoe-weekly) with bucket exclusions
#
# Note: Automated sensitive data discovery is enabled via AWS CLI in
# post-deployment (no Terraform resource type exists yet).
#
# This module must be called from the AUDIT account context.

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.9"
    }
  }
}

# -----------------------------------------------------------------------------
# Enable Macie in the Audit Account
# -----------------------------------------------------------------------------

resource "aws_macie2_account" "audit" {
  finding_publishing_frequency = "FIFTEEN_MINUTES"
}

# Wait for Macie to fully initialize in the audit account before
# configuring organization settings.
resource "time_sleep" "after_audit_enable" {
  create_duration = "10s"

  depends_on = [aws_macie2_account.audit]
}

# -----------------------------------------------------------------------------
# Organization Configuration
# -----------------------------------------------------------------------------
# Auto-enable Macie for all existing and new member accounts.

resource "aws_macie2_organization_configuration" "main" {
  auto_enable = true

  depends_on = [time_sleep.after_audit_enable]
}

# -----------------------------------------------------------------------------
# Classification Export Configuration
# -----------------------------------------------------------------------------
# Export sensitive data discovery results to S3 with KMS encryption.

resource "aws_macie2_classification_export_configuration" "main" {
  count = var.enable_findings_export ? 1 : 0

  s3_destination {
    bucket_name = var.findings_bucket_name
    key_prefix  = "macie-findings/"
    kms_key_arn = var.findings_kms_key_arn
  }

  depends_on = [aws_macie2_account.audit]
}

# -----------------------------------------------------------------------------
# Weekly Classification Job
# -----------------------------------------------------------------------------
# Scheduled job that runs every Saturday to classify sensitive data
# across all S3 buckets in the organization.
#
# Buckets tagged with {prefix}_macie_exclude=true are excluded.
# Infrastructure buckets (tfstate, access-logs, findings, etc.) should
# be tagged by the projects that create them.
#
# The job name includes a hash of the job configuration so that when the
# definition changes, Terraform creates a new job with a different name.
# This avoids name collisions with cancelled jobs (Macie retains cancelled
# job names indefinitely and rejects new jobs with the same name).

locals {
  job_config_hash = substr(md5(jsonencode({
    tag_key   = var.bucket_exclusion_tag_key
    tag_value = var.bucket_exclusion_tag_value
    schedule  = "SATURDAY"
    sampling  = 100
  })), 0, 8)

  classification_job_name = "ccoe-weekly-${local.job_config_hash}"
}

resource "aws_macie2_classification_job" "ccoe_weekly" {
  name     = local.classification_job_name
  job_type = "SCHEDULED"

  s3_job_definition {
    bucket_criteria {
      excludes {
        and {
          tag_criterion {
            comparator = "EQ"
            tag_values {
              key   = var.bucket_exclusion_tag_key
              value = var.bucket_exclusion_tag_value
            }
          }
        }
      }
    }
  }

  schedule_frequency {
    weekly_schedule = "SATURDAY"
  }

  sampling_percentage = 100

  tags = var.common_tags

  depends_on = [aws_macie2_organization_configuration.main]

  lifecycle {
    ignore_changes = [
      initial_run,
    ]
  }
}
