# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AWS Macie organization-wide deployment using Terraform wrapped in Docker. Deploys Macie in the primary region with delegated admin, findings export, automated sensitive data discovery, and a weekly classification job with tag-based bucket exclusions.

**Stack:** Terraform (infrastructure), Python (discovery/verification), Bash (orchestration), Docker (distribution)

**Relationship:** This project follows the same pattern as `portfolio-aws-org-guardduty`. The AWS Organization and Macie service access principals are managed by `portfolio-aws-org-baseline`.

**Key difference from GuardDuty:** Macie is single-region (primary region only), so we need only 2 providers (management + audit) instead of 51.

## Architecture

### Core Principles

1. **Terraform as State Owner** - Single source of truth for Macie infrastructure state
2. **Config-Driven** - `config.yaml` in project root defines deployment specification
3. **Discovery-Driven** - Python discovers existing resources before Terraform runs
4. **Idempotent Operations** - All deployments safe to retry via two-phase apply
5. **Modular Design** - Reusable Terraform modules for each Macie component

### Data Flow

```
config.yaml + SSM Parameter → discover.py → bootstrap.auto.tfvars.json → state_sync.py → Terraform → verify-macie.py
```

Discovery reads `/{resource_prefix}/org-baseline/config` from SSM Parameter Store (written by `portfolio-aws-org-baseline`) to auto-discover `audit_account_id`, `primary_region`, `tags`, etc. Falls back to `config.yaml` values if SSM is unavailable.

### Account Types

- **Management Account** - AWS Organization root, runs Terraform, enables Macie, registers delegated admin
- **Audit Account** - Delegated admin for Macie, hosts org configuration, findings export bucket, classification jobs

### Macie Architecture

1. **Enable + Delegated Admin** (Management Account) - `macie-org` module
2. **Organization Configuration** (Audit Account) - `macie-config` module
   - Auto-enable for all org members
   - Classification export to S3 with KMS
   - Weekly classification job (ccoe-weekly) with tag-based bucket exclusions
3. **Post-Deployment** - `verify-macie.py`
   - Enables automated sensitive data discovery via AWS API (no Terraform resource exists)
   - Validates all 7 configuration checks

### Two-Phase Apply

Delegating Macie admin to the audit account auto-enables Macie there, which creates a resource Terraform doesn't know about. The entrypoint handles this with a two-phase apply:

1. **Phase 3a** - `terraform apply -target=module.macie_org` (+ KMS, S3, CloudWatch) — enables Macie and delegates admin
2. **Phase 3b** - Re-run `state_sync.py` — imports the auto-enabled audit Macie account
3. **Phase 3c** - Full `terraform apply` — configures org settings, export, classification job

### Timing Safeguards

`time_sleep` resources prevent race conditions between Macie API calls:

| Location | Duration | Purpose |
|----------|----------|---------|
| `macie-org` module | 15s | Wait after enabling Macie before delegating admin |
| Root `main.tf` | 30s | Wait after delegation before audit account configuration |
| `macie-config` module | 10s | Wait after audit Macie enable before org configuration |

### Bucket Exclusion Strategy

The weekly classification job excludes infrastructure buckets using **tag-based exclusion**:

- **Tag key:** `{resource_prefix}_macie_exclude` (e.g., `ccoe_macie_exclude`)
- **Tag value:** `true`
- Infrastructure buckets (tfstate, access-logs, findings, etc.) should be tagged by the projects that create them

The Macie API only supports `EQ`/`NE` comparators for bucket criteria (no prefix matching), so tag-based exclusion is the only viable approach for dynamic bucket sets.

### Dry-Run Support

`make plan` passes `--dry-run` to Python scripts:

| Script | Plan (dry-run) | Apply (normal) |
|--------|---------------|----------------|
| `discover.py` | Always writes tfvars (Terraform needs them) | Same |
| `state_sync.py` | Shows "Would import X" without modifying state | Imports resources |
| `verify-macie.py` | Checks discovery status, does not enable | Enables automated discovery |

## Directory Structure

