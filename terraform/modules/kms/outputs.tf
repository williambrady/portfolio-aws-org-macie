# Outputs for KMS Module

output "key_id" {
  description = "The ID of the KMS key"
  value       = aws_kms_key.main.key_id
}

output "key_arn" {
  description = "The ARN of the KMS key"
  value       = aws_kms_key.main.arn
}

output "alias_name" {
  description = "The alias name of the KMS key"
  value       = aws_kms_alias.main.name
}

output "alias_arn" {
  description = "The ARN of the KMS alias"
  value       = aws_kms_alias.main.arn
}
