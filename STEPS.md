# Deployment Steps

This document describes every step that occurs during a `make apply` deployment, including where values are retrieved and decision logic.

## Pre-Deployment (entrypoint.sh)

### 0.1 AWS Credential Check

- Calls `aws sts get-caller-identity` to verify credentials
- Extracts `ACCOUNT_ID` and `CALLER_ARN` from the response
- **Fails if:** No valid AWS credentials are available

### 0.2 Load Configuration from config.yaml

- Reads `resource_prefix` from `/work/config.yaml` (required, fails if missing)
- Reads `deployment_name` from `/work/config.yaml` (required, fails if missing)
- Reads `primary_region` from `/work/config.yaml` (defaults to `us-east-1`)

### 0.3 CloudWatch Logging Setup

- Constructs log group name: `/{resource_prefix}/deployments/{deployment_name}`
- Creates CloudWatch log group via `aws logs create-log-group` (idempotent, ignores if exists)
- Creates initial log stream: `{timestamp}/config`
- Starts background `cloudwatch_logger.py` process reading from a FIFO pipe
- All subsequent phase output is tee'd to both stdout and CloudWatch via `tee_log` helper
- Each phase creates a separate log stream: `{timestamp}/{phase_name}`

### 0.4 State Bucket Verification

- Constructs bucket name: `{resource_prefix}-tfstate-{ACCOUNT_ID}`
- Calls `aws s3api head-bucket` to verify it exists
- State key: `macie/terraform.tfstate`
- **Fails if:** Bucket does not exist (must be created by `portfolio-aws-org-baseline` first)

### 0.5 Runtime Configuration Logging

- Writes timestamp, action, account ID, caller ARN, terraform args, and effective settings to the `config` log stream

## Phase 1: Discovery (discover.py)

### 1.1 Load config.yaml

- Reads from `/work/config.yaml` (Docker) or `../config.yaml` (local fallback)
- Extracts `resource_prefix` (required)

### 1.2 Get Caller Identity

- Calls `sts.get_caller_identity()` to get `management_account_id`
- Uses `primary_region` from config for the initial STS client

### 1.3 Read SSM Parameter

- Path: `/{resource_prefix}/org-baseline/config`
- Written by `portfolio-aws-org-baseline` during its deployment
- Contains JSON with: `audit_account_id`, `primary_region`, `organization_id`, `tags`
- **If SSM parameter exists:** Values override config.yaml defaults
- **If SSM parameter missing:** Falls back to config.yaml values

### 1.4 Merge Configuration

Priority order (highest wins):
1. `config.yaml` explicit overrides (e.g., `audit_account_id` if set)
2. SSM parameter values
3. Hardcoded defaults (`primary_region` defaults to `us-east-1`)

Resolved values:
- `primary_region` - from config.yaml or SSM
- `audit_account_id` - from config.yaml or SSM (required)
- `custom_tags` - from SSM `tags` field, or config.yaml `tags`
- `bucket_exclusion_tag_value` - from config.yaml (default: `true`)
- `audit_account_role` - from config.yaml (default: `OrganizationAccountAccessRole`)

### 1.5 Discover Macie Organization State

- **Fails if:** `audit_account_id` is empty (not in config or SSM)
- Calls `organizations.list_delegated_administrators(ServicePrincipal="macie.amazonaws.com")`
- If a delegated admin is registered and matches `audit_account_id`:
  - Assumes role into audit account via `OrganizationAccountAccessRole`
  - Calls `macie2.get_macie_session()` to check if Macie is enabled
  - Calls `macie2.describe_organization_configuration()` to check auto-enable status
  - Calls `macie2.get_automated_discovery_configuration()` to check discovery status

### 1.6 Check Access Logs Bucket

- Assumes role into audit account
- Calls `s3.head_bucket` for `{resource_prefix}-s3-access-logs-{audit_account_id}`
- Sets `access_logs_bucket_exists = True/False`
- This bucket is created by `portfolio-aws-org-baseline` and used for S3 access logging on the findings bucket
- **If missing:** Prints warning, findings bucket created without access logging

