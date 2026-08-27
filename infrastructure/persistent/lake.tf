# ---------------------------------------------------------------------------
# The data lake bucket.
#
# Deliberately in the PERSISTENT layer, not the training layer. Rationale:
# S3 is storage, not a running service - an idle bucket holding a few hundred
# MB costs about a cent a month, while the multi-day raw history it holds is
# what makes the incremental-processing exercise meaningful. Every compute,
# catalog, warehouse and orchestration resource is still destroyed nightly.
#
# `de.sh nuke` empties and removes it when a genuinely clean slate is wanted.
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "lake" {
  bucket = local.lake_bucket
  tags   = { Name = local.lake_bucket }
}

resource "aws_s3_bucket_versioning" "lake" {
  bucket = aws_s3_bucket.lake.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id

  rule {
    apply_server_side_encryption_by_default {
      kms_master_key_id = aws_kms_key.lake.arn
      sse_algorithm     = "aws:kms"
    }

    # S3 Bucket Keys cut KMS request charges by up to 99% on high-object-count
    # prefixes. Cheap, and a concrete cost-optimisation answer.
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "lake" {
  bucket                  = aws_s3_bucket.lake.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id

  # Data expiry is the cost guard for the lake. Everything here is
  # regenerable from src/generate with a fixed seed.
  rule {
    id     = "expire-training-data"
    status = "Enabled"

    filter {}

    expiration {
      days = var.lake_expiration_days
    }

    noncurrent_version_expiration {
      noncurrent_days = 1
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  # Athena query results are disposable the moment they are downloaded.
  rule {
    id     = "expire-athena-results"
    status = "Enabled"

    filter {
      prefix = "athena-results/"
    }

    expiration {
      days = 1
    }
  }
}

# Force TLS. Cheap control, and the first thing a security review asks for.
data "aws_iam_policy_document" "lake" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]

    resources = [
      aws_s3_bucket.lake.arn,
      "${aws_s3_bucket.lake.arn}/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "lake" {
  bucket = aws_s3_bucket.lake.id
  policy = data.aws_iam_policy_document.lake.json
}
