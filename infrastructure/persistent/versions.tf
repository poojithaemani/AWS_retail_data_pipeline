terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = var.project_tag
      Layer     = "persistent"
      ManagedBy = "terraform"
    }
  }
}

# Budgets and Cost Explorer are global services fronted by us-east-1.
provider "aws" {
  alias  = "billing"
  region = "us-east-1"

  default_tags {
    tags = {
      Project   = var.project_tag
      Layer     = "persistent"
      ManagedBy = "terraform"
    }
  }
}