### 1.7 Write discovery.json

- Writes Macie org status to `/work/terraform/discovery.json`
- Contains: `macie_org_exists`, `macie_delegated_admin`, `macie_auto_enable`, `macie_automated_discovery`

### 1.8 Write bootstrap.auto.tfvars.json

- Writes to `/work/terraform/bootstrap.auto.tfvars.json`
- Contains: `primary_region`, `resource_prefix`, `deployment_name`, `management_account_id`, `audit_account_id`, `audit_account_role`, `access_logs_bucket_exists`, `bucket_exclusion_tag_value`, `custom_tags`
- Terraform reads this file automatically during plan/apply

## Phase 2: Terraform Init + State Sync

### 2.1 Clean Local State

- Removes `.terraform/` and `.terraform.lock.hcl` to prevent stale backend config

### 2.2 Terraform Init

- Initializes with S3 backend configuration:
  - `bucket` = `{resource_prefix}-tfstate-{ACCOUNT_ID}`
  - `key` = `macie/terraform.tfstate`
  - `region` = `{primary_region}`
  - `encrypt` = `true`
- Downloads provider plugins (hashicorp/aws ~> 5.0, hashicorp/time ~> 0.9)
- Initializes 2 provider configurations (management + audit)

### 2.3 State Sync (state_sync.py)

Imports existing AWS resources into Terraform state to avoid conflicts on first apply or after manual changes.

#### 2.3.0 Provider Warm-Up (empty state only)

- **Triggered when:** Terraform state has 0 resources (first deployment or after state reset)
- Runs `terraform plan -refresh-only -input=false -compact-warnings` (timeout: 300s)
- Initializes both provider configurations and caches credentials
- **Why:** Each `terraform import` reinitializes providers. On empty state, this ensures providers are ready before imports begin.
- Import commands use retry logic (2 attempts with 5s delay) as defense-in-depth

#### 2.3.1 Sync CloudWatch Log Group

- Address: `aws_cloudwatch_log_group.deployments`
- Log group is pre-created by entrypoint.sh (step 0.3) before Terraform runs
- Import ID: `/{resource_prefix}/deployments/{deployment_name}`
- **Skipped if:** Already in state

#### 2.3.2 Sync Macie Management Account

- Address: `module.macie_org[0].aws_macie2_account.management`
- Calls `macie2.get_macie_session()` from management account
- Import ID: `macie`
- **Skipped if:** Already in state or Macie not enabled

#### 2.3.3 Sync Macie Delegated Admin

- Address: `module.macie_org[0].aws_macie2_organization_admin_account.main`
- Calls `organizations.list_delegated_administrators(ServicePrincipal="macie.amazonaws.com")`
- Import ID: `{audit_account_id}`
- **Skipped if:** Already in state or delegated admin not configured

#### 2.3.4 Sync Macie Audit Account

- Address: `module.macie_config[0].aws_macie2_account.audit`
- Assumes role into audit account via `OrganizationAccountAccessRole`
- Calls `macie2.get_macie_session()` from audit account
- Import ID: `macie`
- **Skipped if:** Already in state or Macie not enabled in audit account
- **Key scenario:** After delegation (Phase 3a), AWS auto-enables Macie in the audit account. Phase 3b re-runs state_sync to import this auto-created resource.

## Phase 3: Terraform Apply (Two-Phase)

On `make apply`, Terraform uses a two-phase apply to handle the delegation auto-enablement behavior. On `make plan` or `make destroy`, a single-phase operation runs instead.

### Phase 3a: Management Account + Infrastructure

Targets applied first:
- `module.macie_org` - Enables Macie in management account, registers audit as delegated admin
- `module.kms_deployment_logs` - KMS key for CloudWatch log encryption
- `aws_cloudwatch_log_group.deployments` - Deployment log group with 365-day retention
- `module.kms_macie_findings` - KMS key for findings encryption (audit account)
- `module.s3_macie_findings` - Findings export bucket (audit account)

