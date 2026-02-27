FROM python:3.11-slim

# Install system dependencies as root
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    unzip \
    git \
    jq \
    && rm -rf /var/lib/apt/lists/*

# Install Terraform
ARG TERRAFORM_VERSION=1.7.0
RUN curl -fsSL https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_amd64.zip -o terraform.zip \
    && unzip terraform.zip -d /usr/local/bin \
    && rm terraform.zip \
    && terraform version

# Install AWS CLI v2
RUN curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip" \
    && unzip awscliv2.zip \
    && ./aws/install \
    && rm -rf awscliv2.zip aws \
    && aws --version

# Create non-root user with home directory for AWS credentials
RUN groupadd -r macie && useradd -r -g macie -d /home/macie -m -s /sbin/nologin macie

# Set working directory
WORKDIR /work

# Copy requirements and install Python dependencies as root
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY config.yaml .
COPY discovery/ ./discovery/
COPY post-deployment/ ./post-deployment/
COPY terraform/ ./terraform/
COPY entrypoint.sh .

# Make entrypoint executable and set ownership
RUN chmod +x entrypoint.sh \
    && chown -R macie:macie /work

# Switch to non-root user
USER macie

# Default entrypoint
ENTRYPOINT ["./entrypoint.sh"]
