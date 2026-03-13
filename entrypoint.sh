#!/bin/bash
set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo "============================================"
echo "  Macie Organization Deployment"
echo "============================================"
echo ""

# Check AWS credentials
echo -e "${YELLOW}Checking AWS credentials...${NC}"
if ! aws sts get-caller-identity > /dev/null 2>&1; then
    echo -e "${RED}Error: AWS credentials not configured${NC}"
    echo "Please provide AWS credentials via:"
    echo "  - Environment variables (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)"
    echo "  - Mounted ~/.aws directory with AWS_PROFILE set"
    exit 1
fi

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
CALLER_ARN=$(aws sts get-caller-identity --query Arn --output text)
echo -e "${GREEN}Authenticated to account: ${ACCOUNT_ID}${NC}"
echo -e "${GREEN}Caller: ${CALLER_ARN}${NC}"
echo ""

# Load configuration from config.yaml
echo -e "${YELLOW}Loading configuration...${NC}"

PRIMARY_REGION=$(python3 -c "import yaml; print(yaml.safe_load(open('/work/config.yaml')).get('primary_region', 'us-east-1'))" 2>/dev/null || echo "us-east-1")
RESOURCE_PREFIX=$(python3 -c "import yaml; print(yaml.safe_load(open('/work/config.yaml'))['resource_prefix'])" 2>/dev/null)
if [ -z "${RESOURCE_PREFIX}" ]; then
    echo -e "${RED}Error: resource_prefix is required in config.yaml${NC}"
    exit 1
fi
DEPLOYMENT_NAME=$(python3 -c "import yaml; v=yaml.safe_load(open('/work/config.yaml')).get('deployment_name'); print(v if v else '')" 2>/dev/null)
if [ -z "${DEPLOYMENT_NAME}" ]; then
    echo -e "${RED}Error: deployment_name is required in config.yaml${NC}"
    exit 1
fi
echo -e "${GREEN}Primary region: ${PRIMARY_REGION}${NC}"
echo -e "${GREEN}Resource prefix: ${RESOURCE_PREFIX}${NC}"
echo -e "${GREEN}Deployment name: ${DEPLOYMENT_NAME}${NC}"
echo ""

# Parse command line arguments (needed early for timestamp)
ACTION="${1:-apply}"
TERRAFORM_ARGS="${@:2}"

# Deployment logging to CloudWatch Logs (always enabled)
DEPLOY_TIMESTAMP=$(date -u +"%Y-%m-%d-%H-%M-%S")
CW_LOG_GROUP="/${RESOURCE_PREFIX}/deployments/${DEPLOYMENT_NAME}"
CW_LOG_PREFIX="${DEPLOY_TIMESTAMP}"
CW_INITIAL_STREAM="${CW_LOG_PREFIX}/config"
echo -e "${YELLOW}Streaming deployment logs to CloudWatch Logs${NC}"
echo -e "${BLUE}  Log group:  ${CW_LOG_GROUP}${NC}"
echo -e "${BLUE}  Log prefix: ${CW_LOG_PREFIX}/${NC}"

# Ensure log group exists before Terraform runs (idempotent)
# Terraform is the source of truth for retention and tags
aws logs create-log-group --log-group-name "${CW_LOG_GROUP}" \
    --region "${PRIMARY_REGION}" 2>/dev/null || true
aws logs create-log-stream --log-group-name "${CW_LOG_GROUP}" \
    --log-stream-name "${CW_INITIAL_STREAM}" \
    --region "${PRIMARY_REGION}" 2>/dev/null || true

CW_FIFO="/tmp/cw-fifo-$$"
mkfifo "${CW_FIFO}"
python3 /work/discovery/cloudwatch_logger.py \
    "${CW_LOG_GROUP}" "${CW_INITIAL_STREAM}" "${PRIMARY_REGION}" < "${CW_FIFO}" &
CW_LOGGER_PID=$!
exec 3>"${CW_FIFO}"

# Helper to tee output to stdout and CloudWatch Logs.
# Usage: some_command 2>&1 | tee_log <phase>
# The phase is used to create a separate CloudWatch log stream per phase.
tee_log() {
    local phase="${1:-}"
    if [ -n "${CW_LOGGER_PID}" ] && kill -0 "${CW_LOGGER_PID}" 2>/dev/null; then
        if [ -n "${phase}" ]; then
            printf '\n###STREAM:%s\n' "${CW_LOG_PREFIX}/${phase}" >&3
        fi
        tee /dev/fd/3
    else
        cat
    fi
}

