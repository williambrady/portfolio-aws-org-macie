# portfolio-aws-org-macie

Deploy Macie organization-wide in the primary AWS region with discovery-driven Terraform deployment.

## Overview

portfolio-aws-org-macie configures AWS Macie in the primary region of an AWS Organization. It designates a delegated administrator (audit account), enables organization-wide auto-enrollment, exports findings to S3 with KMS encryption, enables automated sensitive data discovery, and runs a weekly classification job with tag-based bucket exclusions.

## Features

- **Delegated Admin** - Configures audit account as Macie delegated administrator
- **Organization Auto-Enable** - Automatically enables Macie for all existing and new member accounts
- **Findings Export** - Centralized S3 bucket in audit account with KMS encryption, lifecycle policies (STANDARD_IA at 90d, GLACIER at 365d, expire at 730d)
- **Automated Sensitive Data Discovery** - Enabled via post-deployment script (no Terraform resource exists yet)
- **Weekly Classification Job** (`ccoe-weekly`) - Scheduled Saturday scan across all organization S3 buckets
- **Tag-Based Bucket Exclusions** - Exclude infrastructure buckets via `{prefix}_macie_exclude=true` tag
- **Two-Phase Apply** - Handles AWS auto-enablement of Macie in audit account during delegation

## Prerequisites

- **Docker** - Required for running the deployment
- **AWS CLI** - Configured with a profile for the management account
- **AWS Organization** - Must already exist (managed by `portfolio-aws-org-baseline`)
- **Macie Service Access** - Must be enabled in Organizations (managed by `portfolio-aws-org-baseline`)
- **Audit Account** - Must already exist in the organization

## Quick Start

### 1. Configure

Edit `config.yaml`:

```yaml
resource_prefix: "myorg"  # Must match org-baseline's resource_prefix
deployment_name: "portfolio-aws-org-macie"
```

The `audit_account_id`, `primary_region`, and `tags` are auto-discovered from `portfolio-aws-org-baseline` via an SSM Parameter Store parameter at `/{resource_prefix}/org-baseline/config`. You can override `audit_account_id` and `primary_region` in `config.yaml` if needed. When SSM provides `tags`, those take precedence over any `tags` defined in `config.yaml`.

### 2. Plan

```bash
AWS_PROFILE=management-account make plan
```

### 3. Apply

```bash
AWS_PROFILE=management-account make apply
```

## Usage

### Commands

| Command | Description |
|---------|-------------|
| `make discover` | Discover current AWS state without changes |
| `make plan` | Discovery + Terraform plan (preview changes) |
| `make apply` | Discovery + Terraform apply + post-deployment verification |
| `make destroy` | Destroy all managed resources (use with caution) |
| `make shell` | Open interactive shell in container for debugging |

### Examples

```bash
# Preview changes
AWS_PROFILE=mgmt make plan

# Apply Macie configuration
AWS_PROFILE=mgmt make apply

# Debug: open shell in container
AWS_PROFILE=mgmt make shell
```

## Configuration Reference

```yaml
# REQUIRED: Prefix for all AWS resource names
# Used to locate SSM parameter: /{resource_prefix}/org-baseline/config
resource_prefix: "myorg"

# REQUIRED: Deployment name (used for CloudWatch log group naming)
deployment_name: "portfolio-aws-org-macie"

# Cross-account role name (default: OrganizationAccountAccessRole)
audit_account_role: "OrganizationAccountAccessRole"

# Tag value for bucket exclusions (tag key is {resource_prefix}_macie_exclude)
bucket_exclusion_tag_value: "true"

# Optional overrides (auto-discovered from org-baseline SSM parameter if omitted)
# primary_region: "us-east-1"
# audit_account_id: "123456789012"
```

### SSM Auto-Discovery

When `portfolio-aws-org-baseline` is deployed, it writes organization configuration to SSM Parameter Store at `/{resource_prefix}/org-baseline/config`. Discovery combines the SSM parameter with AWS API calls to resolve:

- `audit_account_id` - Delegated admin account (from SSM)
- `primary_region` - Primary AWS region (from SSM)
- `management_account_id` - Management account (from `sts.get_caller_identity()`)
- `tags` - Shared resource tags (from SSM, takes precedence over config.yaml)

