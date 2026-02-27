# Provider Configurations
# Management account provider (default) + Audit account provider = 2 total
# Macie is single-region (primary region only), unlike GuardDuty which needs 17 regions.

# -----------------------------------------------------------------------------
# Management Account Provider
# -----------------------------------------------------------------------------

provider "aws" {
  region = var.primary_region
}

# -----------------------------------------------------------------------------
# Audit Account Provider
# -----------------------------------------------------------------------------

provider "aws" {
  alias  = "audit"
  region = var.primary_region

  dynamic "assume_role" {
    for_each = var.audit_account_id != "" ? [1] : []
    content {
      role_arn = "arn:aws:iam::${var.audit_account_id}:role/${var.audit_account_role}"
    }
  }
}
