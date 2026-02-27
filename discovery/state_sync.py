#!/usr/bin/env python3
"""
Macie Terraform State Sync Script.

Ensures existing Macie resources are properly imported into Terraform state
before plan/apply runs. Much simpler than GuardDuty - single region, ~5 resources.

Usage:
    python3 state_sync.py              # Import missing resources into state
    python3 state_sync.py --dry-run    # Show what would be imported without modifying state
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

# Number of retries for terraform import commands
IMPORT_RETRIES = 2
IMPORT_RETRY_DELAY = 5


def run_terraform_cmd(args: list, timeout: int = 120) -> tuple:
    """Run a terraform command and return (success, output)."""
    cmd = ["terraform"] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd="/work/terraform",
            timeout=timeout,
        )
        output = result.stdout + result.stderr
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, f"Command timed out after {timeout}s: {' '.join(cmd)}"
    except Exception as e:
        return False, str(e)


def get_state_resources() -> set:
    """Get set of all resource addresses in current Terraform state."""
    success, output = run_terraform_cmd(["state", "list"])
    if success:
        return set(output.strip().split("\n")) if output.strip() else set()
    return set()


def resource_exists_in_state(address: str, state_resources: set) -> bool:
    """Check if a resource exists in the Terraform state."""
    return address in state_resources


def import_resource(address: str, resource_id: str, dry_run: bool = False) -> bool:
    """Import a resource into Terraform state with retries.

    Returns True if the resource was successfully imported or already exists.
    In dry_run mode, prints what would be imported and returns True.
    """
    if dry_run:
        print(f"    DRY RUN: Would import {address} with ID {resource_id}")
        return True

    for attempt in range(1, IMPORT_RETRIES + 1):
        success, output = run_terraform_cmd(["import", "-input=false", address, resource_id])
        if success:
            return True
        if "Resource already managed" in output:
            return True
        if attempt < IMPORT_RETRIES:
            print(f"    Retry {attempt}/{IMPORT_RETRIES - 1} after {IMPORT_RETRY_DELAY}s...")
            time.sleep(IMPORT_RETRY_DELAY)
    return False


def get_account_ids_from_tfvars() -> dict:
    """Get account IDs from bootstrap.auto.tfvars.json."""
    tfvars_path = Path("/work/terraform/bootstrap.auto.tfvars.json")
    result = {"management": "", "audit": ""}

    if tfvars_path.exists():
        try:
            with open(tfvars_path) as f:
                tfvars = json.load(f)
            result["management"] = tfvars.get("management_account_id", "")
            result["audit"] = tfvars.get("audit_account_id", "")
        except Exception:
            pass

    return result


def get_cross_account_session(account_id: str, region: str):
    """Get boto3 session for cross-account access via OrganizationAccountAccessRole."""
    sts = boto3.client("sts")
    try:
        response = sts.assume_role(
            RoleArn=f"arn:aws:iam::{account_id}:role/OrganizationAccountAccessRole",
            RoleSessionName="state-sync",
        )
        credentials = response["Credentials"]
        return boto3.Session(
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"],
            region_name=region,
        )
    except ClientError as e:
        print(f"    Failed to assume role into {account_id}: {e}")
        return None


def warm_up_providers():
    """Run terraform refresh to initialize all provider credentials."""
    print("\n=== Warming Up Terraform Providers ===\n")
    success, output = run_terraform_cmd(
        ["plan", "-refresh-only", "-input=false", "-compact-warnings"],
        timeout=300,
    )
    if success:
        print("  Provider initialization successful")
    else:
        error_lines = [line for line in output.split("\n") if "error" in line.lower() or "Error" in line]
        if error_lines:
            print("  Provider initialization warnings:")
            for line in error_lines[:5]:
                print(f"    {line.strip()}")
        else:
            print("  Provider initialization completed with warnings")


def sync_cloudwatch_log_group(state_resources: set, dry_run: bool = False):
    """Sync CloudWatch log group into Terraform state.

    The log group is pre-created by entrypoint.sh (via aws logs create-log-group)
    before Terraform runs to allow immediate logging. Terraform is the source of
    truth for retention, KMS encryption, and tags.
    """
    print("\n=== Syncing CloudWatch Log Group ===\n")

    tf_address = "aws_cloudwatch_log_group.deployments"

    if resource_exists_in_state(tf_address, state_resources):
        print("  Already in state, skipping")
        return

    tfvars_path = Path("/work/terraform/bootstrap.auto.tfvars.json")
    if not tfvars_path.exists():
        print("  No tfvars found, skipping")
        return

    with open(tfvars_path) as f:
        tfvars = json.load(f)

    resource_prefix = tfvars.get("resource_prefix", "")
    deployment_name = tfvars.get("deployment_name", "")
    if not resource_prefix or not deployment_name:
        print("  Missing resource_prefix or deployment_name, skipping")
        return

    log_group_name = f"/{resource_prefix}/deployments/{deployment_name}"

    print(f"  Importing {tf_address} ({log_group_name})...")
    if import_resource(tf_address, log_group_name, dry_run=dry_run):
        if not dry_run:
            print("    Imported successfully")
    else:
        print("    Import failed (will be retried on next run)")


def sync_macie_management_account(state_resources: set, dry_run: bool = False):
    """Sync Macie management account enablement into Terraform state."""
    print("\n=== Syncing Macie Management Account ===\n")

    tf_address = "module.macie_org[0].aws_macie2_account.management"

    if resource_exists_in_state(tf_address, state_resources):
        print("  Already in state, skipping")
        return

    # Check if Macie is enabled in the management account
    try:
        macie_client = boto3.client("macie2")
        session = macie_client.get_macie_session()
        if session.get("status") == "ENABLED":
            print(f"  Importing {tf_address}...")
            if import_resource(tf_address, "macie", dry_run=dry_run):
                if not dry_run:
                    print("    Imported successfully")
            else:
                print("    Import failed (will be retried on next run)")
        else:
            print("  Macie not enabled in management account, skipping")
    except ClientError as e:
        if "Macie is not enabled" in str(e):
            print("  Macie not enabled in management account, skipping")
        else:
            print(f"  Error checking Macie status: {e}")


def sync_macie_org_admin(state_resources: set, dry_run: bool = False):
    """Sync Macie delegated administrator into Terraform state."""
    print("\n=== Syncing Macie Delegated Admin ===\n")

    account_ids = get_account_ids_from_tfvars()

    if not account_ids["audit"]:
        print("  No audit account ID found, skipping")
        return

    audit_account_id = account_ids["audit"]
    tf_address = "module.macie_org[0].aws_macie2_organization_admin_account.main"

    if resource_exists_in_state(tf_address, state_resources):
        print("  Already in state, skipping")
        return

    try:
        org_client = boto3.client("organizations")
        response = org_client.list_delegated_administrators(ServicePrincipal="macie.amazonaws.com")
        admins = response.get("DelegatedAdministrators", [])
        is_delegated_admin = any(a["Id"] == audit_account_id for a in admins)

        if is_delegated_admin:
            print(f"  Importing {tf_address}...")
            if import_resource(tf_address, audit_account_id, dry_run=dry_run):
                if not dry_run:
                    print("    Imported successfully")
            else:
                print("    Import failed")
        else:
            print("  Delegated admin not configured, skipping")
    except ClientError as e:
        print(f"  Error checking delegated admin: {e}")


def sync_macie_audit_account(state_resources: set, primary_region: str, dry_run: bool = False):
    """Sync Macie audit account enablement into Terraform state."""
    print("\n=== Syncing Macie Audit Account ===\n")

    account_ids = get_account_ids_from_tfvars()

    if not account_ids["audit"]:
        print("  No audit account ID found, skipping")
        return

    tf_address = "module.macie_config[0].aws_macie2_account.audit"

    if resource_exists_in_state(tf_address, state_resources):
        print("  Already in state, skipping")
        return

    session = get_cross_account_session(account_ids["audit"], primary_region)
    if not session:
        print("  Could not assume role into audit account, skipping")
        return

    try:
        macie_client = session.client("macie2")
        macie_session = macie_client.get_macie_session()
        if macie_session.get("status") == "ENABLED":
            print(f"  Importing {tf_address}...")
            if import_resource(tf_address, "macie", dry_run=dry_run):
                if not dry_run:
                    print("    Imported successfully")
            else:
                print("    Import failed (will be retried on next run)")
        else:
            print("  Macie not enabled in audit account, skipping")
    except ClientError as e:
        if "Macie is not enabled" in str(e):
            print("  Macie not enabled in audit account, skipping")
        else:
            print(f"  Error checking Macie status: {e}")


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Macie Terraform State Sync")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be imported without modifying Terraform state",
    )
    return parser.parse_args()


def main():
    """Main state sync function."""
    args = parse_args()
    dry_run = args.dry_run

    print("=" * 50)
    if dry_run:
        print("  Macie State Sync (DRY RUN)")
    else:
        print("  Macie State Sync")
    print("=" * 50)
    print("")

    # Load config
    config_path = Path("/work/config.yaml")
    if not config_path.exists():
        config_path = Path(__file__).parent.parent / "config.yaml"

    with open(config_path) as f:
        config = json.load(f) if str(config_path).endswith(".json") else __import__("yaml").safe_load(f)

    primary_region = config.get("primary_region", "us-east-1")

    # Get current Terraform state
    state_resources = get_state_resources()
    print(f"  Current state has {len(state_resources)} resources")

    # Warm up providers on empty state (skip in dry-run since no imports will happen)
    if len(state_resources) == 0 and not dry_run:
        warm_up_providers()

    # Sync CloudWatch log group (pre-created by entrypoint.sh before Terraform)
    sync_cloudwatch_log_group(state_resources, dry_run=dry_run)

    # Sync Macie management account enablement
    sync_macie_management_account(state_resources, dry_run=dry_run)

    # Sync Macie delegated admin
    sync_macie_org_admin(state_resources, dry_run=dry_run)

    # Sync Macie audit account enablement
    sync_macie_audit_account(state_resources, primary_region, dry_run=dry_run)

    print("\n" + "=" * 50)
    if dry_run:
        print("  State Sync Complete (DRY RUN - no state changes)")
    else:
        print("  State Sync Complete")
    print("=" * 50 + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
