# Implementation Plan: portfolio-aws-org-macie

## Status: Complete

All phases implemented and validated with a successful `make apply` against a live AWS Organization.

## Overview

Deploy Macie organization-wide with delegated admin, findings export, automated sensitive data discovery, and a weekly classification job with tag-based bucket exclusions.

**Key difference from GuardDuty**: Macie is single-region (primary region only), so we need only 2 providers (management + audit) instead of 51.

## Architecture

```
Management Account (primary region)
├── aws_macie2_account (enable Macie)
├── time_sleep (15s - wait for Macie init)
├── aws_macie2_organization_admin_account (delegate to audit)
├── time_sleep (30s - wait for delegation propagation)
├── KMS key for deployment CloudWatch logs
└── CloudWatch log group for deployment logging

Audit Account (primary region, delegated admin)
├── aws_macie2_account (auto-enabled by delegation, imported via state_sync)
├── time_sleep (10s - wait for Macie init)
├── aws_macie2_organization_configuration (auto-enable for org)
├── aws_macie2_classification_export_configuration (S3 + KMS)
├── aws_macie2_classification_job (ccoe-weekly, Saturday, tag-based exclusion)
├── Automated sensitive data discovery (enabled via post-deployment script)
├── KMS key for findings encryption
└── S3 bucket for findings export
```

## Key Design Decisions

### Two-Phase Apply

Delegating admin auto-enables Macie in the audit account (AWS behavior). This creates a resource outside Terraform's control. The entrypoint handles this with:
1. Phase 3a: `terraform apply -target=module.macie_org` (+ infrastructure)
2. Phase 3b: Re-run `state_sync.py` to import audit Macie account
3. Phase 3c: Full `terraform apply`

### Tag-Based Bucket Exclusion

The Macie API only supports `EQ`/`NE` comparators in `bucket_criteria` — no prefix matching (`STARTS_WITH` is not available). Additionally, `bucket_criteria` and `bucket_definitions` are mutually exclusive.

Solution: Tag-based exclusion using `{resource_prefix}_macie_exclude=true`. Infrastructure buckets should be tagged by the projects that create them (org-baseline, org-guardduty, etc.).

### Automated Discovery via Post-Deployment

`aws_macie2_automated_discovery_configuration` does not exist in the Terraform AWS provider (open feature request #34938). Automated discovery is enabled via `verify-macie.py` using the boto3 `update_automated_discovery_configuration` API.

### Dry-Run for Plan

`make plan` passes `--dry-run` to `state_sync.py` (no state imports) and `verify-macie.py` (no automated discovery enablement), ensuring plan is read-only.

## Implementation Phases

### Phase 1: Project Infrastructure
- `Dockerfile` - python:3.11-slim, Terraform 1.7.0, AWS CLI v2, non-root `macie:macie` user
- `entrypoint.sh` - Two-phase apply, dry-run support, CloudWatch log streaming
- `config.yaml` - `resource_prefix`, `deployment_name`, `audit_account_role`, tag exclusion config
- `requirements.txt` - `pyyaml>=6.0`, `boto3>=1.34.0`
- `pyproject.toml` - ruff config (line-length=120, py311)
- `Makefile` - Docker-based targets: build/discover/plan/apply/destroy/shell/clean
- `.pre-commit-config.yaml` - pre-commit-hooks, terraform_fmt, ruff
- `.gitignore` / `.dockerignore` - Updated for discovery pattern

### Phase 2: Terraform Foundation
- `terraform/versions.tf` - `>= 1.5.0`, aws `~> 5.0`, time `~> 0.9`, S3 backend
- `terraform/providers.tf` - 2 providers: default (management) + `audit` (cross-account)
- `terraform/variables.tf` - Macie-specific variables

### Phase 3: Terraform Modules
- `terraform/modules/kms/` - Copied from guardduty (generic, provider-agnostic)
- `terraform/modules/s3/` - Copied from guardduty (generic, policy injected by caller)
- `terraform/modules/macie-org/` - Enable Macie + `time_sleep` + delegate admin
- `terraform/modules/macie-config/` - Audit account: org config, export, classification job with `bucket_criteria`

### Phase 4: Main Terraform Wiring
- `terraform/main.tf` - Wire all modules with `time_sleep.after_delegation` between phases
- `terraform/outputs.tf` - Summary outputs including `bucket_exclusion_tag`

### Phase 5: Discovery & State Sync Scripts
- `discovery/cloudwatch_logger.py` - Copied from guardduty (generic utility)
- `discovery/discover.py` - SSM config, Macie org state, generate tfvars (`--dry-run` supported)
- `discovery/state_sync.py` - Import existing Macie resources (`--dry-run` supported)

### Phase 6: Post-Deployment
- `post-deployment/verify-macie.py` - 7 checks + automated discovery enablement (`--dry-run` supported)

### Phase 7: CI/CD & Documentation
- `.github/workflows/lint.yml` - ruff check/format on discovery/ and post-deployment/
- `.github/workflows/sast.yml` - `williambrady/portfolio-code-scanner@v1`
- `.github/CODEOWNERS` - Set correct username
- `CLAUDE.md` - Project-specific commands and architecture

### Files Deleted
- `cloudformation/.gitkeep` - No CloudFormation in this project
- `scripts/.gitkeep` - Scripts live in discovery/ and post-deployment/
- `.terraform-docs.yml` - Not used in this pattern
- `.tflint.hcl` - Not used in this pattern

## Validation

Verified with a successful deployment:
1. `make build` - Docker image builds
2. `make plan` - Dry-run plan with no state mutations
3. `make apply` - Full deployment (20 resources created, all 7 checks passed)
4. `pre-commit run --all-files` - All hooks pass
5. `ruff check` / `ruff format --check` - Python linting passes
6. `terraform fmt -check -recursive` - Formatting correct