# EXIT trap to clean up CloudWatch logger on any exit
cleanup_logger() {
    local exit_code=$?

    # Close CloudWatch logger
    if [ -n "${CW_LOGGER_PID:-}" ]; then
        exec 3>&- 2>/dev/null || true
        wait "${CW_LOGGER_PID}" 2>/dev/null || true
        rm -f "${CW_FIFO}" 2>/dev/null || true
    fi

    exit $exit_code
}
trap cleanup_logger EXIT

# Capture runtime configuration as the first log stream
{
    echo "Runtime Configuration"
    echo "=================================================="
    echo ""
    echo "Timestamp:      $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    echo "Action:         ${ACTION}"
    echo "Account ID:     ${ACCOUNT_ID}"
    echo "Caller ARN:     ${CALLER_ARN}"
    echo "Terraform Args: ${TERRAFORM_ARGS:-<none>}"
    echo ""
    echo "Effective Settings:"
    echo "  resource_prefix:    ${RESOURCE_PREFIX}"
    echo "  deployment_name:    ${DEPLOYMENT_NAME}"
    echo "  primary_region:     ${PRIMARY_REGION}"
    echo ""
    echo "config.yaml:"
    echo "=================================================="
    cat /work/config.yaml
    echo ""
    echo "=================================================="
} 2>&1 | tee_log "config"

# State bucket configuration - reuse the org-baseline state bucket with a separate key prefix
STATE_BUCKET="${RESOURCE_PREFIX}-tfstate-${ACCOUNT_ID}"
STATE_KEY="macie/terraform.tfstate"
STATE_REGION="${PRIMARY_REGION}"

# Verify the state bucket exists (created by portfolio-aws-org-baseline)
echo -e "${YELLOW}Checking Terraform state bucket...${NC}"
if ! aws s3api head-bucket --bucket "${STATE_BUCKET}" 2>/dev/null; then
    echo -e "${RED}Error: State bucket '${STATE_BUCKET}' does not exist${NC}"
    echo "The state bucket must be created by portfolio-aws-org-baseline first."
    echo "Run 'make apply' in portfolio-aws-org-baseline before deploying Macie."
    exit 1
fi
echo -e "${GREEN}State bucket exists: ${STATE_BUCKET}${NC}"
echo -e "${GREEN}State key: ${STATE_KEY}${NC}"
echo ""

case "$ACTION" in
    discover)
        echo -e "${YELLOW}Running discovery only...${NC}"
        python3 /work/discovery/discover.py 2>&1 | tee_log "discover"
        exit 0
        ;;
    shell)
        echo -e "${YELLOW}Opening interactive shell...${NC}"
        exec /bin/bash
        ;;
    plan)
        TF_ACTION="plan"
        DRY_RUN="--dry-run"
        ;;
    apply)
        TF_ACTION="apply -auto-approve"
        DRY_RUN=""
        ;;
    destroy)
        TF_ACTION="destroy -auto-approve"
        DRY_RUN=""
        ;;
    *)
        echo "Usage: $0 [discover|plan|apply|destroy|shell]"
        exit 1
        ;;
esac

# Phase 1: Discovery
echo ""
echo "============================================"
echo "  Phase 1: Discovery"
echo "============================================"
echo ""
python3 /work/discovery/discover.py 2>&1 | tee_log "discover"
echo ""

# Phase 2: Terraform Init
echo ""
echo "============================================"
echo "  Phase 2: Terraform Init"
echo "============================================"
echo ""

cd /work/terraform

# Clear local Terraform state to prevent stale backend config
rm -rf .terraform .terraform.lock.hcl

# Initialize Terraform with S3 backend
echo -e "${YELLOW}Initializing Terraform...${NC}"
terraform init -input=false -reconfigure \
    -backend-config="bucket=${STATE_BUCKET}" \
    -backend-config="key=${STATE_KEY}" \
    -backend-config="region=${STATE_REGION}" \
    -backend-config="encrypt=true" 2>&1 | tee_log "init"

# Sync existing resources into Terraform state
echo ""
echo -e "${YELLOW}Syncing Terraform state with existing resources...${NC}"
python3 /work/discovery/state_sync.py ${DRY_RUN} 2>&1 | tee_log "import"

# Phase 3: Terraform Plan/Apply
echo ""
echo "============================================"
echo "  Phase 3: Terraform ${TF_ACTION}"
echo "============================================"
echo ""

