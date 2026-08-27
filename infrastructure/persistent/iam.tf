# ---------------------------------------------------------------------------
# IAM baseline.
#
# All long-lived principals live here so that Lake Formation grants (Phase 6)
# always reference stable role ARNs. The training layer only ever GRANTS
# permissions to these roles - it never creates them.
# ---------------------------------------------------------------------------

# --- Glue service role -----------------------------------------------------

data "aws_iam_policy_document" "glue_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["glue.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "glue" {
  # Name follows the brief's de-training prefix; description says what it does.
  name               = "${var.project}-glue-role"
  assume_role_policy = data.aws_iam_policy_document.glue_assume.json
  tags = {
    Name = "${var.project}-glue-role"
    # Slashes, not commas: IAM tag values permit only
    # [\p{L}\p{Z}\p{N}_.:/=+\-@] and a comma fails validation. S3 and KMS
    # accept commas, so this surfaces only on IAM resources.
    Purpose = "Retail order ETL: crawl / clean / deduplicate / join / curate"
  }
}

resource "aws_iam_role_policy_attachment" "glue_service" {
  role       = aws_iam_role.glue.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole"
}

data "aws_iam_policy_document" "lake_access" {
  statement {
    sid       = "ListLake"
    effect    = "Allow"
    actions   = ["s3:GetBucketLocation", "s3:ListBucket", "s3:ListBucketMultipartUploads"]
    resources = [aws_s3_bucket.lake.arn]
  }

  statement {
    sid    = "ReadWriteLakeObjects"
    effect = "Allow"

    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]

    resources = ["${aws_s3_bucket.lake.arn}/*"]
  }

  statement {
    sid    = "UseLakeKey"
    effect = "Allow"

    actions = [
      "kms:Encrypt",
      "kms:Decrypt",
      "kms:ReEncrypt*",
      "kms:GenerateDataKey*",
      "kms:DescribeKey",
    ]

    resources = [aws_kms_key.lake.arn]
  }

  statement {
    sid    = "Observability"
    effect = "Allow"

    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:AssociateKmsKey",
      "cloudwatch:PutMetricData",
    ]

    resources = ["*"]
  }
}

resource "aws_iam_policy" "lake_access" {
  name   = "${var.project}-lake-access"
  policy = data.aws_iam_policy_document.lake_access.json
}

resource "aws_iam_role_policy_attachment" "glue_lake" {
  role       = aws_iam_role.glue.name
  policy_arn = aws_iam_policy.lake_access.arn
}

# --- Redshift role (COPY from the lake, plus Spectrum against the catalog) ---

data "aws_iam_policy_document" "redshift_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["redshift.amazonaws.com", "redshift-serverless.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "redshift" {
  name               = "${var.project}-redshift-role"
  assume_role_policy = data.aws_iam_policy_document.redshift_assume.json
  tags = {
    Name    = "${var.project}-redshift-role"
    Purpose = "Load curated retail sales into the warehouse star schema"
  }
}

# Redshift is a sink in this architecture: it COPYs curated Parquet in and
# never writes back to the lake. Its permissions say exactly that.
#
# What this replaced, and why:
#
#   AWSGlueConsoleFullAccess - an AWS-managed policy carrying 49 actions across
#     13 services, including glue:* (so DeleteDatabase and DeleteTable) and
#     cloudformation:DeleteStack. It exists for a human clicking through the
#     Glue console, not for a service principal reading Parquet. A role whose
#     job is COPY could have deleted the catalog it reads from.
#
#   the shared lake_access policy - grants PutObject and DeleteObject across
#     the entire bucket plus kms:Encrypt. Correct for the Glue ETL role, which
#     writes processed and curated output; wrong for a warehouse that only
#     reads one prefix.

