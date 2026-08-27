# Dedicated Athena workgroup.
#
# The default "primary" workgroup is deliberately left untouched: it cannot be
# deleted, so anything configured there would survive teardown and pollute the
# next rebuild. A project workgroup is destroyed cleanly with everything else.
resource "aws_athena_workgroup" "training" {
  name          = "${var.project}-wg"
  description   = "Retail sales analytics: curated order facts over customer and product dimensions"
  force_destroy = true

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true

    # Hard stop on runaway scans. With Parquet plus partition pruning the
    # real queries in this project scan single-digit MB, so a 1 GB ceiling
    # only ever fires when a query has gone wrong.
    bytes_scanned_cutoff_per_query = 1073741824

    result_configuration {
      output_location = "s3://${local.lake_bucket}/athena-results/"

      encryption_configuration {
        encryption_option = "SSE_KMS"
        kms_key_arn       = local.kms_key_arn
      }
    }
  }
}
