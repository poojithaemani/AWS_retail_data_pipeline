# ---------------------------------------------------------------------------
# Remote state.
#
# The state bucket itself is created by `scripts/de.sh bootstrap` using the
# AWS CLI, NOT by Terraform. Terraform cannot cleanly own the bucket its own
# state lives in - it is a bootstrap paradox, and the usual local-state-then-
# migrate workaround leaves a local state file lying around on every machine.
# One idempotent CLI call is simpler and safer.
#
# Locking uses S3 native conditional writes (use_lockfile), so there is no
# DynamoDB lock table to pay for or forget to delete.
#
# Configured at init time: scripts/de.sh passes -backend-config.
# ---------------------------------------------------------------------------

terraform {
  backend "s3" {
    key          = "persistent/terraform.tfstate"
    encrypt      = true
    use_lockfile = true
  }
}