#### Management Account Resources

**Macie Enablement:**
- `aws_macie2_account.management` - Enables Macie in the management account
- 15-second `time_sleep` between enablement and delegation (eventual consistency)

**Delegated Admin:**
- `aws_macie2_organization_admin_account.main` - Registers audit account as Macie delegated admin
- This triggers AWS to auto-enable Macie in the audit account

**Deployment Logging (primary region only):**
- `module.kms_deployment_logs` - KMS key with CloudWatch Logs service principal
- `aws_cloudwatch_log_group.deployments` - Log group at `/{resource_prefix}/deployments/{deployment_name}`

#### Audit Account Resources (Infrastructure)

**KMS Key:**
- `module.kms_macie_findings` - Encryption key for findings bucket
- Service principal: `macie.amazonaws.com`
- Actions: GenerateDataKey, Encrypt, Decrypt, ReEncrypt*, DescribeKey
- Account root also has full kms:* access

**S3 Bucket:**
- `module.s3_macie_findings` - Findings export destination
- Bucket name: `{resource_prefix}-macie-findings-{audit_account_id}`
- KMS encryption using the findings KMS key
- Versioning enabled
- Public access block (all four settings enabled)
- Bucket policy with:
  - DenyNonSSL - Denies all requests without TLS
  - AllowMaciePutObject - Allows `macie.amazonaws.com` to write with `bucket-owner-full-control` ACL
  - AllowMacieGetBucketLocation - Allows `macie.amazonaws.com` to read bucket location
- Access logging to `{resource_prefix}-s3-access-logs-{audit_account_id}` (conditional on `access_logs_bucket_exists`)
- Lifecycle rules:
  - Abort incomplete multipart uploads after 7 days
  - Transition to STANDARD_IA at 90 days
  - Transition to GLACIER at 365 days
  - Expire at 730 days

### Phase 3b: Re-Sync State After Delegation

- Re-runs `state_sync.py` (without `--dry-run`)
- Imports the now-existing `module.macie_config[0].aws_macie2_account.audit` (auto-enabled by delegation in Phase 3a)
- Also re-checks other resources in case of drift

### Phase 3c: Full Apply

Applies all remaining resources (now that the audit Macie account is in state):

**30-second wait** (`time_sleep.after_delegation`) after `module.macie_org` for delegation propagation.

#### Audit Account Resources (Macie Configuration)

**Macie Account:**
- `aws_macie2_account.audit` - Already exists (imported in Phase 3b), Terraform takes ownership
- `finding_publishing_frequency = "FIFTEEN_MINUTES"`
- 10-second `time_sleep` after enablement for Macie initialization

**Organization Configuration:**
- `aws_macie2_organization_configuration.main` - `auto_enable = true` for all org members

**Classification Export:**
- `aws_macie2_classification_export_configuration.main` - S3 destination with KMS encryption
- Bucket: `{resource_prefix}-macie-findings-{audit_account_id}`
- Key prefix: `macie-findings/`
- KMS key: findings KMS key ARN

**Classification Job (ccoe-weekly):**
- `aws_macie2_classification_job.ccoe_weekly` - Scheduled job
- Name: `ccoe-weekly`
- Type: `SCHEDULED`
- Schedule: Every Saturday
- Sampling: 100%
- Exclusions: Buckets tagged with `{resource_prefix}_macie_exclude=true` via `tag_criterion` in `bucket_criteria.excludes`
- `lifecycle { ignore_changes = [initial_run] }` to prevent Terraform from re-triggering the initial run

## Phase 4: Post-Deployment Verification (verify-macie.py)

Runs after apply (required) and after plan (optional, non-blocking with `--dry-run`).

### 4.1 Check Service Access

- Calls `organizations.list_aws_service_access_for_organization()`
- Verifies `macie.amazonaws.com` is in the enabled services list

### 4.2 Check Delegated Admin

