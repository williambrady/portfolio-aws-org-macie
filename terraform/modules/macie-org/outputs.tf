# Outputs for Macie Organization Module

output "delegated_admin_account_id" {
  description = "Account ID of the Macie delegated administrator"
  value       = aws_macie2_organization_admin_account.main.admin_account_id
}

output "management_macie_account_id" {
  description = "The Macie account ID for the management account"
  value       = aws_macie2_account.management.id
}
