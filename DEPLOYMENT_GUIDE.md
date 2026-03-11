# Prompt2TestAgentCore - Deployment Guide

Complete guide to deploy the Prompt2TestAgentCore agent to AWS AgentCore.

---

## Prerequisites

- AWS account with Bedrock access
- AWS IAM user with ECR permissions
- AWS CloudShell access with Docker and buildx support
- **IMPORTANT:** Image MUST be built for **arm64** architecture (required by AgentCore)

---

## Part 1: Create & Push Docker Image to ECR

### Step 1: Create ECR Repository (One-time)

```bash
aws ecr create-repository \
  --repository-name prompt2test-agent \
  --region us-east-1
```

**Output:** You'll see the repository URI
```
590183962483.dkr.ecr.us-east-1.amazonaws.com/prompt2test-agent
```

### Step 2: Open AWS CloudShell

1. Go to **AWS Console** (top right)
2. Click **CloudShell** icon (terminal icon)
3. A terminal opens in your browser

### Step 3: Clone Your Git Repository

In CloudShell:

```bash
cd /tmp
git clone https://github.com/YOUR-GITHUB-USERNAME/Prompt2TestAgentCore.git
cd Prompt2TestAgentCore
```

Or with authentication (if private repo):
```bash
git clone https://github.com/YOUR-GITHUB-USERNAME/Prompt2TestAgentCore.git --depth 1
cd Prompt2TestAgentCore
```

### Step 4: Verify Files Are There

```bash
ls -la
```

You should see:
```
Dockerfile
server.py
agent_loop.py
mcp_client.py
saved_tests.py
requirements.txt
DEPLOYMENT_GUIDE.md
.env.example
```

### Step 5: Authenticate with ECR

```bash
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin 590183962483.dkr.ecr.us-east-1.amazonaws.com
```

Expected output:
```
Login Succeeded
```

### Step 6: Create BuildX Builder for arm64

**IMPORTANT:** AgentCore requires **arm64** architecture. Create a builder with cross-compilation support:

```bash
# Create new builder with docker-container driver
docker buildx create --name builder --driver docker-container --use

# Verify it's created
docker buildx ls
```

Expected output shows builder with platforms: `linux/amd64,linux/arm64`

### Step 7: Build for arm64 and Push to ECR

```bash
docker buildx build --platform linux/arm64 \
  -t 590183962483.dkr.ecr.us-east-1.amazonaws.com/prompt2test-agent:latest \
  --push .
```

**Expected time:** 8-10 minutes (cross-compilation takes longer)

Watch for build progress. Final messages should show:
```
=> pushing layers
=> pushing manifest sha256:xxx
```

### Step 8: Verify Image in ECR

1. Go to **AWS Console** → **Elastic Container Registry**
2. Click **Repositories** in left sidebar
3. Click **prompt2test-agent**
4. You should see image with tag **latest** ✅

**Image URI to use next:**
```
590183962483.dkr.ecr.us-east-1.amazonaws.com/prompt2test-agent:latest
```

---

## Part 2: Deploy to AgentCore Runtime

### Step 1: Provision AgentCore Resources (if not already done)

#### 1a. Create AgentCore Browser Runtime

1. Go to **AWS Console** → **Bedrock** → **AgentCore** → **Browser**
2. Click **Create runtime**
3. Wait for status to be **Active**
4. Copy the browser endpoint:
   ```
   https://<runtime-id>.browser.bedrock-agentcore.us-east-1.amazonaws.com/mcp
   ```
   **Save this** → use as `AGENTCORE_BROWSER_ENDPOINT`

#### 1b. Create AgentCore Memory Store

1. Go to **AWS Console** → **Bedrock** → **AgentCore** → **Memory**
2. Click **Create memory store**
3. Wait for creation to complete
4. Copy the Memory Store ID
   ```
   store-abc123xyz
   ```
   **Save this** → use as `AGENTCORE_MEMORY_STORE_ID`

### Step 2: Create AgentCore Runtime

1. Go to **AWS Console** → **Bedrock** → **AgentCore** → **Runtimes**
2. Click **Create runtime**
3. Enter name: `prompt2test-runtime` (or your choice)
4. Click **Create**

### Step 3: Add Deployment

1. Click on your newly created runtime
2. Click **Add deployment**
3. Fill in the following details:

#### Deployment Configuration

**Container Image:**
```
590183962483.dkr.ecr.us-east-1.amazonaws.com/prompt2test-agent:latest
```

**Port:**
```
8000
```

**Environment Variables:**
```
AWS_REGION=us-east-1
BEDROCK_MODEL=anthropic.claude-3-5-sonnet-20241022-v2:0
AGENTCORE_BROWSER_ENDPOINT=https://<your-runtime-id>.browser.bedrock-agentcore.us-east-1.amazonaws.com/mcp
AGENTCORE_MEMORY_STORE_ID=<your-memory-store-id>
ALLOWED_ORIGINS=*
OTEL_SERVICE_NAME=prompt2test-agent
```

Replace:
- `<your-runtime-id>` with your AgentCore Browser runtime ID
- `<your-memory-store-id>` with your AgentCore Memory Store ID

### Step 4: Deploy

1. Click **Deploy**
2. Wait for status to show **Active** (usually 2-5 minutes)
3. Monitor logs for any errors

### Step 5: Test Health Endpoint

Once deployed, AgentCore will provide an endpoint URL. Test it:

```bash
curl https://<agentcore-endpoint>/api/health
```

Expected response:
```json
{"status": "ok"}
```

