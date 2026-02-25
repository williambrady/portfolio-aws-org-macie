.PHONY: help init plan apply destroy fmt validate lint test clean

# Default target
help:
	@echo "Available targets:"
	@echo "  init      - Initialize Terraform"
	@echo "  plan      - Run Terraform plan"
	@echo "  apply     - Apply Terraform changes"
	@echo "  destroy   - Destroy Terraform resources"
	@echo "  fmt       - Format Terraform and Python code"
	@echo "  validate  - Validate Terraform configuration"
	@echo "  lint      - Run linters"
	@echo "  test      - Run tests"
	@echo "  clean     - Clean generated files"
	@echo "  pre-commit- Run pre-commit hooks"

# Terraform targets
init:
	cd terraform && terraform init

plan:
	cd terraform && terraform plan

apply:
	cd terraform && terraform apply

destroy:
	cd terraform && terraform destroy

fmt:
	terraform fmt -recursive terraform/
	@if command -v black >/dev/null 2>&1; then black scripts/; fi

validate:
	cd terraform && terraform validate

lint:
	@if command -v tflint >/dev/null 2>&1; then cd terraform && tflint; fi
	@if command -v flake8 >/dev/null 2>&1; then flake8 scripts/; fi
	@if command -v pylint >/dev/null 2>&1; then pylint scripts/; fi

test:
	@if command -v pytest >/dev/null 2>&1; then pytest tests/; fi

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true

pre-commit:
	pre-commit run --all-files
