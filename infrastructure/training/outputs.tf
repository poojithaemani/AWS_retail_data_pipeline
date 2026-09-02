output "database_name" {
  description = "Glue Data Catalog database."
  value       = aws_glue_catalog_database.training.name
}

output "athena_workgroup" {
  description = "Athena workgroup name."
  value       = aws_athena_workgroup.training.name
}

output "lake_bucket" {
  description = "Data lake bucket (owned by the persistent layer)."
  value       = local.lake_bucket
}

output "glue_log_group" {
  description = "CloudWatch log group for Glue jobs."
  value       = aws_cloudwatch_log_group.glue.name
}

output "crawler_name" {
  description = "Run with: aws glue start-crawler --name <this>"
  value       = aws_glue_crawler.raw.name
}

output "glue_job_name" {
  description = "Run with: ./scripts/de.sh runjob"
  value       = aws_glue_job.curated_sales.name
}
