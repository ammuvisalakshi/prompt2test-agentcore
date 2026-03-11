# Terraform Deployment - Prompt2TestAgentCore

Deploy your **entire** Prompt2TestAgentCore agent to AWS using Terraform — from ECR repository creation through Bedrock AgentCore runtime setup.

## Prerequisites

1. **Terraform installed** (`terraform --version`)
   - [Download Terraform](https://www.terraform.io/downloads)

2. **AWS CLI configured**
   ```bash
   aws configure --profile kushi
   export AWS_PROFILE=kushi
   ```

3. **Docker installed** (for building image)
   - [Download Docker](https://www.docker.com/products/docker-desktop)
   - Or use AWS CloudShell (has Docker pre-installed)

4. **IAM permissions**
   - User must have permissions for: ECR, Bedrock, AgentCore, IAM services

---

## Complete Workflow

### Phase 1: Infrastructure (Terraform)

```bash
cd terraform
terraform init
terraform plan
terraform apply
```

This creates:
- ✅ ECR Repository
- ✅ AgentCore Runtime
- ✅ Browser Runtime
- ✅ Memory Store
- ✅ Runtime Endpoint
- ✅ IAM Roles

### Phase 2: Docker Image (Build & Push)

```bash
# Get ECR credentials from Terraform output
ECR_URL=$(terraform output -raw ecr_repository_url)
AWS_REGION=$(terraform output -raw aws_region)

# Authenticate with ECR
aws ecr get-login-password --region $AWS_REGION | \
  docker login --username AWS --password-stdin $ECR_URL

# Build for arm64 (required by AgentCore)
docker buildx build --platform linux/arm64 \
  -t $ECR_URL:latest \
  --push ..  # Build from parent directory
```

### Phase 3: Deploy & Test

```bash
# Get runtime ARN from Terraform output
RUNTIME_ARN=$(terraform output -raw runtime_arn)

# Wait a few minutes for container to start, then test
aws bedrock-agentcore invoke-agent-runtime \
  --runtime-arn "$RUNTIME_ARN" \
  --runtime-session-id "test-session-1234567890123456789012345" \
  --payload '{"prompt": "What is 2+2?"}' \
  --region us-east-1
```

---

## Step-by-Step Instructions

### Step 1: Initialize Terraform

```bash
cd terraform
terraform init
```

Expected output:
```
✅ Terraform has been successfully configured!
```

### Step 2: Create Configuration File

```bash
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars` if needed (defaults are good for most cases):

```hcl
aws_region = "us-east-1"
environment = "dev"
ecr_repository_name = "prompt2test-agent"
image_tag = "latest"
```

### Step 3: Plan Infrastructure

```bash
terraform plan
```

Review what will be created. Should show:
```
Plan: 8 to add, 0 to change, 0 to destroy
```

### Step 4: Deploy Infrastructure

```bash
terraform apply
```

Type **yes** when prompted.

**Wait for completion** — takes 1-2 minutes.

Output shows:
```
✅ Apply complete!

Outputs:
ecr_repository_url = "590183962483.dkr.ecr.us-east-1.amazonaws.com/prompt2test-agent"
runtime_arn = "arn:aws:bedrock-agentcore:us-east-1:590183962483:runtime/prompt2test-agentcore-..."
browser_endpoint = "https://xxx.browser.bedrock-agentcore.us-east-1.amazonaws.com/mcp"
...
```

### Step 5: Build & Push Docker Image

Now that ECR is created, build your Docker image:

```bash
# Get ECR repository URL from Terraform output
ECR_REPO=$(terraform output -raw ecr_repository_url)

# Authenticate with ECR
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin $ECR_REPO

# Create buildx builder for arm64
docker buildx create --name builder --driver docker-container --use

# Build for arm64 and push to ECR (from project root)
cd ..
docker buildx build --platform linux/arm64 \
  -t $ECR_REPO:latest \
  --push .

# Wait 5-10 minutes for build and push to complete
```

### Step 6: Verify Everything

Check that image is in ECR:

```bash
aws ecr describe-images \
  --repository-name prompt2test-agent \
  --region us-east-1
```

Check that runtime is Active:

```bash
RUNTIME_ARN=$(cd terraform && terraform output -raw runtime_arn)
aws bedrock-agentcore describe-runtime \
  --runtime-arn "$RUNTIME_ARN" \
  --region us-east-1
```

### Step 7: Test Agent Invocation

```bash
aws bedrock-agentcore invoke-agent-runtime \
  --runtime-arn "$(cd terraform && terraform output -raw runtime_arn)" \
  --runtime-session-id "test-session-1234567890123456789012345" \
  --payload '{"prompt": "What is 2+2?"}' \
  --region us-east-1
```

---

## File Structure

```
terraform/
├── main.tf                      # ECR + Runtime + Endpoint resources
├── variables.tf                 # Variable definitions
├── terraform.tfvars.example     # Example config (copy to terraform.tfvars)
├── README.md                     # This file
├── .gitignore                    # Ignore sensitive/generated files
└── terraform.state              # Generated (DO NOT COMMIT)

../                              # Project root
├── Dockerfile
├── server.py
├── agent_loop.py
├── mcp_client.py
├── saved_tests.py
├── requirements.txt
└── ...
```

---

## Configuration Reference

### Terraform Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `aws_region` | `us-east-1` | AWS region |
| `environment` | `dev` | dev/staging/prod |
| `ecr_repository_name` | `prompt2test-agent` | ECR repo name |
| `image_tag` | `latest` | Docker image tag |
| `bedrock_model` | `anthropic.claude-3-5-sonnet-20241022-v2:0` | Claude model |
| `allowed_origins` | `*` | CORS allowed origins |
| `container_memory_mb` | `1024` | Memory (MB) |
| `container_cpu` | `512` | CPU (units) |

### Resources Created

| Resource | Name | Purpose |
|----------|------|---------|
| ECR Repository | `prompt2test-agent` | Docker image storage |
| IAM Role | `prompt2test-agentcore-runtime-role` | Runtime execution role |
| Memory Store | `prompt2test-memory-store` | Test persistence |
| Browser Runtime | `prompt2test-browser` | Browser automation |
| AgentCore Runtime | `prompt2test-agentcore` | Main runtime |
| Endpoint | `DEFAULT` | Service entry point |

---

## Common Tasks

### View All Outputs

```bash
terraform output
```

### View Specific Output

```bash
terraform output ecr_repository_url
terraform output runtime_arn
```

### Update Configuration

Edit `terraform.tfvars`, then:

```bash
terraform plan
terraform apply
```

### Destroy Everything

```bash
terraform destroy
```

⚠️ Type **yes** to confirm — all resources will be deleted!

---

## Troubleshooting

### Docker Build Fails with "exec format error"

**Cause:** BuildX builder doesn't support cross-compilation

**Solution:**
```bash
docker buildx create --name builder --driver docker-container --use
docker buildx build --platform linux/arm64 -t ... --push .
```

### ECR Login Failed

**Error:** `docker login ... denied`

**Solution:** Re-authenticate:
```bash
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin $(terraform output -raw ecr_repository_url)
```

### Runtime Shows "Initializing"

**Expected behavior** — Takes 2-5 minutes for container to start

Wait and retry:
```bash
sleep 60
aws bedrock-agentcore describe-runtime --runtime-arn "..." --region us-east-1
```

### Terraform State Locked

**Error:** `Error acquiring the lock`

**Solution:**
```bash
terraform force-unlock LOCK_ID
```

---

## Security Best Practices

1. **Don't commit `terraform.tfvars`**
   ```bash
   echo "terraform.tfvars" >> ../.gitignore
   ```

2. **Use Remote State** (for team environments)
   ```hcl
   terraform {
     backend "s3" {
       bucket = "my-terraform-state"
       key    = "prompt2test/terraform.tfstate"
     }
   }
   ```

3. **Limit CORS Origins** (production)
   ```bash
   terraform apply -var="allowed_origins=https://myapp.com"
   ```

4. **Use Immutable Image Tags** (production)
   ```bash
   terraform apply -var="image_tag=v1.0.0"
   ```

---

## Next Steps

1. ✅ Deploy infrastructure with Terraform
2. ✅ Build & push Docker image to ECR
3. ✅ Test agent invocation
4. ✅ Monitor CloudWatch logs
5. ✅ Build UI frontend (optional)
6. ✅ Integrate with CI/CD pipeline

---

## Example: Automated CI/CD Pipeline

Create `.github/workflows/deploy.yml`:

```yaml
name: Deploy AgentCore

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Configure AWS
        uses: aws-actions/configure-aws-credentials@v2
        with:
          aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region: us-east-1

      - name: Build & Push Docker Image
        run: |
          cd terraform
          ECR_REPO=$(terraform output -raw ecr_repository_url)
          aws ecr get-login-password --region us-east-1 | \
            docker login --username AWS --password-stdin $ECR_REPO
          docker buildx build --platform linux/arm64 \
            -t $ECR_REPO:latest -t $ECR_REPO:$GITHUB_SHA \
            --push ..
```

---

## Support

For issues:
- Check **AWS Console**: Bedrock → AgentCore → Runtimes
- View **CloudWatch logs**
- Run `terraform plan` to diagnose
- Check `terraform.state` for actual resources
