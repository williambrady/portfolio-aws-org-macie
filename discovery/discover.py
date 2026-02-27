#!/usr/bin/env python3
"""
Macie Organization Discovery Script.

Discovers existing Macie organization configuration and generates
Terraform variables for the deployment.

Usage:
    python3 discover.py              # Full discovery, writes tfvars
    python3 discover.py --dry-run    # Read-only discovery, prints what would be written
"""

import argparse
import json
import sys
from pathlib import Path

import boto3
import yaml
from botocore.exceptions import ClientError


def load_config() -> dict:
    """Load configuration from config.yaml."""
    config_path = Path("/work/config.yaml")
    if not config_path.exists():
        config_path = Path(__file__).parent.parent / "config.yaml"

    if not config_path.exists():
        print("Error: config.yaml not found")
        sys.exit(1)

    with open(config_path) as f:
        return yaml.safe_load(f)


def discover_macie_org_config(primary_region: str, audit_account_id: str) -> dict:
    """Discover existing Macie organization configuration.

    Returns information about Macie organization status:
    - Whether Macie is enabled organization-wide
    - The delegated admin account ID
    - Whether auto-enable is configured
    """
    result = {
        "macie_org_exists": False,
        "macie_delegated_admin": "",
        "macie_auto_enable": False,
        "macie_automated_discovery": False,
    }

    try:
        org_client = boto3.client("organizations", region_name=primary_region)
        try:
            response = org_client.list_delegated_administrators(ServicePrincipal="macie.amazonaws.com")
            admins = response.get("DelegatedAdministrators", [])
            if admins:
                result["macie_delegated_admin"] = admins[0]["Id"]
                result["macie_org_exists"] = True
                print(f"    Delegated Admin: {result['macie_delegated_admin']}")

                if result["macie_delegated_admin"] == audit_account_id:
                    try:
                        sts_client = boto3.client("sts", region_name=primary_region)
                        assumed = sts_client.assume_role(
                            RoleArn=f"arn:aws:iam::{audit_account_id}:role/OrganizationAccountAccessRole",
                            RoleSessionName="macie-discovery",
                        )
                        creds = assumed["Credentials"]
                        audit_macie = boto3.client(
                            "macie2",
                            region_name=primary_region,
                            aws_access_key_id=creds["AccessKeyId"],
                            aws_secret_access_key=creds["SecretAccessKey"],
                            aws_session_token=creds["SessionToken"],
                        )

                        macie_status = audit_macie.get_macie_session()
                        if macie_status.get("status") == "ENABLED":
                            print("    Macie enabled in audit account")

                        try:
                            org_config = audit_macie.describe_organization_configuration()
                            result["macie_auto_enable"] = org_config.get("autoEnable", False)
                            if result["macie_auto_enable"]:
                                print("    Auto-enable: ALL")
                            else:
                                print("    Auto-enable: DISABLED")
                        except ClientError:
                            print("    Warning: Could not check org configuration")

                        try:
                            discovery_config = audit_macie.get_automated_discovery_configuration()
                            result["macie_automated_discovery"] = discovery_config.get("status") == "ENABLED"
                            if result["macie_automated_discovery"]:
                                print("    Automated discovery: ENABLED")
                            else:
                                print("    Automated discovery: DISABLED")
                        except ClientError:
                            print("    Automated discovery: Not configured")

                    except ClientError as e:
                        print(f"    Warning: Could not check org config from audit account: {e}")
            else:
                print("    Delegated Admin: None configured")
        except ClientError as e:
            if "AccessDenied" not in str(e):
                print(f"    Warning: Could not list delegated admins: {e}")

    except ClientError as e:
        print(f"    Warning: Could not check Macie status: {e}")

    return result


def read_ssm_org_config(resource_prefix: str, region: str) -> dict:
    """Read org-baseline configuration from SSM Parameter Store.

    Returns the parsed JSON config dict, or empty dict if unavailable.
    """
    ssm_path = f"/{resource_prefix}/org-baseline/config"
    try:
        ssm = boto3.client("ssm", region_name=region)
        response = ssm.get_parameter(Name=ssm_path, WithDecryption=True)
        value = json.loads(response["Parameter"]["Value"])
        print(f"SSM Parameter: {ssm_path}")
        print("    Source: portfolio-aws-org-baseline")
        return value
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "ParameterNotFound":
            print(f"SSM Parameter: {ssm_path} (not found)")
            print("    Falling back to config.yaml values")
        else:
            print(f"SSM Parameter: {ssm_path} (error: {code})")
            print("    Falling back to config.yaml values")
        return {}


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Macie Organization Discovery")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read-only mode: discover state but do not write output files",
    )
    return parser.parse_args()


