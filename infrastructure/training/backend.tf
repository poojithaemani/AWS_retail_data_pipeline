# Remote state for the ephemeral layer. Configured at init time by
# scripts/de.sh via -backend-config.
terraform {
  backend "s3" {
    key          = "training/terraform.tfstate"
    encrypt      = true
    use_lockfile = true
  }
}