```
portfolio-aws-org-macie/
├── entrypoint.sh           # Main orchestration script (two-phase apply)
├── config.yaml             # Deployment configuration
├── requirements.txt        # Python dependencies
├── discovery/
│   ├── discover.py         # AWS discovery, generates tfvars (--dry-run supported)
│   ├── state_sync.py       # Terraform state synchronization (--dry-run supported)
│   └── cloudwatch_logger.py # CloudWatch Logs streaming helper
├── post-deployment/
│   └── verify-macie.py     # Verification + automated discovery enablement (--dry-run supported)
├── terraform/
│   ├── main.tf             # Root module (KMS, S3, CloudWatch, module wiring, time_sleep)
│   ├── variables.tf        # Variable definitions
│   ├── outputs.tf          # Output definitions
│   ├── providers.tf        # Provider configurations (2 providers)
│   ├── versions.tf         # Terraform/provider version constraints (aws + time)
│   └── modules/
│       ├── macie-org/      # Enable Macie + delegated admin registration
│       ├── macie-config/   # Organization config, export, classification job
│       ├── kms/            # Reusable KMS key module
│       └── s3/             # Reusable S3 bucket module
├── Dockerfile
└── Makefile
```

## Commands

All code runs inside Docker containers. Use the Makefile:

```bash
# Build Docker image
make build

# Discover current AWS state
AWS_PROFILE=mgmt make discover

# Show Terraform plan (dry-run for Python scripts)
AWS_PROFILE=mgmt make plan

# Apply configuration (two-phase apply)
AWS_PROFILE=mgmt make apply

# Open interactive shell
AWS_PROFILE=mgmt make shell
```

### Local Development

```bash
# Python linting
ruff check discovery/ post-deployment/
ruff format --check discovery/ post-deployment/

# Terraform
cd terraform && terraform fmt -recursive && terraform validate

# Pre-commit
pre-commit run --all-files
```

## Configuration

Edit `config.yaml` to customize:

- `resource_prefix` - Prefix for all resource names. **Required.** Also determines the bucket exclusion tag key (`{prefix}_macie_exclude`).
- `deployment_name` - Used for CloudWatch log group naming. **Required.**
- `audit_account_role` - Cross-account role name (default: `OrganizationAccountAccessRole`)
- `bucket_exclusion_tag_value` - Tag value for bucket exclusions (default: `true`)

## Module Architecture

### Provider Aliases

- 1 management account provider (default)
- 1 audit account provider with cross-account role assumption
- Total: 2 providers + `hashicorp/time` for timing safeguards

### Key Modules

**Macie Org Module** - Enable Macie + delegated admin (management account context):
- `aws_macie2_account` (management)
- `time_sleep` (15s after enable)
- `aws_macie2_organization_admin_account` (delegate to audit)

**Macie Config Module** - Organization configuration (audit account context):
- `aws_macie2_account` (audit — imported via state_sync after delegation auto-enables it)
- `time_sleep` (10s after audit enable)
- `aws_macie2_organization_configuration` with auto_enable = true
- `aws_macie2_classification_export_configuration` (S3 + KMS)
- `aws_macie2_classification_job` (ccoe-weekly, Saturday, tag-based exclusion via `bucket_criteria`)

## Post-Deployment

### verify-macie.py

Validates and finalizes Macie configuration:
1. Service access enabled in Organizations
2. Delegated admin correctly configured
3. Macie enabled in management and audit accounts
4. Organization auto-enable applied
5. Classification export configured (S3 + KMS)
6. Automated sensitive data discovery enabled (enables via AWS API if not active — no Terraform resource exists)
7. ccoe-weekly classification job exists and is active

In `--dry-run` mode (used by `make plan`), check 6 only verifies status without enabling.

## Rules

- **No Claude Attribution** - Do not mention Claude, AI, or any AI assistant in commit messages, documentation, or code comments.
- **Use python3** - Always use `python3` instead of `python` when executing Python scripts.
- **Run pre-commit before pushing** - Always run `pre-commit run --all-files` before pushing changes.