if [ "$TF_ACTION" = "apply -auto-approve" ]; then
    # Two-phase apply:
    # Phase 3a: Apply macie_org first (enables Macie in mgmt + delegates admin to audit).
    #           Delegating admin auto-enables Macie in the audit account, which means
    #           resources created by AWS outside Terraform need to be imported.
    # Phase 3b: Re-run state_sync to import the now-existing audit Macie account,
    #           then run the full apply.
    echo -e "${YELLOW}Phase 3a: Applying Macie organization (management account)...${NC}"
    terraform apply -auto-approve \
        -target=module.macie_org \
        -target=module.kms_deployment_logs \
        -target=aws_cloudwatch_log_group.deployments \
        -target=module.kms_macie_findings \
        -target=module.s3_macie_findings \
        ${TERRAFORM_ARGS} 2>&1 | tee_log "apply-phase1"

    echo ""
    echo -e "${YELLOW}Phase 3b: Re-syncing state after delegation...${NC}"
    python3 /work/discovery/state_sync.py 2>&1 | tee_log "import-phase2"

    echo ""
    echo -e "${YELLOW}Phase 3c: Applying full configuration...${NC}"
    terraform apply -auto-approve ${TERRAFORM_ARGS} 2>&1 | tee_log "apply"
else
    echo -e "${YELLOW}Running terraform ${TF_ACTION}...${NC}"
    terraform ${TF_ACTION} ${TERRAFORM_ARGS} 2>&1 | tee_log "${TF_ACTION%% *}"
fi

# Phase 4: Post-Deployment Verification
if [ "$TF_ACTION" = "plan" ]; then
    echo ""
    echo "============================================"
    echo "  Phase 4: Macie Organization Preview"
    echo "============================================"
    echo ""
    echo -e "${YELLOW}Verifying Macie organization configuration...${NC}"
    python3 /work/post-deployment/verify-macie.py --dry-run 2>&1 | tee_log "verify" || true
    echo ""

    # Enroll existing member accounts (dry-run preview)
    AUDIT_ACCOUNT_ID=$(jq -r '.audit_account_id // empty' /work/terraform/bootstrap.auto.tfvars.json 2>/dev/null)
    if [ -n "$AUDIT_ACCOUNT_ID" ]; then
        echo -e "${YELLOW}Checking Macie member enrollment...${NC}"
        python3 /work/post-deployment/enroll-macie-members.py \
            --audit-account-id "$AUDIT_ACCOUNT_ID" \
            --region "${PRIMARY_REGION}" 2>&1 | tee_log "enroll-members" || true
        echo ""
    fi
fi

if [ "$TF_ACTION" = "apply -auto-approve" ]; then
    echo ""
    echo "============================================"
    echo "  Phase 4: Post-Deployment Verification"
    echo "============================================"
    echo ""

    echo -e "${YELLOW}Verifying Macie organization configuration...${NC}"
    MACIE_EXIT_CODE=0
    python3 /work/post-deployment/verify-macie.py 2>&1 | tee_log "verify" || MACIE_EXIT_CODE=$?

    if [ $MACIE_EXIT_CODE -eq 0 ]; then
        echo -e "${GREEN}Macie organization verification completed successfully${NC}"
    else
        echo -e "${YELLOW}Warning: Macie verification encountered issues (exit code: $MACIE_EXIT_CODE)${NC}"
    fi
    echo ""

    # Enroll existing member accounts
    AUDIT_ACCOUNT_ID=$(jq -r '.audit_account_id // empty' /work/terraform/bootstrap.auto.tfvars.json 2>/dev/null)
    if [ -n "$AUDIT_ACCOUNT_ID" ]; then
        echo -e "${YELLOW}Enrolling existing member accounts in Macie...${NC}"
        python3 /work/post-deployment/enroll-macie-members.py \
            --audit-account-id "$AUDIT_ACCOUNT_ID" \
            --region "${PRIMARY_REGION}" \
            --apply 2>&1 | tee_log "enroll-members" || true
        echo ""
    else
        echo -e "${YELLOW}Skipping member enrollment (audit account ID not found)${NC}"
    fi
fi

# Phase 5: Summary
if [ "$TF_ACTION" = "apply -auto-approve" ]; then
    echo ""
    echo "============================================"
    echo "  Phase 5: Summary"
    echo "============================================"
    echo ""
    terraform output -json macie_summary 2>/dev/null | jq . | tee_log "summary" || echo "No summary output available"
    echo ""
    echo -e "${GREEN}Macie organization deployment complete!${NC}"
fi
