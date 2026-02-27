# AWS Macie Organization Module
# Enables Macie in the management account and designates the audit account
# as the Macie delegated administrator.
#
# This module must be called from the MANAGEMENT account context.

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
# Enable Macie in the Management Account
# -----------------------------------------------------------------------------

resource "aws_macie2_account" "management" {
  finding_publishing_frequency = "FIFTEEN_MINUTES"
}

# -----------------------------------------------------------------------------
# Wait for Macie to fully initialize before delegating admin
# -----------------------------------------------------------------------------
# Macie enablement is eventually consistent; the delegated admin API
# can fail if called immediately after enabling the service.

resource "time_sleep" "after_macie_enable" {
  create_duration = "15s"

  depends_on = [aws_macie2_account.management]
}

# -----------------------------------------------------------------------------
# Macie Delegated Administrator
# -----------------------------------------------------------------------------

resource "aws_macie2_organization_admin_account" "main" {
  admin_account_id = var.audit_account_id

  depends_on = [time_sleep.after_macie_enable]

  lifecycle {
    ignore_changes = [admin_account_id]
  }
}
