output "account_id" {
  description = "AWS account this project is deployed into."
  value       = local.account_id
}

output "region" {
  description = "Pinned region."
  value       = var.region
}

output "state_bucket" {
  description = "Terraform remote state bucket (never destroyed)."
  value       = local.state_bucket
}

output "lake_bucket" {
  description = "Data lake bucket name."
  value       = aws_s3_bucket.lake.id
}

output "lake_bucket_arn" {
  description = "Data lake bucket ARN."
  value       = aws_s3_bucket.lake.arn
}

output "lake_kms_key_arn" {
  description = "CMK protecting the lake."
  value       = aws_kms_key.lake.arn
}

output "glue_role_arn" {
  description = "Role assumed by Glue crawlers and jobs."
  value       = aws_iam_role.glue.arn
}

output "redshift_role_arn" {
  description = "Role attached to the Redshift Serverless namespace."
  value       = aws_iam_role.redshift.arn
}

output "persona_role_arns" {
  description = "Lake Formation persona roles, keyed by persona."
  value       = { for k, r in aws_iam_role.persona : k => r.arn }
}
