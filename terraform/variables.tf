# Variables for Macie Organization Deployment

variable "primary_region" {
  description = "Primary AWS region for Macie deployment"
  type        = string
}

variable "resource_prefix" {
  description = "Prefix for all AWS resource names"
  type        = string
}

variable "deployment_name" {
  description = "Deployment name used for CloudWatch log group naming"
  type        = string
}

variable "audit_account_id" {
  description = "AWS account ID of the audit account (delegated administrator for Macie)"
  type        = string
  default     = ""
}

variable "audit_account_role" {
  description = "IAM role name for cross-account access to the audit account"
  type        = string
  default     = "OrganizationAccountAccessRole"
}

variable "access_logs_bucket_exists" {
  description = "Whether the access logs bucket exists in the audit account (auto-discovered)"
  type        = bool
  default     = false
}

variable "bucket_exclusion_tag_value" {
  description = "Tag value for bucket exclusions from the classification job (key is {resource_prefix}_macie_exclude)"
  type        = string
  default     = "true"
}

variable "custom_tags" {
  description = "Custom tags applied to all resources (auto-discovered from org-baseline SSM parameter)"
  type        = map(string)
  default     = {}
}