data "aws_iam_policy_document" "redshift_access" {
  statement {
    sid       = "ListCuratedPrefix"
    effect    = "Allow"
    actions   = ["s3:GetBucketLocation", "s3:ListBucket"]
    resources = [aws_s3_bucket.lake.arn]

    # Scopes listing to the one prefix Redshift loads from.
    #
    # NOTE for Phase 7: if COPY fails with AccessDenied on ListBucket, check
    # this condition first. Redshift lists the prefix it is given, so a COPY
    # from s3://<lake>/curated/... satisfies it - but a manifest file located
    # outside curated/, or a COPY issued against the bucket root, would not.
    # The fallback is to drop this condition block and leave ListBucket
    # unconditional: listing is metadata, not data access, and GetObject below
    # stays scoped either way.
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["curated", "curated/*"]
    }
  }

  statement {
    sid       = "ReadCuratedObjects"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.lake.arn}/curated/*"]
  }

  # Decrypt only. Redshift reads SSE-KMS objects; it never encrypts into the
  # lake, so Encrypt, ReEncrypt and GenerateDataKey are all absent.
  statement {
    sid       = "DecryptCuratedObjects"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:DescribeKey"]
    resources = [aws_kms_key.lake.arn]
  }

  # Spectrum resolves external tables through the Glue Data Catalog. Read-only,
  # and scoped to this project's database rather than every database in the
  # account.
  statement {
    sid    = "SpectrumCatalogRead"
    effect = "Allow"

    actions = [
      "glue:GetDatabase",
      "glue:GetDatabases",
      "glue:GetTable",
      "glue:GetTables",
      "glue:GetPartition",
      "glue:GetPartitions",
    ]

    resources = [
      "arn:aws:glue:${var.region}:${local.account_id}:catalog",
      "arn:aws:glue:${var.region}:${local.account_id}:database/${var.glue_database}",
      "arn:aws:glue:${var.region}:${local.account_id}:table/${var.glue_database}/*",
    ]
  }
}

resource "aws_iam_policy" "redshift_access" {
  name        = "${var.project}-redshift-access"
  description = "Read-only: curated retail Parquet, the lake key, and catalog metadata"
  policy      = data.aws_iam_policy_document.redshift_access.json
}

resource "aws_iam_role_policy_attachment" "redshift_access" {
  role       = aws_iam_role.redshift.name
  policy_arn = aws_iam_policy.redshift_access.arn
}

# --- Analyst / engineer personas -------------------------------------------
#
# IAM here grants only the ability to *use the query engines*. Which columns
# and tables each persona can actually read is decided by Lake Formation in
# Phase 6 - that separation is the whole point of the exercise.

data "aws_iam_policy_document" "persona_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${local.account_id}:root"]
    }
  }
}

data "aws_iam_policy_document" "persona_query" {
  statement {
    sid    = "AthenaQuery"
    effect = "Allow"

    actions = [
      "athena:StartQueryExecution",
      "athena:GetQueryExecution",
      "athena:GetQueryResults",
      "athena:GetWorkGroup",
      "athena:StopQueryExecution",
      "athena:ListWorkGroups",
      "athena:ListDataCatalogs",
      "athena:GetDataCatalog",
    ]

    resources = ["*"]
  }

  statement {
    sid       = "CatalogRead"
    effect    = "Allow"
    actions   = ["glue:GetDatabase*", "glue:GetTable*", "glue:GetPartition*"]
    resources = ["*"]
  }

  statement {
    sid       = "LakeFormationCredentialVending"
    effect    = "Allow"
    actions   = ["lakeformation:GetDataAccess"]
    resources = ["*"]
  }

  statement {
    sid       = "AthenaResults"
    effect    = "Allow"
    actions   = ["s3:GetBucketLocation", "s3:ListBucket"]
    resources = [aws_s3_bucket.lake.arn]
  }

  statement {
    sid       = "AthenaResultObjects"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.lake.arn}/athena-results/*"]
  }

  statement {
    sid       = "DecryptResults"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.lake.arn]
  }
}

resource "aws_iam_policy" "persona_query" {
  name   = "${var.project}-persona-query"
  policy = data.aws_iam_policy_document.persona_query.json
}

locals {
  personas = {
    data_engineer     = "DataEngineerRole"
    finance_analyst   = "FinanceAnalystRole"
    marketing_analyst = "MarketingAnalystRole"
  }
}

resource "aws_iam_role" "persona" {
  for_each = local.personas

  name               = each.value
  assume_role_policy = data.aws_iam_policy_document.persona_assume.json
  tags               = { Name = each.value, Persona = each.key }
}

resource "aws_iam_role_policy_attachment" "persona_query" {
  for_each = aws_iam_role.persona

  role       = each.value.name
  policy_arn = aws_iam_policy.persona_query.arn
}
