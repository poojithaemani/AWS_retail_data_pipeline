# ---------------------------------------------------------------------------
# Glue PySpark ETL job - Phase 3.
#
# Three resources and nothing else: the job, its entry script, and a zip of the
# transformation module. No trigger, no workflow, no schedule - orchestration
# is Phase 8, and adding it here would be architecture without a requirement.
#
# Everything else is reused: the lake bucket, the KMS key, training_db, and the
# Glue role whose permissions were scoped in Phase 1.
# ---------------------------------------------------------------------------

# The transformation module, zipped for --extra-py-files.
#
# Built by Terraform rather than by hand so the artifact in S3 always matches
# the code in the repository. A zip built manually is a zip that goes stale.
#
# source_dir is src/, NOT src/retail_pipeline/, and that distinction is the
# whole point. archive_file zips the *contents* of source_dir, so pointing it
# at the package directory produces a zip whose root is transforms.py with no
# enclosing package. Glue puts the zip itself on sys.path, so the entry script's
# `from retail_pipeline import transforms` would then fail with
# ModuleNotFoundError - at job startup, after the run had already been billed.
# Zipping from the parent keeps retail_pipeline/ inside the archive, so the
# import path is identical locally and on Glue.
data "archive_file" "retail_pipeline" {
  type        = "zip"
  source_dir  = "${path.module}/../../src"
  output_path = "${path.module}/.terraform/retail_pipeline.zip"

  # generate/ is local tooling - the dataset generator and its pandas
  # dependency - and has no place in the job's runtime. __pycache__ would make
  # the archive hash change on every local test run, producing a spurious diff.
  excludes = [
    "generate",
    "generate/__pycache__",
    "retail_pipeline/__pycache__",
  ]
}

resource "aws_s3_object" "job_package" {
  bucket = local.lake_bucket
  key    = "scripts/retail_pipeline.zip"
  source = data.archive_file.retail_pipeline.output_path

  # Re-uploads when the code changes, and only then.
  #
  # source_hash, not etag. The lake bucket is SSE-KMS, and S3 does not return
  # the plaintext MD5 as the ETag for a KMS-encrypted object - so `etag` can
  # never match the local hash and every future plan would show this object as
  # drifted, permanently. The provider documents source_hash for this case.
  source_hash = data.archive_file.retail_pipeline.output_md5

  tags = {
    Name    = "retail_pipeline.zip"
    Purpose = "Transformation module imported by the curated sales job"
  }
}

resource "aws_s3_object" "job_script" {
  bucket = local.lake_bucket
  key    = "scripts/curated_sales.py"
  source = "${path.module}/../../scripts/glue_jobs/curated_sales.py"

  # source_hash rather than etag, for the SSE-KMS reason above.
  source_hash = filemd5("${path.module}/../../scripts/glue_jobs/curated_sales.py")

  tags = {
    Name    = "curated_sales.py"
    Purpose = "Glue entry point: raw orders to curated sales"
  }
}

resource "aws_glue_job" "curated_sales" {
  name        = "${var.project}-curated-sales"
  role_arn    = local.glue_role
  description = "Retail ETL: dedupe, clean, validate, join, compute order_total, write curated Parquet"

  glue_version      = "5.0" # Spark 3.5 / Python 3.11
  worker_type       = "G.1X"
  number_of_workers = 2

  # No retries. A failure here is a data or logic problem, and retrying it
  # three times turns one confusing failure into three identical ones while
  # billing for each. Retry belongs in Phase 8's Step Functions, where a
  # *transient* failure can be distinguished from a deterministic one.
  max_retries = 0

  timeout = 30 # minutes - a runaway job is capped rather than open-ended

  command {
    name            = "glueetl"
    script_location = "s3://${local.lake_bucket}/${aws_s3_object.job_script.key}"
    python_version  = "3"
  }

  default_arguments = {
    "--extra-py-files" = "s3://${local.lake_bucket}/${aws_s3_object.job_package.key}"
    "--TempDir"        = "s3://${local.lake_bucket}/temp/"

    "--database"        = var.glue_database
    "--lake_bucket"     = local.lake_bucket
    "--curated_prefix"  = "curated/sales"
    "--rejected_prefix" = "quarantine/sales"

    # Bookmarks OFF for Phase 3, deliberately.
    #
    # With them on, the second run of a job processes zero rows - which during
    # development is indistinguishable from a broken job, and costs an hour of
    # confusion the first time it happens. Bookmarks are the Phase 4 exercise,
    # where "the re-run reprocessed 0 rows" is the intended result rather than
    # a surprise.
    "--job-bookmark-option" = "job-bookmark-disable"

    # Without this, spark.sql() resolves against Spark's own in-memory
    # catalog and never sees training_db - the first read fails with
    # TABLE_OR_VIEW_NOT_FOUND. The Data Catalog client jar is on the Glue
    # classpath either way; this flag is what installs it as the metastore.
    # Verified the hard way: the first run failed here after 46s.
    "--enable-glue-datacatalog" = "true"

    "--enable-metrics"                   = "true"
    "--enable-continuous-cloudwatch-log" = "true"
    "--enable-spark-ui"                  = "false" # writes event logs to S3; not needed here
  }

  tags = {
    Name    = "${var.project}-curated-sales"
    Purpose = "Retail order ETL to the curated layer"
  }
}
