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
          Service = [
            "bedrock.amazonaws.com",
            "bedrock-agentcore.amazonaws.com"
          ]
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

# ── IAM Policy for ECR Access (Pull Docker Images) ────────────────────────────
resource "aws_iam_role_policy" "agentcore_ecr_policy" {
  name   = "prompt2test-ecr-policy"
  role   = aws_iam_role.agentcore_runtime_role.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken",
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
          "ecr:DescribeImages"
        ]
        Resource = "*"
      }
    ]
  })
}

# ── AgentCore resources are created via AWS CLI (see AGENTCORE_SETUP.md) ────
# Terraform AWS provider doesn't yet support bedrock-agentcore resources
# You'll create Memory Store, Browser Runtime, Runtime, and Endpoint manually
# using the AWS CLI commands in AGENTCORE_SETUP.md

# ── Outputs ───────────────────────────────────────────────────────────────────
output "ecr_repository_url" {
  description = "ECR Repository URL"
  value       = aws_ecr_repository.agent.repository_url
}

output "ecr_registry_id" {
  description = "ECR Registry ID (AWS Account ID)"
  value       = aws_ecr_repository.agent.registry_id
}

output "ecr_login_command" {
  description = "Command to authenticate with ECR"
  value       = "aws ecr get-login-password --region ${var.aws_region} | docker login --username AWS --password-stdin ${aws_ecr_repository.agent.repository_url}"
}

output "docker_push_command" {
  description = "Command to build and push Docker image to ECR (run from project root)"
  value = <<-EOT
docker buildx build --platform linux/arm64 \
  -t ${aws_ecr_repository.agent.repository_url}:${var.image_tag} \
  --push .
  EOT
}

output "iam_role_arn" {
  description = "IAM Role ARN (use for AgentCore runtime creation)"
  value       = aws_iam_role.agentcore_runtime_role.arn
}

output "next_step" {
  description = "Next steps after Terraform"
  value       = "See AGENTCORE_SETUP.md for AWS CLI commands to create Memory Store, Browser Runtime, Runtime, and Endpoint"
}
