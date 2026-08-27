# Customer-managed key for the data lake. Owned by the persistent layer so the
# key (and therefore the ability to read yesterday's data) survives teardown.
resource "aws_kms_key" "lake" {
  description             = "Encryption key for the retail data lake (customers, products, orders)"
  deletion_window_in_days = 7
  enable_key_rotation     = true

  tags = { Name = "${var.project}-lake" }
}

resource "aws_kms_alias" "lake" {
  name          = "alias/${var.project}-lake"
  target_key_id = aws_kms_key.lake.key_id
}

# Allows Glue, Athena, Redshift and CloudWatch Logs in this account to use the
# key without each service role needing an inline key grant.
data "aws_iam_policy_document" "lake_key" {
  statement {
    sid       = "AccountRootFullControl"
    effect    = "Allow"
    actions   = ["kms:*"]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${local.account_id}:root"]
    }
  }

  statement {
    sid    = "AllowAnalyticsServices"
    effect = "Allow"

    actions = [
      "kms:Encrypt",
      "kms:Decrypt",
      "kms:ReEncrypt*",
      "kms:GenerateDataKey*",
      "kms:DescribeKey",
    ]

    resources = ["*"]

    principals {
      type = "Service"
      identifiers = [
        "glue.amazonaws.com",
        "athena.amazonaws.com",
        "redshift.amazonaws.com",
        "logs.${var.region}.amazonaws.com",
      ]
    }
  }
}

resource "aws_kms_key_policy" "lake" {
  key_id = aws_kms_key.lake.id
  policy = data.aws_iam_policy_document.lake_key.json
}
