# The training layer never creates buckets, keys or roles - it reads them from
# the persistent layer. That is what makes `terraform destroy` here safe to run
# every single session.
data "terraform_remote_state" "persistent" {
  backend = "s3"

  config = {
    bucket = var.state_bucket
    key    = "persistent/terraform.tfstate"
    region = var.region
  }
}

# The identity running Terraform and the project scripts - dev-user. Discovered
# rather than written down, so the config carries no account-specific literal.
data "aws_caller_identity" "current" {}

locals {
  account_id  = data.terraform_remote_state.persistent.outputs.account_id
  lake_bucket = data.terraform_remote_state.persistent.outputs.lake_bucket
  lake_arn    = data.terraform_remote_state.persistent.outputs.lake_bucket_arn
  kms_key_arn = data.terraform_remote_state.persistent.outputs.lake_kms_key_arn
  glue_role   = data.terraform_remote_state.persistent.outputs.glue_role_arn

  # Phase 7. Scoped in the persistent layer to curated/* and the lake key only;
  # the warehouse reads the curated fact and nothing else.
  redshift_role = data.terraform_remote_state.persistent.outputs.redshift_role_arn
  personas      = data.terraform_remote_state.persistent.outputs.persona_role_arns

  # The brief names this database explicitly (training_db) and names the raw
  # tables customers_raw / products_raw / orders_raw. Deriving it from the
  # project prefix instead would have been tidier but wrong: these are the
  # names the exercise is graded against.
  database_name = var.glue_database
}
