data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id

  # Globally unique, deterministic bucket names. Deterministic matters: the
  # training layer is destroyed and recreated constantly and must always find
  # the same lake without any manual wiring.
  state_bucket = "${var.project}-tfstate-${local.account_id}"

  # The brief targets s3://de-training/. Bucket names are globally unique, so
  # the account id is the smallest suffix that makes that name legal. Nothing
  # else is added on top of it.
  lake_bucket = "${var.project}-${local.account_id}"
}
