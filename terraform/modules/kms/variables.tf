# Variables for KMS Module

variable "alias_name" {
  description = "Alias name for the KMS key (without 'alias/' prefix)"
  type        = string
}

variable "description" {
  description = "Description of the KMS key"
  type        = string
}

variable "deletion_window_in_days" {
  description = "Duration in days before the key is deleted after destruction"
  type        = number
  default     = 30
}

variable "enable_key_rotation" {
  description = "Enable automatic key rotation"
  type        = bool
  default     = true
}

variable "policy" {
  description = "Custom KMS key policy JSON. If null, a policy is generated from other variables."
  type        = string
  default     = null
}

variable "service_principals" {
  description = "AWS service principals that can use this key (e.g., cloudtrail.amazonaws.com)"
  type        = list(string)
  default     = []
}

variable "service_principal_actions" {
  description = "Actions allowed for service principals"
  type        = list(string)
  default     = ["kms:Decrypt", "kms:GenerateDataKey*", "kms:DescribeKey"]
}

variable "allow_organization_access" {
  description = "Allow all accounts in the organization to use this key"
  type        = bool
  default     = false
}

variable "organization_id" {
  description = "Organization ID for cross-account access (required if allow_organization_access is true)"
  type        = string
  default     = ""
}

variable "additional_account_ids" {
  description = "Additional AWS account IDs that can use this key"
  type        = list(string)
  default     = []
}

variable "additional_policy_statements" {
  description = "Additional IAM policy statements to add to the key policy"
  type        = list(any)
  default     = []
}

variable "common_tags" {
  description = "Common tags to apply to all resources"
  type        = map(string)
  default     = {}
}
