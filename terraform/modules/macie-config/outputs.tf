# Outputs for Macie Organization Configuration Module

output "macie_account_id" {
  description = "The Macie account ID in the audit account"
  value       = aws_macie2_account.audit.id
}

output "auto_enable" {
  description = "Whether Macie auto-enable is configured for the organization"
  value       = aws_macie2_organization_configuration.main.auto_enable
}

output "classification_job_id" {
  description = "The ID of the ccoe-weekly classification job"
  value       = aws_macie2_classification_job.ccoe_weekly.id
}

output "classification_job_name" {
  description = "The name of the classification job"
  value       = aws_macie2_classification_job.ccoe_weekly.name
}
