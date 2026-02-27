# Variables for S3 Bucket Module

variable "bucket_name" {
  description = "Name of the S3 bucket"
  type        = string
}

variable "kms_key_arn" {
  description = "ARN of the KMS key for bucket encryption (required)"
  type        = string
}

variable "versioning_enabled" {
  description = "Enable versioning on the bucket"
  type        = bool
  default     = true
}

variable "enforce_ssl" {
  description = "Add bucket policy to deny non-SSL requests"
  type        = bool
  default     = true
}

# -----------------------------------------------------------------------------
# Access Logging
# -----------------------------------------------------------------------------

variable "is_access_logging_bucket" {
  description = "Set to true if this bucket is used for access logging (disables logging on itself)"
  type        = bool
  default     = false
}

variable "access_logging_enabled" {
  description = "Enable access logging for this bucket (ignored if is_access_logging_bucket is true)"
  type        = bool
  default     = true
}

variable "access_logging_bucket" {
  description = "Target bucket for access logs (required if access_logging_enabled is true)"
  type        = string
  default     = ""
}

variable "access_logging_prefix" {
  description = "Prefix for access log objects (defaults to bucket_name/)"
  type        = string
  default     = ""
}

# -----------------------------------------------------------------------------
# Bucket Policy
# -----------------------------------------------------------------------------

variable "bucket_policy" {
  description = "Custom bucket policy JSON. If null, generates policy from other variables."
  type        = string
  default     = null
}

variable "additional_policy_statements" {
  description = "Additional IAM policy statements to add to the bucket policy"
  type        = list(any)
  default     = []
}

# -----------------------------------------------------------------------------
# Lifecycle Rules
# -----------------------------------------------------------------------------

variable "lifecycle_rules" {
  description = "List of lifecycle rules for the bucket"
  type = list(object({
    id     = string
    status = string
    prefix = optional(string, "")
    transitions = optional(list(object({
      days          = number
      storage_class = string
    })), [])
    expiration_days = optional(number)
    noncurrent_version_transitions = optional(list(object({
      days          = number
      storage_class = string
    })), [])
    noncurrent_version_expiration_days = optional(number)
  }))
  default = null
}

variable "common_tags" {
  description = "Common tags to apply to all resources"
  type        = map(string)
  default     = {}
}