- Calls `organizations.list_delegated_administrators(ServicePrincipal="macie.amazonaws.com")`
- Verifies the admin account ID matches `audit_account_id`

### 4.3 Check Macie Enabled (2 accounts)

For management and audit accounts:
- Assumes role into the account (or uses current creds for management)
- Calls `macie2.get_macie_session()`
- Verifies status is `ENABLED`
- Reports `findingPublishingFrequency`

### 4.4 Check Organization Auto-Enable

- Assumes role into audit account (delegated admin)
- Calls `macie2.describe_organization_configuration()`
- Verifies `autoEnable = true`

### 4.5 Check Classification Export

- Calls `macie2.get_classification_export_configuration()` from audit account
- Verifies `s3Destination` is configured
- Reports bucket name and KMS key ARN

### 4.6 Enable Automated Sensitive Data Discovery

- Calls `macie2.get_automated_discovery_configuration()` from audit account
- If already `ENABLED`: Reports OK
- If not enabled: Calls `macie2.update_automated_discovery_configuration(status="ENABLED")`
- **Why post-deployment?** No Terraform resource type exists for `aws_macie2_automated_discovery_configuration` yet (open feature request #34938)
- **On `--dry-run` (plan):** Read-only check, does not enable

### 4.7 Check ccoe-weekly Classification Job

- Calls `macie2.list_classification_jobs()` with filter `name = "ccoe-weekly"` from audit account
- Verifies job exists
- Reports job ID, status, and type

### Verification Exit Behavior

- **On plan:** Verification runs with `--dry-run` but failures don't block (non-zero exit ignored)
- **On apply:** Verification runs; failures produce a warning but don't fail the deployment

## Phase 5: Summary

- Outputs `terraform output -json macie_summary` containing:
  - `delegated_admin` - Audit account ID
  - `management_account` - Management account ID
  - `primary_region` - Primary AWS region
  - `findings_bucket` - S3 bucket name for findings export
  - `classification_job` - Classification job name (`ccoe-weekly`)
  - `bucket_exclusion_tag` - Tag key=value for bucket exclusions

## CloudWatch Log Streams

Each deployment creates log streams under `/{resource_prefix}/deployments/{deployment_name}`:

| Stream | Content |
|--------|---------|
| `{timestamp}/config` | Runtime configuration, effective settings |
| `{timestamp}/discover` | Discovery output |
| `{timestamp}/init` | Terraform init output |
| `{timestamp}/import` | State sync (terraform import) output |
| `{timestamp}/apply-phase1` | Phase 3a: Management + infrastructure apply |
| `{timestamp}/import-phase2` | Phase 3b: Re-sync after delegation |
| `{timestamp}/apply` | Phase 3c: Full apply output |
| `{timestamp}/verify` | Post-deployment verification output |
| `{timestamp}/summary` | Final deployment summary |

## Terraform Resources Summary

Total resources managed (20):

| Resource | Module | Account | Count |
|----------|--------|---------|-------|
| `aws_cloudwatch_log_group` | root | Management | 1 |
| `aws_kms_key` + `aws_kms_alias` | kms_deployment_logs | Management | 2 |
| `aws_kms_key` + `aws_kms_alias` | kms_macie_findings | Audit | 2 |
| `aws_s3_bucket` + versioning + encryption + public access block + policy + lifecycle + logging | s3_macie_findings | Audit | 7 |
| `aws_macie2_account` (management) | macie-org | Management | 1 |
| `aws_macie2_organization_admin_account` | macie-org | Management | 1 |
| `time_sleep` (after enable, after delegation) | macie-org, root | Management | 2 |
| `aws_macie2_account` (audit) | macie-config | Audit | 1 |
| `time_sleep` (after audit enable) | macie-config | Audit | 1 |
| `aws_macie2_organization_configuration` | macie-config | Audit | 1 |
| `aws_macie2_classification_export_configuration` | macie-config | Audit | 1 |
| `aws_macie2_classification_job` | macie-config | Audit | 1 |
