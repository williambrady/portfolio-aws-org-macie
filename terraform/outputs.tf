# Outputs for Macie Organization Deployment

output "delegated_admin_account_id" {
  description = "The audit account ID configured as Macie delegated administrator"
  value       = var.audit_account_id
}

output "deployment_log_group" {
  description = "CloudWatch log group for deployment logs"
  value       = aws_cloudwatch_log_group.deployments.name
}

output "findings_bucket_name" {
  description = "S3 bucket name for Macie findings export"
  value       = local.accounts_exist ? module.s3_macie_findings[0].bucket_id : ""
}

output "findings_kms_key_arn" {
  description = "KMS key ARN used for Macie findings encryption"
  value       = local.accounts_exist ? module.kms_macie_findings[0].key_arn : ""
}

output "classification_job_id" {
  description = "The ID of the ccoe-weekly classification job"
  value       = local.accounts_exist ? module.macie_config[0].classification_job_id : ""
}

output "macie_summary" {
  description = "Summary of Macie organization configuration"
  value = {
    delegated_admin      = var.audit_account_id
    management_account   = data.aws_caller_identity.current.account_id
    primary_region       = data.aws_region.current.name
    findings_bucket      = local.accounts_exist ? module.s3_macie_findings[0].bucket_id : ""
    classification_job   = local.accounts_exist ? module.macie_config[0].classification_job_name : ""
    bucket_exclusion_tag = "${local.bucket_exclusion_tag_key}=${var.bucket_exclusion_tag_value}"
  }
}