def main():
    """Main discovery function."""
    args = parse_args()
    dry_run = args.dry_run

    print("=" * 50)
    if dry_run:
        print("  Macie Organization Discovery (DRY RUN)")
    else:
        print("  Macie Organization Discovery")
    print("=" * 50)
    print("")

    # Load configuration
    config = load_config()
    resource_prefix = config.get("resource_prefix", "")

    if not resource_prefix:
        print("Error: resource_prefix is required in config.yaml")
        return 1

    # Get caller identity (needed before SSM call to determine region)
    initial_region = config.get("primary_region", "us-east-1")
    sts = boto3.client("sts", region_name=initial_region)
    identity = sts.get_caller_identity()
    management_account_id = identity["Account"]

    # Read org-baseline config from SSM Parameter Store
    ssm_config = read_ssm_org_config(resource_prefix, initial_region)

    # Merge: SSM values take precedence, config.yaml provides overrides/fallbacks
    primary_region = config.get("primary_region") or ssm_config.get("primary_region", "us-east-1")
    audit_account_id = config.get("audit_account_id") or ssm_config.get("audit_account_id", "")
    custom_tags = ssm_config.get("tags", config.get("tags", {}))

    # Read exclusion config from config.yaml
    # Tag key is dynamic: {resource_prefix}_macie_exclude
    bucket_exclusion_tag_value = config.get("bucket_exclusion_tag_value", "true")
    audit_account_role = config.get("audit_account_role", "OrganizationAccountAccessRole")

    print(f"Management Account: {management_account_id}")
    print(f"Primary Region: {primary_region}")
    print(f"Audit Account: {audit_account_id or '(not configured)'}")
    print("")

    # Discover Macie organization configuration
    discovery = {}

    print("Macie Organization:")
    if not audit_account_id:
        print("    ERROR: audit_account_id not available")
        print("    Ensure org-baseline SSM parameter exists or set audit_account_id in config.yaml")
        print("")
        print("This project requires portfolio-aws-org-baseline to be deployed first.")
        return 1

    macie_info = discover_macie_org_config(primary_region, audit_account_id)
    discovery.update(macie_info)
    print("")

    # Check for access logs bucket in audit account (created by org-baseline)
    access_logs_bucket_exists = False
    access_logs_bucket_name = f"{resource_prefix}-s3-access-logs-{audit_account_id}"
    print("Access Logs Bucket:")
    try:
        sts_client = boto3.client("sts", region_name=primary_region)
        assumed = sts_client.assume_role(
            RoleArn=f"arn:aws:iam::{audit_account_id}:role/{audit_account_role}",
            RoleSessionName="macie-discovery-s3",
        )
        creds = assumed["Credentials"]
        s3_client = boto3.client(
            "s3",
            region_name=primary_region,
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
        )
        s3_client.head_bucket(Bucket=access_logs_bucket_name)
        access_logs_bucket_exists = True
        print(f"    {access_logs_bucket_name} exists")
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        if error_code in ("404", "NoSuchBucket"):
            print(f"    WARNING: {access_logs_bucket_name} not found")
            print("    Findings bucket will be created without access logging")
            print("    Deploy portfolio-aws-org-baseline first to create the access logs bucket")
        else:
            print(f"    WARNING: Could not check bucket: {e}")
            print("    Findings bucket will be created without access logging")
    print("")

    # Build output data
    discovery_path = Path("/work/terraform/discovery.json")
    if not discovery_path.parent.exists():
        discovery_path = Path(__file__).parent.parent / "terraform" / "discovery.json"

    deployment_name = config.get("deployment_name", "portfolio-aws-org-macie")

    tfvars = {
        "primary_region": primary_region,
        "resource_prefix": resource_prefix,
        "deployment_name": deployment_name,
        "management_account_id": management_account_id,
        "audit_account_id": audit_account_id,
        "audit_account_role": audit_account_role,
        "access_logs_bucket_exists": access_logs_bucket_exists,
        "bucket_exclusion_tag_value": bucket_exclusion_tag_value,
        "custom_tags": custom_tags,
    }

    tfvars_path = Path("/work/terraform/bootstrap.auto.tfvars.json")
    if not tfvars_path.parent.exists():
        tfvars_path = Path(__file__).parent.parent / "terraform" / "bootstrap.auto.tfvars.json"

    if dry_run:
        print("DRY RUN: Would write the following files:")
        print(f"\n  {discovery_path}:")
        print(json.dumps(discovery, indent=2, default=str))
        print(f"\n  {tfvars_path}:")
        print(json.dumps(tfvars, indent=2))
    else:
        with open(discovery_path, "w") as f:
            json.dump(discovery, f, indent=2, default=str)
        print(f"Discovery output written to {discovery_path}")

        with open(tfvars_path, "w") as f:
            json.dump(tfvars, f, indent=2)
        print(f"Terraform variables written to {tfvars_path}")

    print("")
    print("=" * 50)
    if dry_run:
        print("  Discovery Complete (DRY RUN - no files written)")
    else:
        print("  Discovery Complete")
    print("=" * 50)
    print("")

    return 0


if __name__ == "__main__":
    sys.exit(main())
