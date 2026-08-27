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

  # Every resource in this layer is tagged with the phase that created it, so
  # `de.sh verify` and Cost Explorer can both attribute anything left behind.
  default_tags {
    tags = {
      Project   = var.project_tag
      Layer     = "training"
      Phase     = var.phase
      ManagedBy = "terraform"
    }
  }
}
