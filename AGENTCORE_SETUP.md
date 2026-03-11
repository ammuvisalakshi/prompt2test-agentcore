# AgentCore Setup - AWS CLI Commands

After Terraform creates ECR + IAM, use these AWS CLI commands to set up the remaining AgentCore resources.

## Prerequisites
- ✅ Terraform has completed successfully
- ✅ You have outputs from `terraform apply` (ECR URL, IAM Role ARN)
- ✅ You're running commands in CloudShell

---

## Step 1: Set Environment Variables

```bash
cd ~/tmp/prompt2test-agentcore/terraform

# Get values from Terraform outputs
export AWS_REGION=$(terraform output -raw aws_region)
export ECR_URL=$(terraform output -raw ecr_repository_url)
export ROLE_ARN=$(terraform output -raw iam_role_arn)
export IMAGE_TAG="latest"

# Verify they're set
echo "Region: $AWS_REGION"
echo "ECR: $ECR_URL"
echo "Role: $ROLE_ARN"
```

---

## Step 2: Create Memory Store

```bash
aws bedrock-agentcore create-memory-store \
  --name prompt2test-memory-store \
  --description "Memory store for Prompt2TestAgentCore saved tests" \
  --region $AWS_REGION

# Wait for response and save the memory store ID
MEMORY_STORE_ID=$(aws bedrock-agentcore list-memory-stores \
  --region $AWS_REGION \
  --query "memoryStores[?name=='prompt2test-memory-store'].id" \
  --output text)

echo "Memory Store ID: $MEMORY_STORE_ID"
```

---

## Step 3: Create Browser Runtime

```bash
aws bedrock-agentcore create-browser-runtime \
  --name prompt2test-browser \
  --description "Browser runtime for Prompt2TestAgentCore automation" \
  --region $AWS_REGION

# Wait for response and save the browser runtime ID
BROWSER_RUNTIME_ID=$(aws bedrock-agentcore list-browser-runtimes \
  --region $AWS_REGION \
  --query "browserRuntimes[?name=='prompt2test-browser'].id" \
  --output text)

echo "Browser Runtime ID: $BROWSER_RUNTIME_ID"
```

---

## Step 4: Create AgentCore Runtime

```bash
aws bedrock-agentcore create-runtime \
  --name prompt2test-agentcore \
  --description "Prompt2TestAgentCore - AI-driven test automation agent" \
  --role-arn $ROLE_ARN \
  --memory-store-id $MEMORY_STORE_ID \
  --browser-runtime-id $BROWSER_RUNTIME_ID \
  --region $AWS_REGION

# Wait for response and save the runtime ARN
RUNTIME_ARN=$(aws bedrock-agentcore list-runtimes \
  --region $AWS_REGION \
  --query "runtimes[?name=='prompt2test-agentcore'].arn" \
  --output text)

echo "Runtime ARN: $RUNTIME_ARN"
```

---

## Step 5: Create Runtime Endpoint

```bash
aws bedrock-agentcore create-endpoint \
  --runtime-arn $RUNTIME_ARN \
  --name DEFAULT \
  --container-image "$ECR_URL:$IMAGE_TAG" \
  --port 8000 \
  --environment-variables \
    "AWS_REGION=$AWS_REGION" \
    "BEDROCK_MODEL=anthropic.claude-3-5-sonnet-20241022-v2:0" \
    "AGENTCORE_BROWSER_ENDPOINT=https://$BROWSER_RUNTIME_ID.browser.bedrock-agentcore.$AWS_REGION.amazonaws.com/mcp" \
    "AGENTCORE_MEMORY_STORE_ID=$MEMORY_STORE_ID" \
    "ALLOWED_ORIGINS=*" \
    "OTEL_SERVICE_NAME=prompt2test-agent" \
  --memory 1024 \
  --cpu 512 \
  --region $AWS_REGION
```

---

## Step 6: Build & Push Docker Image to ECR

```bash
# Navigate to project root
cd ~/tmp/prompt2test-agentcore

# Authenticate with ECR
aws ecr get-login-password --region $AWS_REGION | \
  docker login --username AWS --password-stdin $ECR_URL

# Create buildx builder (if not exists)
docker buildx create --name builder --driver docker-container --use 2>/dev/null || true

# Build and push image
docker buildx build --platform linux/arm64 \
  -t $ECR_URL:$IMAGE_TAG \
  --push .

# Wait 5-10 minutes for push to complete
echo "Docker image pushed! Waiting for runtime to start..."
```

---

## Step 7: Wait for Runtime to Start

```bash
# Wait 2-3 minutes for container to pull and start
sleep 120

# Check runtime status
aws bedrock-agentcore describe-runtime \
  --runtime-arn $RUNTIME_ARN \
  --region $AWS_REGION

# Look for: "Status": "Active" ✅
```

---

## Step 8: Test Agent Invocation

```bash
aws bedrock-agentcore invoke-agent-runtime \
  --runtime-arn "$RUNTIME_ARN" \
  --runtime-session-id "test-session-1234567890123456789012345" \
  --payload '{"prompt": "What is 2+2?"}' \
  --region $AWS_REGION
```

If successful, you'll see a response from Claude! 🎉

---

## Troubleshooting

### Command: aws bedrock-agentcore list-memory-stores

if this fails with "Unknown service", bedrock-agentcore might not be available in your region.

**Solution:** Try another region:
```bash
export AWS_REGION="us-west-2"  # or us-east-1, eu-west-1
```

### "Container image not found" during endpoint creation

**Solution:** Wait a bit longer for ECR to finish processing, then try again:
```bash
sleep 30
# Re-run the create-endpoint command
```

### Runtime stuck in "Initializing"

**Solution:** Wait 3-5 minutes, then check CloudWatch:
```bash
aws logs describe-log-groups --region $AWS_REGION | grep prompt2test
```

---

## Save These Commands

After deployment, export these for future reference:

```bash
# Save to file for later use
cat > ~/prompt2test-env.sh << 'EOF'
export AWS_REGION="us-east-1"
export ECR_URL="YOUR_ECR_URL"
export RUNTIME_ARN="YOUR_RUNTIME_ARN"
export MEMORY_STORE_ID="YOUR_MEMORY_STORE_ID"
EOF

source ~/prompt2test-env.sh
```

---

## Next: Invoke the Agent

Once `describe-runtime` shows `"Status": "Active"`, you can invoke it:

```bash
aws bedrock-agentcore invoke-agent-runtime \
  --runtime-arn "$RUNTIME_ARN" \
  --runtime-session-id "test-session-$(date +%s)" \
  --payload '{"prompt": "Your task here"}' \
  --region $AWS_REGION
```

---

## Cleanup (if needed)

```bash
# Delete endpoint
aws bedrock-agentcore delete-endpoint \
  --endpoint-arn $ENDPOINT_ARN \
  --region $AWS_REGION

# Delete runtime
aws bedrock-agentcore delete-runtime \
  --runtime-arn $RUNTIME_ARN \
  --region $AWS_REGION

# Delete with Terraform
cd terraform && terraform destroy
```
