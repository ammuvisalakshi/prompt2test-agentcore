# ──────────────────────────────────────────────────────────────────────────────
# Prompt2TestAgentCore - Terraform Variables
# ──────────────────────────────────────────────────────────────────────────────

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
  default     = "dev"
}

variable "ecr_repository_name" {
  description = "ECR repository name"
  type        = string
  default     = "prompt2test-agent"
}

variable "image_tag" {
  description = "Docker image tag"
  type        = string
  default     = "latest"
}

variable "bedrock_model" {
  description = "Bedrock model ID to use"
  type        = string
  default     = "anthropic.claude-3-5-sonnet-20241022-v2:0"
}

variable "allowed_origins" {
  description = "Comma-separated list of allowed CORS origins"
  type        = string
  default     = "*"
}

variable "container_memory_mb" {
  description = "Container memory in MB"
  type        = number
  default     = 1024
}

variable "container_cpu" {
  description = "Container CPU units (256, 512, 1024, 2048, 4096)"
  type        = number
  default     = 512
}
