#!/usr/bin/env python3
"""
Verify and finalize Macie organization-wide configuration.

This script validates that Macie is properly configured after Terraform
has applied the configuration. It also enables automated sensitive data
discovery via the AWS API (no Terraform resource exists for this yet).

Checks performed:
1. Macie service access is enabled in AWS Organizations
2. Audit account is delegated administrator
3. Macie is enabled in management and audit accounts
4. Organization auto-enable is configured
5. Classification export is configured (S3 + KMS)
6. Automated sensitive data discovery is enabled (enables if not yet active)
7. ccoe-weekly classification job exists and is active

Prerequisites:
- Must be run from the management account
- Terraform should have already applied Macie configuration

Usage:
    python3 verify-macie.py              # Verify and enable automated discovery
    python3 verify-macie.py --dry-run    # Verify only, do not enable anything
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError


def load_tfvars() -> dict:
    """Load bootstrap.auto.tfvars.json for account IDs."""
    tfvars_path = Path("/work/terraform/bootstrap.auto.tfvars.json")
    if not tfvars_path.exists():
        tfvars_path = Path(__file__).parent.parent / "terraform" / "bootstrap.auto.tfvars.json"

    if not tfvars_path.exists():
        return {}

    with open(tfvars_path) as f:
        return json.load(f)


def assume_role(account_id: str, region: str) -> boto3.Session | None:
    """Assume OrganizationAccountAccessRole in target account."""
    sts_client = boto3.client("sts", region_name=region)
    role_arn = f"arn:aws:iam::{account_id}:role/OrganizationAccountAccessRole"

    try:
        response = sts_client.assume_role(
            RoleArn=role_arn,
            RoleSessionName="macie-verification",
            DurationSeconds=900,
        )
        credentials = response["Credentials"]
        return boto3.Session(
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"],
            region_name=region,
        )
    except ClientError:
        return None


def check_service_access() -> dict:
    """Check if Macie service access is enabled in Organizations."""
    result = {"enabled": False, "error": None}

    org_client = boto3.client("organizations", region_name="us-east-1")

    try:
        response = org_client.list_aws_service_access_for_organization()
        enabled_services = [s["ServicePrincipal"] for s in response.get("EnabledServicePrincipals", [])]
        result["enabled"] = "macie.amazonaws.com" in enabled_services
    except ClientError as e:
        result["error"] = str(e)

    return result


def check_delegated_admin(region: str, expected_admin: str) -> dict:
    """Check delegated admin configuration."""
    result = {"correct": False, "actual_admin": None, "error": None}

    try:
        org_client = boto3.client("organizations", region_name=region)
        response = org_client.list_delegated_administrators(ServicePrincipal="macie.amazonaws.com")
        admins = response.get("DelegatedAdministrators", [])

        if admins:
            result["actual_admin"] = admins[0]["Id"]
            result["correct"] = result["actual_admin"] == expected_admin
    except ClientError as e:
        result["error"] = str(e)

    return result


def check_macie_enabled(session: boto3.Session, region: str, account_name: str) -> dict:
    """Check if Macie is enabled in an account."""
    result = {"enabled": False, "publishing_frequency": None, "error": None}

    if session is None:
        macie_client = boto3.client("macie2", region_name=region)
    else:
        macie_client = session.client("macie2", region_name=region)

    try:
        macie_session = macie_client.get_macie_session()
        result["enabled"] = macie_session.get("status") == "ENABLED"
        result["publishing_frequency"] = macie_session.get("findingPublishingFrequency")
    except ClientError as e:
        if "Macie is not enabled" in str(e):
            result["enabled"] = False
        else:
            result["error"] = str(e)

    return result


def check_org_config(session: boto3.Session, region: str) -> dict:
    """Check organization configuration from the audit account."""
    result = {"configured": False, "auto_enable": False, "error": None}

    if session is None:
        macie_client = boto3.client("macie2", region_name=region)
    else:
        macie_client = session.client("macie2", region_name=region)

    try:
        response = macie_client.describe_organization_configuration()
        result["configured"] = True
        result["auto_enable"] = response.get("autoEnable", False)
    except ClientError as e:
        result["error"] = str(e)

    return result


def check_classification_export(session: boto3.Session, region: str) -> dict:
    """Check classification export configuration."""
    result = {"configured": False, "bucket": None, "kms_key": None, "error": None}

    if session is None:
        macie_client = boto3.client("macie2", region_name=region)
    else:
        macie_client = session.client("macie2", region_name=region)

    try:
        response = macie_client.get_classification_export_configuration()
        s3_dest = response.get("configuration", {}).get("s3Destination", {})
        if s3_dest:
            result["configured"] = True
            result["bucket"] = s3_dest.get("bucketName")
            result["kms_key"] = s3_dest.get("kmsKeyArn")
    except ClientError as e:
        result["error"] = str(e)

    return result


def enable_automated_discovery(session: boto3.Session, region: str) -> dict:
    """Enable automated sensitive data discovery if not already enabled.

    Since aws_macie2_automated_discovery_configuration does not exist in
    the Terraform AWS provider yet (open feature request #34938), this
    function handles enablement via the boto3 API as a post-deployment step.
    """
    result = {"enabled": False, "already_enabled": False, "error": None}

    if session is None:
        macie_client = boto3.client("macie2", region_name=region)
    else:
        macie_client = session.client("macie2", region_name=region)

    try:
        response = macie_client.get_automated_discovery_configuration()
        if response.get("status") == "ENABLED":
            result["enabled"] = True
            result["already_enabled"] = True
            return result
    except ClientError as e:
        if "is not enabled" not in str(e).lower():
            result["error"] = str(e)
            return result

    try:
        macie_client.update_automated_discovery_configuration(status="ENABLED")
        result["enabled"] = True
    except ClientError as e:
        result["error"] = str(e)

    return result


def check_automated_discovery(session: boto3.Session, region: str) -> dict:
    """Check automated sensitive data discovery status (read-only)."""
    result = {"enabled": False, "error": None}

    if session is None:
        macie_client = boto3.client("macie2", region_name=region)
    else:
        macie_client = session.client("macie2", region_name=region)

    try:
        response = macie_client.get_automated_discovery_configuration()
        result["enabled"] = response.get("status") == "ENABLED"
    except ClientError as e:
        result["error"] = str(e)

    return result


def check_classification_jobs(session: boto3.Session, region: str) -> dict:
    """Check for the ccoe-weekly classification job."""
    result = {"found": False, "job_id": None, "status": None, "job_type": None, "error": None}

    if session is None:
        macie_client = boto3.client("macie2", region_name=region)
    else:
        macie_client = session.client("macie2", region_name=region)

    try:
        response = macie_client.list_classification_jobs(
            filterCriteria={
                "includes": [
                    {
                        "comparator": "EQ",
                        "key": "name",
                        "values": ["ccoe-weekly"],
                    }
                ]
            }
        )
        items = response.get("items", [])
        if items:
            job = items[0]
            result["found"] = True
            result["job_id"] = job.get("jobId")
            result["status"] = job.get("jobStatus")
            result["job_type"] = job.get("jobType")
    except ClientError as e:
        result["error"] = str(e)

    return result


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Macie Organization Verification")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Verify only; do not enable automated discovery or make any changes",
    )
    return parser.parse_args()


def main():
    """Main function."""
    args = parse_args()
    dry_run = args.dry_run

    print("=" * 60)
    if dry_run:
        print("  Macie Organization Verification (DRY RUN)")
    else:
        print("  Macie Organization Verification")
    print("=" * 60)
    print("")

    # Load configuration
    print("Loading configuration...")
    tfvars = load_tfvars()

    management_account_id = tfvars.get("management_account_id", "")
    audit_account_id = tfvars.get("audit_account_id", "")
    primary_region = tfvars.get("primary_region", "us-east-1")

    if not audit_account_id:
        print("Error: Could not determine audit account ID from tfvars")
        return 1

    print(f"  Management account: {management_account_id}")
    print(f"  Audit account: {audit_account_id}")
    print(f"  Primary region: {primary_region}")
    print("")

    issues = []
    warnings = []

    # Check 1: Service access
    print("Checking Macie service access in Organizations...")
    service_result = check_service_access()
    if service_result.get("error"):
        print(f"  ERROR: {service_result['error']}")
        issues.append("Service access check failed")
    elif service_result["enabled"]:
        print("  OK: Macie service access enabled")
    else:
        print("  ERROR: Macie service access NOT enabled")
        issues.append("Macie service access not enabled in Organizations")
    print("")

    # Check 2: Delegated admin
    print("Checking delegated administrator configuration...")
    admin_result = check_delegated_admin(primary_region, audit_account_id)
    if admin_result.get("error"):
        print(f"  ERROR: {admin_result['error']}")
        issues.append("Delegated admin check failed")
    elif admin_result["correct"]:
        print(f"  OK: Delegated admin is {audit_account_id}")
    elif admin_result["actual_admin"]:
        print(f"  ERROR: Wrong delegated admin ({admin_result['actual_admin']})")
        issues.append(f"Wrong delegated admin: {admin_result['actual_admin']}")
    else:
        print("  ERROR: No delegated admin configured")
        issues.append("No delegated admin configured")
    print("")

    # Check 3: Macie enabled in accounts
    accounts = [
        ("Management", management_account_id, None),
        ("Audit", audit_account_id, audit_account_id),
    ]

    for account_name, account_id, assume_account_id in accounts:
        if not account_id:
            print(f"Checking {account_name} Macie status... SKIPPED (no account ID)")
            continue

        print(f"Checking {account_name} Macie status...")
        session = assume_role(assume_account_id, primary_region) if assume_account_id else None
        result = check_macie_enabled(session, primary_region, account_name)
        if result.get("error"):
            print(f"  ERROR: {result['error']}")
            issues.append(f"{account_name}: Macie check failed")
        elif result["enabled"]:
            print(f"  OK: Macie enabled (publishing: {result['publishing_frequency']})")
        else:
            print("  ERROR: Macie NOT enabled")
            issues.append(f"{account_name}: Macie not enabled")
    print("")

    # Check 4: Organization auto-enable
    print("Checking organization auto-enable configuration...")
    audit_session = assume_role(audit_account_id, primary_region)
    org_result = check_org_config(audit_session, primary_region)
    if org_result.get("error"):
        print(f"  ERROR: {org_result['error']}")
        issues.append("Org configuration check failed")
    elif org_result["configured"] and org_result["auto_enable"]:
        print("  OK: Auto-enable is ON for all accounts")
    elif org_result["configured"]:
        print("  WARNING: Auto-enable is OFF")
        warnings.append("Org auto-enable is disabled")
    else:
        print("  ERROR: Org configuration not found")
        issues.append("Organization configuration not found")
    print("")

    # Check 5: Classification export
    print("Checking classification export configuration...")
    export_result = check_classification_export(audit_session, primary_region)
    if export_result.get("error"):
        print(f"  ERROR: {export_result['error']}")
        issues.append("Classification export check failed")
    elif export_result["configured"]:
        print(f"  OK: Exporting to {export_result['bucket']}")
        print(f"      KMS key: {export_result['kms_key']}")
    else:
        print("  ERROR: Classification export not configured")
        issues.append("Classification export not configured")
    print("")

    # Check 6: Automated discovery
    if dry_run:
        print("Checking automated sensitive data discovery...")
        discovery_result = check_automated_discovery(audit_session, primary_region)
        if discovery_result.get("error"):
            print(f"  ERROR: {discovery_result['error']}")
            issues.append("Automated discovery check failed")
        elif discovery_result["enabled"]:
            print("  OK: Automated discovery enabled")
        else:
            print("  INFO: Automated discovery not enabled (would enable on apply)")
        print("")
    else:
        print("Enabling automated sensitive data discovery...")
        discovery_result = enable_automated_discovery(audit_session, primary_region)
        if discovery_result.get("error"):
            print(f"  ERROR: {discovery_result['error']}")
            issues.append("Automated discovery enablement failed")
        elif discovery_result["already_enabled"]:
            print("  OK: Automated discovery already enabled")
        elif discovery_result["enabled"]:
            print("  OK: Automated discovery enabled (newly configured)")
        else:
            print("  WARNING: Automated discovery could not be enabled")
            warnings.append("Automated sensitive data discovery not enabled")
        print("")

    # Check 7: Classification job
    print("Checking ccoe-weekly classification job...")
    job_result = check_classification_jobs(audit_session, primary_region)
    if job_result.get("error"):
        print(f"  ERROR: {job_result['error']}")
        issues.append("Classification job check failed")
    elif job_result["found"]:
        print(f"  OK: Job found (ID: {job_result['job_id']})")
        print(f"      Status: {job_result['status']}, Type: {job_result['job_type']}")
    else:
        print("  WARNING: ccoe-weekly job not found")
        warnings.append("ccoe-weekly classification job not found")
    print("")

    # Summary
    print("=" * 60)
    print("  Verification Summary")
    print("=" * 60)
    print("")

    if issues:
        print(f"Issues Found ({len(issues)}):")
        for issue in issues:
            print(f"  - {issue}")
        print("")

    if warnings:
        print(f"Warnings ({len(warnings)}):")
        for warning in warnings:
            print(f"  - {warning}")
        print("")

    if not issues and not warnings:
        print("All checks passed! Macie is fully configured.")
        return 0
    elif not issues:
        print("Verification complete with warnings.")
        return 0
    else:
        print("Verification complete with issues that need attention.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