If the SSM parameter is unavailable (e.g., first-time setup), discovery falls back to values in `config.yaml` where possible.

### Bucket Exclusions

The weekly classification job excludes S3 buckets tagged with `{resource_prefix}_macie_exclude=true`. Tag infrastructure buckets (tfstate, access-logs, findings, etc.) in their respective portfolio projects to exclude them from scanning.

## Project Structure

```
portfolio-aws-org-macie/
├── entrypoint.sh           # Main orchestration script
├── config.yaml             # Deployment configuration
├── requirements.txt        # Python dependencies
├── discovery/
│   ├── discover.py         # AWS discovery, generates tfvars
│   ├── state_sync.py       # Terraform state synchronization
│   └── cloudwatch_logger.py # CloudWatch Logs streaming helper
├── post-deployment/
│   └── verify-macie.py     # Deployment verification (7 checks)
├── terraform/
│   ├── main.tf             # Root module (KMS, S3, CloudWatch, module wiring)
│   ├── variables.tf        # Variable definitions
│   ├── outputs.tf          # Output definitions
│   ├── providers.tf        # Provider configurations (2 providers)
│   ├── versions.tf         # Version constraints
│   └── modules/
│       ├── macie-org/      # Enable Macie + delegated admin registration
│       ├── macie-config/   # Organization config, export, classification job
│       ├── kms/            # Reusable KMS key module
│       └── s3/             # Reusable S3 bucket module
├── Dockerfile
└── Makefile
```

## Architecture

### Module Structure

| Module | Account | Purpose |
|--------|---------|---------|
| `macie-org` | Management | Enable Macie + designate delegated admin |
| `macie-config` | Audit (Delegated Admin) | Organization config, findings export, classification job |
| `kms` | Management + Audit | KMS keys for deployment logs and findings encryption |
| `s3` | Audit | Findings export bucket with lifecycle policies |

### Deployment Phases

1. **Discovery** - Inspect existing Macie state, check access logs bucket, generate tfvars
2. **Terraform Init** - Initialize with S3 backend, sync existing resources into state
3. **Terraform Plan/Apply** - Deploy Macie configuration (two-phase on apply)
4. **Verification** - Validate configuration (7 checks including automated discovery enablement)
5. **Summary** - Output deployment results

See [STEPS.md](STEPS.md) for detailed documentation of every deployment step.

### Multi-Account Architecture

```
Management Account
├── Enables Macie
├── Registers delegated admin (audit account)
├── Deployment CloudWatch Logs + KMS
└── Runs Terraform

Audit Account (Delegated Admin)
├── Macie enabled (auto-enabled by delegation)
├── Organization auto-enable configuration
├── Classification export → S3 findings bucket
├── KMS key for findings encryption
├── Automated sensitive data discovery
└── ccoe-weekly classification job (Saturday)

Other Member Accounts
└── Auto-enrolled by organization configuration
```

### Two-Phase Apply

Delegating admin to the audit account auto-enables Macie there (AWS behavior). Since Terraform doesn't know about this auto-created resource, the apply uses two phases:

1. **Phase 3a** - Apply `macie_org` module + infrastructure (KMS, S3, CloudWatch)
2. **Phase 3b** - Re-run `state_sync.py` to import the auto-enabled audit Macie account
3. **Phase 3c** - Full apply (remaining resources: org config, export, classification job)

## State Management

- Terraform state stored in the org-baseline S3 bucket: `{resource_prefix}-tfstate-{ACCOUNT_ID}`
- State key: `macie/terraform.tfstate` (separate prefix from org-baseline's `organization/terraform.tfstate`)
- The state bucket must be created by `portfolio-aws-org-baseline` before running this project

## Relationship to org-baseline

This project depends on `portfolio-aws-org-baseline` for:
- AWS Organization creation
- Macie service access principal (`macie.amazonaws.com`) in the organization
- Shared accounts (audit account) creation
- SSM Parameter Store config at `/{resource_prefix}/org-baseline/config` (auto-discovery of account IDs and tags)

Macie-specific resources (delegated admin, org config, findings export, classification job) are fully managed by this project.

## Security

This project includes automated security scanning via [portfolio-code-scanner](https://github.com/williambrady/portfolio-code-scanner).

## License

See [LICENSE](LICENSE) for details.
