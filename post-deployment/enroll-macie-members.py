#!/usr/bin/env python3
"""
Enroll existing organization member accounts in Macie.

This script runs from the management account and assumes a role into the audit
account (Macie delegated administrator) to associate all existing member
accounts with Macie in the primary region.

New accounts joining the organization are automatically enrolled via
aws_macie2_organization_configuration auto_enable settings. This script
handles accounts that existed before Macie was configured.

Usage:
    # Dry run (show what would be enrolled)
    python3 enroll-macie-members.py --audit-account-id 123456789012

    # Actually enroll accounts
    python3 enroll-macie-members.py --audit-account-id 123456789012 --apply
"""

from __future__ import annotations

import argparse
import sys

import boto3
from botocore.exceptions import ClientError


def assume_audit_role(
    audit_account_id: str,
    region: str,
    role_name: str = "OrganizationAccountAccessRole",
) -> boto3.Session:
    """Assume role into the audit account and return a session."""
    sts = boto3.client("sts", region_name=region)
    role_arn = f"arn:aws:iam::{audit_account_id}:role/{role_name}"

    try:
        response = sts.assume_role(RoleArn=role_arn, RoleSessionName="MacieEnrollment")
        credentials = response["Credentials"]
        return boto3.Session(
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"],
            region_name=region,
        )
    except ClientError as e:
        print(f"Error assuming role {role_arn}: {e}", file=sys.stderr)
        sys.exit(1)


def get_organization_accounts() -> list[dict]:
    """Get all active accounts in the organization."""
    org_client = boto3.client("organizations")
    accounts = []

    paginator = org_client.get_paginator("list_accounts")
    for page in paginator.paginate():
        for account in page["Accounts"]:
            if account["Status"] == "ACTIVE":
                accounts.append(
                    {
                        "id": account["Id"],
                        "name": account["Name"],
                        "email": account["Email"],
                    }
                )

    return accounts


def get_macie_delegated_admin(region: str) -> str | None:
    """Get the Macie delegated admin account ID via Organizations API."""
    org_client = boto3.client("organizations", region_name=region)

    try:
        response = org_client.list_delegated_administrators(ServicePrincipal="macie.amazonaws.com")
        admins = response.get("DelegatedAdministrators", [])
        if admins:
            return admins[0].get("Id")
        return None
    except ClientError as e:
        print(f"Error getting delegated admin: {e}", file=sys.stderr)
        return None


def get_macie_members(session: boto3.Session, region: str) -> dict[str, str]:
    """Get accounts already associated with Macie and their relationship status.

    Returns a dict mapping account_id to relationshipStatus.
    Uses onlyAssociated='false' to include all accounts (not just active members).
    """
    macie_client = session.client("macie2", region_name=region)
    members = {}

    try:
        paginator = macie_client.get_paginator("list_members")
        for page in paginator.paginate(onlyAssociated="false"):
            for member in page.get("members", []):
                members[member["accountId"]] = member.get("relationshipStatus", "Unknown")
    except ClientError as e:
        if "not enabled" in str(e).lower():
            print(f"  ERROR: Macie is not enabled in {region}")
            sys.exit(1)
        print(f"  Warning: Error listing Macie members in {region}: {e}")

    return members


def create_member(session: boto3.Session, account_id: str, email: str, region: str) -> bool:
    """Associate a member account with Macie."""
    macie_client = session.client("macie2", region_name=region)

    try:
        macie_client.create_member(
            account={
                "accountId": account_id,
                "email": email,
            }
        )
        return True
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        if error_code == "ConflictException":
            return True  # Already a member
        print(f"    Failed to enroll {account_id}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Enroll organization accounts in Macie")
    parser.add_argument(
        "--audit-account-id",
        required=True,
        help="Audit account ID (Macie delegated admin)",
    )
    parser.add_argument(
        "--region",
        default="us-east-1",
        help="Primary region for Macie (default: us-east-1)",
    )
    parser.add_argument(
        "--role-name",
        default="OrganizationAccountAccessRole",
        help="Role name to assume in audit account",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually enroll accounts (default is dry-run)",
    )
    args = parser.parse_args()

    dry_run = not args.apply

    print("=" * 60)
    if dry_run:
        print("  Macie Member Enrollment (DRY RUN)")
    else:
        print("  Macie Member Enrollment")
    print("=" * 60)
    print("")

    # Get current account
    sts = boto3.client("sts", region_name=args.region)
    current_account = sts.get_caller_identity()["Account"]
    print(f"Management account: {current_account}")
    print(f"Primary region: {args.region}")

    # Verify the delegated admin matches
    delegated_admin = get_macie_delegated_admin(args.region)
    if delegated_admin != args.audit_account_id:
        print("")
        print("Error: Provided audit account does not match Macie delegated admin.")
        print(f"  Provided: {args.audit_account_id}")
        print(f"  Delegated admin: {delegated_admin}")
        sys.exit(1)
    print(f"Delegated admin: {delegated_admin}")

    # Assume role into audit account
    print(f"Assuming {args.role_name} in audit account...")
    audit_session = assume_audit_role(args.audit_account_id, args.region, args.role_name)
    print("")

    # Get all org accounts, excluding the audit account (delegated admin cannot
    # be enrolled as a member — AWS rejects it with ValidationException)
    print("Fetching organization accounts...")
    all_accounts = get_organization_accounts()
    member_accounts = [a for a in all_accounts if a["id"] != args.audit_account_id]
    print(f"  Total organization accounts: {len(all_accounts)}")
    print(f"  Member accounts (excluding delegated admin): {len(member_accounts)}")
    print("")

    # Get current Macie members
    print("Checking current Macie member status...")
    existing_members = get_macie_members(audit_session, args.region)

    # Categorize accounts
    already_enabled = []
    needs_enrollment = []
    other_status = []

    for account in member_accounts:
        status = existing_members.get(account["id"])
        if status == "Enabled":
            already_enabled.append(account)
        elif status is None:
            needs_enrollment.append(account)
        else:
            other_status.append({**account, "status": status})

    print(f"  Already enrolled: {len(already_enabled)}")
    if other_status:
        print(f"  Other status: {len(other_status)}")
        for account in other_status:
            print(f"    {account['id']} ({account['name']}): {account['status']}")
    print(f"  Needs enrollment: {len(needs_enrollment)}")
    print("")

    if not needs_enrollment:
        print("All member accounts are already enrolled in Macie.")
        return

    # Show accounts to enroll
    print("Accounts to enroll:")
    for account in needs_enrollment:
        print(f"  {account['id']} ({account['name']})")
    print("")

    if dry_run:
        print(f"Would enroll {len(needs_enrollment)} account(s).")
        print("Dry run complete. Use --apply to enroll accounts.")
        return

    # Enroll accounts
    print("Enrolling accounts...")
    enrolled = 0
    failed = 0

    for account in needs_enrollment:
        if create_member(audit_session, account["id"], account["email"], args.region):
            print(f"  {account['id']} ({account['name']}): enrolled")
            enrolled += 1
        else:
            failed += 1

    # Summary
    print("")
    print("=" * 60)
    print("  Enrollment Summary")
    print("=" * 60)
    print("")
    print(f"  Enrolled: {enrolled}/{len(needs_enrollment)}")
    if failed:
        print(f"  Failed: {failed}")
    if not failed:
        print("  All accounts enrolled successfully.")


if __name__ == "__main__":
    sys.exit(main() or 0)
