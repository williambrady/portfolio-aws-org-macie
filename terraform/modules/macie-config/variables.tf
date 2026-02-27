# Variables for Macie Organization Configuration Module

variable "enable_findings_export" {
  description = "Whether to configure Macie findings export to S3"
  type        = bool
  default     = false
}

variable "findings_bucket_name" {
  description = "S3 bucket name for Macie findings export"
  type        = string
  default     = ""
}

variable "findings_kms_key_arn" {
  description = "KMS key ARN for encrypting Macie findings export"
  type        = string
  default     = ""
}

variable "bucket_exclusion_tag_key" {
  description = "Tag key used to exclude S3 buckets from the weekly classification job"
  type        = string
}

variable "bucket_exclusion_tag_value" {
  description = "Tag value used to exclude S3 buckets from the weekly classification job"
  type        = string
  default     = "true"
}

variable "common_tags" {
  description = "Common tags to apply to all resources"
  type        = map(string)
  default     = {}
}