---

## Deployment Checklist

- [ ] ECR repository created
- [ ] Git repository cloned to CloudShell
- [ ] BuildX builder created with docker-container driver
- [ ] Docker image built for **arm64** architecture
- [ ] Docker image pushed to ECR
- [ ] Image verified in ECR console (shows arm64 platform)
- [ ] AgentCore Browser runtime created (endpoint saved)
- [ ] AgentCore Memory store created (ID saved)
- [ ] AgentCore Runtime created
- [ ] Deployment configured with correct env vars
- [ ] Deployment status shows **Active**
- [ ] Health endpoint responds with `{"status": "ok"}`

---

## Part 3: Using Your Agent (Invoke AgentCore Runtime)

Once your agent is deployed and **Active**, you can invoke it using the AWS SDK.

### Method: Using Python boto3 SDK

**Complete working example:**

```python
import boto3
import json

# Initialize client
client = boto3.client('bedrock-agentcore', region_name='us-east-1')

# Prepare your prompt/task
payload = json.dumps({
    "prompt": "Log in to the app and verify the dashboard loads"
})

# Invoke the agent
response = client.invoke_agent_runtime(
    agentRuntimeArn='arn:aws:bedrock-agentcore:us-east-1:590183962483:runtime/prompt2test_agentcore-AJiczt9BjU',
    runtimeSessionId='test-session-1234567890123456789012345',  # Must be 33+ characters
    payload=payload
    # qualifier='DEFAULT'  # Optional, uses DEFAULT endpoint by default
)

# Read response
response_body = response['response'].read()
response_data = json.loads(response_body)

print("Agent Response:")
print(json.dumps(response_data, indent=2))
```

### Parameters Explained

| Parameter | Description | Example |
|-----------|-------------|---------|
| `agentRuntimeArn` | Your runtime ARN (from AgentCore console) | `arn:aws:bedrock-agentcore:us-east-1:590183962483:runtime/...` |
| `runtimeSessionId` | Unique session ID (33+ characters) | `test-session-1234567890123456789012345` |
| `payload` | JSON string with your prompt/task | `{"prompt": "your task here"}` |
| `qualifier` | Endpoint qualifier (optional, defaults to DEFAULT) | `DEFAULT` |

### Important Notes

- **SessionId**: Each unique SessionId creates a new session/microVM. Use the same SessionId to continue a conversation.
- **Payload format**: Must be a JSON string, not a dict
- **Credentials**: Ensure your boto3 client has AWS credentials configured (IAM role in production, or `aws configure` locally)
- **Agent**: Connects to Bedrock LLM (Claude) + AgentCore Browser for automation

### Example Responses

**Success response:**
```json
{
  "sessionId": "test-session-1234567890123456789012345",
  "output": "Test completed successfully. Dashboard verified.",
  "status": "COMPLETE"
}
```

**Error response:**
```json
{
  "error": "InvalidSessionId",
  "message": "SessionId must be 33+ characters"
}
```

---

### Architecture Error on AgentCore Deployment

**Error:** `Architecture incompatible for uri '...'. Supported architectures: [arm64]`

**Cause:** Image was built for x86_64 instead of arm64

**Solution:** Rebuild the image for arm64:
```bash
# Create buildx builder with cross-compilation support
docker buildx create --name builder --driver docker-container --use

# Rebuild for arm64
docker buildx build --platform linux/arm64 \
  -t 590183962483.dkr.ecr.us-east-1.amazonaws.com/prompt2test-agent:latest \
  --push .
```

Then delete the old x86_64 image from ECR and retry deployment.

### Build Fails with "exec format error"

**Error:** `exec /bin/sh: exec format error` during build

**Cause:** BuildX default builder doesn't support cross-compilation to arm64

**Solution:** Create proper buildx builder:
```bash
docker buildx create --name builder --driver docker-container --use
docker buildx ls  # Verify it shows both linux/amd64 and linux/arm64
```

### Image Push Failed

**Error:** `docker push ... denied: User is not authorized`

**Solution:** Re-authenticate with ECR:
```bash
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin 590183962483.dkr.ecr.us-east-1.amazonaws.com
```

### Deployment Status is "Failed"

1. Click on the deployment
2. Check **Logs** tab for error messages
3. Common issues:
   - Missing environment variables
   - Invalid image URI
   - Port 8000 not exposed (already configured in Dockerfile)

### Health Check Fails

1. Wait 30+ seconds (health check has 30s startup period)
2. Check logs for Python/Bedrock connection errors
3. Verify environment variables are set correctly
4. Check AWS credentials in IAM Task Role

---

## API Endpoints

Once deployed, your agent exposes these endpoints:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | GET | Health check |
| `/api/run` | POST | Start new test run (streams results) |
| `/api/history` | GET | Get run history |
| `/api/saved` | GET | List saved tests |
| `/api/approve/{run_id}` | POST | Save test after approval |
| `/api/replay` | POST | Replay a saved test |

---

## Next Steps

1. **Start using the agent** via AgentCore API
2. **Build UI** (optional) - frontend can call these REST endpoints
3. **Monitor logs** - AgentCore console shows real-time logs
4. **Scale** - adjust memory/CPU as needed in AgentCore

---

## Support

For issues:
1. Check AgentCore Runtime logs
2. Verify environment variables
3. Check IAM permissions for Bedrock access
4. Ensure AGENTCORE_BROWSER_ENDPOINT and AGENTCORE_MEMORY_STORE_ID are correctly provisioned

