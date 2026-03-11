# ──────────────────────────────────────────────────────────────────────────────
# Prompt2TestAgentCore - Terraform Deployment
# Deploys ECR repository, Docker image, and Bedrock AgentCore infrastructure
# ──────────────────────────────────────────────────────────────────────────────

terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# ── ECR Repository ────────────────────────────────────────────────────────────
resource "aws_ecr_repository" "agent" {
  name                 = var.ecr_repository_name
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = {
    Environment = var.environment
    Project     = "Prompt2TestAgentCore"
  }
}

# ── ECR Lifecycle Policy (Keep only latest 5 images) ────────────────────────
resource "aws_ecr_lifecycle_policy" "agent" {
  repository = aws_ecr_repository.agent.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep last 5 images"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["v", "latest"]
          countType     = "imageCountMoreThan"
          countNumber   = 5
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}

# ── IAM Role for AgentCore Runtime ────────────────────────────────────────────
resource "aws_iam_role" "agentcore_runtime_role" {
  name = "prompt2test-agentcore-runtime-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "bedrock.amazonaws.com"
        }
      }
    ]
  })
}

# ── IAM Policy for Bedrock Runtime (Bedrock LLM + Memory Access) ──────────────
resource "aws_iam_role_policy" "agentcore_bedrock_policy" {
  name   = "prompt2test-bedrock-policy"
  role   = aws_iam_role.agentcore_runtime_role.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream"
        ]
        Resource = "arn:aws:bedrock:${var.aws_region}::model/anthropic.claude-3-5-sonnet-20241022-v2:0"
      },
      {
        Effect = "Allow"
        Action = [
          "bedrock-agentcore:GetMemory",
          "bedrock-agentcore:PutMemory",
          "bedrock-agentcore:DeleteMemory"
        ]
        Resource = "*"
      }
    ]
  })
}

# ── AgentCore Memory Store ────────────────────────────────────────────────────
resource "aws_bedrock_agentcore_memory" "test_memory_store" {
  name        = "prompt2test-memory-store"
  description = "Memory store for Prompt2TestAgentCore saved tests and history"
}

# ── AgentCore Browser Runtime ─────────────────────────────────────────────────
resource "aws_bedrock_agentcore_browser_runtime" "test_browser" {
  name        = "prompt2test-browser"
  description = "Browser runtime for Prompt2TestAgentCore automation"
}

# ── AgentCore Runtime ─────────────────────────────────────────────────────────
resource "aws_bedrock_agentcore_runtime" "test_runtime" {
  name                  = "prompt2test-agentcore"
  description           = "Prompt2TestAgentCore - AI-driven test automation agent"
  role_arn              = aws_iam_role.agentcore_runtime_role.arn
  memory_store_id       = aws_bedrock_agentcore_memory.test_memory_store.id
  browser_runtime_id    = aws_bedrock_agentcore_browser_runtime.test_browser.id

  tags = {
    Environment = var.environment
    Project     = "Prompt2TestAgentCore"
  }
}

# ── Runtime Endpoint / Deployment ────────────────────────────────────────────
resource "aws_bedrock_agentcore_endpoint" "test_endpoint" {
  runtime_arn = aws_bedrock_agentcore_runtime.test_runtime.arn
  name        = "DEFAULT"

  # Use the ECR repository URI
  container_image = "${aws_ecr_repository.agent.repository_url}:${var.image_tag}"

  # Port must match Dockerfile EXPOSE
  port = 8000

  # Environment Variables
  environment_variables = {
    AWS_REGION                    = var.aws_region
    BEDROCK_MODEL                 = var.bedrock_model
    AGENTCORE_BROWSER_ENDPOINT    = "https://${aws_bedrock_agentcore_browser_runtime.test_browser.id}.browser.bedrock-agentcore.${var.aws_region}.amazonaws.com/mcp"
    AGENTCORE_MEMORY_STORE_ID     = aws_bedrock_agentcore_memory.test_memory_store.id
    ALLOWED_ORIGINS               = var.allowed_origins
    OTEL_SERVICE_NAME             = "prompt2test-agent"
  }

  # Container resource configuration
  memory            = var.container_memory_mb
  cpu               = var.container_cpu

  tags = {
    Environment = var.environment
    Project     = "Prompt2TestAgentCore"
  }
}

# ── Outputs ───────────────────────────────────────────────────────────────────
output "ecr_repository_url" {
  description = "ECR Repository URL"
  value       = aws_ecr_repository.agent.repository_url
}

output "ecr_registry_id" {
  description = "ECR Registry ID (AWS Account ID)"
  value       = aws_ecr_repository.agent.registry_id
}

output "runtime_arn" {
  description = "AgentCore Runtime ARN"
  value       = aws_bedrock_agentcore_runtime.test_runtime.arn
}

output "endpoint_arn" {
  description = "Runtime Endpoint ARN"
  value       = aws_bedrock_agentcore_endpoint.test_endpoint.arn
}

output "browser_endpoint" {
  description = "AgentCore Browser endpoint URL"
  value       = "https://${aws_bedrock_agentcore_browser_runtime.test_browser.id}.browser.bedrock-agentcore.${var.aws_region}.amazonaws.com/mcp"
}

output "memory_store_id" {
  description = "AgentCore Memory Store ID"
  value       = aws_bedrock_agentcore_memory.test_memory_store.id
}

output "docker_push_command" {
  description = "Command to push Docker image to ECR"
  value = <<-EOT
    # 1. Authenticate with ECR
    aws ecr get-login-password --region ${var.aws_region} | \
      docker login --username AWS --password-stdin ${aws_ecr_repository.agent.repository_url}

    # 2. Build image for arm64
    docker buildx build --platform linux/arm64 \
      -t ${aws_ecr_repository.agent.repository_url}:${var.image_tag} \
      --push .
  EOT
}

output "invocation_command" {
  description = "Example command to invoke the agent"
  value = <<-EOT
    aws bedrock-agentcore invoke-agent-runtime \
      --runtime-arn '${aws_bedrock_agentcore_runtime.test_runtime.arn}' \
      --runtime-session-id 'test-session-1234567890123456789012345' \
      --payload '{"prompt": "Your test task here"}' \
      --region ${var.aws_region}
  EOT
}
