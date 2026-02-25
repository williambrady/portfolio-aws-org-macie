# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

[Brief description of what this project does]

## Common Commands

### Pre-commit Hooks
```bash
# Install hooks (first time setup)
pip install pre-commit
pre-commit install

# Run all checks manually
pre-commit run --all-files
```

### Terraform
```bash
cd terraform
terraform init      # Initialize
terraform fmt       # Format
terraform validate  # Validate
terraform plan      # Preview changes
terraform apply     # Apply changes
```

### Using the Makefile
```bash
make help       # Show available targets
make init       # Initialize Terraform
make plan       # Run Terraform plan
make apply      # Apply changes
make fmt        # Format code
make lint       # Run linters
make test       # Run tests
```

## Architecture

[Describe the architecture of your project]

### Key Components

- `terraform/` - Infrastructure as Code definitions
- `cloudformation/` - CloudFormation templates (if applicable)
- `scripts/` - Utility scripts

## Important Notes

[Add any project-specific notes, conventions, or warnings]
