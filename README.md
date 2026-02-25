# Project Name

Brief description of your project.

## Prerequisites

- [Terraform](https://www.terraform.io/downloads) >= 1.0
- [AWS CLI](https://aws.amazon.com/cli/) configured with appropriate credentials
- [pre-commit](https://pre-commit.com/) (optional, for development)
- [tflint](https://github.com/terraform-linters/tflint) (optional, for linting)

## Quick Start

```bash
# Clone the repository
git clone https://github.com/your-username/your-repo.git
cd your-repo

# Initialize Terraform
cd terraform
terraform init

# Preview changes
terraform plan

# Apply changes
terraform apply
```

## Project Structure

```
.
├── .github/
│   ├── workflows/
│   │   ├── lint.yml          # Pre-commit linting pipeline
│   │   └── sast.yml          # Security scanning pipeline
│   └── CODEOWNERS            # Code ownership definitions
├── cloudformation/           # CloudFormation templates
├── scripts/                  # Utility scripts
├── terraform/
│   ├── main.tf               # Main Terraform configuration
│   ├── variables.tf          # Input variables
│   ├── outputs.tf            # Output definitions
│   ├── providers.tf          # Provider configuration
│   └── versions.tf           # Version constraints
├── .gitignore
├── .pre-commit-config.yaml   # Pre-commit hooks configuration
├── .tflint.hcl               # TFLint configuration
├── .terraform-docs.yml       # Terraform docs configuration
├── CLAUDE.md                 # AI assistant guidance
├── LICENSE
├── Makefile
└── README.md
```

## Branching Strategy

```
main (production)
 └── develop (integration)
      └── feature/* (new features)
```

| Branch | Purpose | Merges To |
|--------|---------|-----------|
| `main` | Production-ready code. Protected branch. | - |
| `develop` | Integration branch for features. | `main` (via PR) |
| `feature/*` | New features and changes. | `develop` (via PR) |

### Workflow

1. Create a feature branch from `develop`:
   ```bash
   git checkout develop
   git pull
   git checkout -b feature/my-feature
   ```

2. Make changes and commit:
   ```bash
   git add .
   git commit -m "feat: add my feature"
   ```

3. Push and create PR to `develop`:
   ```bash
   git push -u origin feature/my-feature
   ```

4. After review and merge to `develop`, create PR from `develop` to `main` for release.

## Development

### Setup Pre-commit Hooks

```bash
pip install pre-commit
pre-commit install
```

### Available Make Targets

```bash
make help       # Show available targets
make init       # Initialize Terraform
make plan       # Run Terraform plan
make apply      # Apply changes
make fmt        # Format code
make lint       # Run linters
make test       # Run tests
```

## Configuration

### Variables

| Name | Description | Default |
|------|-------------|---------|
| `aws_region` | AWS region for resources | `us-east-1` |
| `project_name` | Name of the project | `my-project` |
| `environment` | Environment name | `dev` |

## Security

This project includes automated security scanning via [portfolio-code-scanner](https://github.com/williambrady/portfolio-code-scanner). Security scans run on:
- Push to `main` or `develop` branches
- Pull requests
- Daily scheduled runs

## License

See [LICENSE](LICENSE) for details.
