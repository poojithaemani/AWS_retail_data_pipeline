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

  # Lake Formation's data-access role, added in Phase 6 in response to an
  # actual denial rather than pre-emptively.
  #
  # Registering the bucket with Lake Formation changed *how* data is read, not
  # only who may read it. Athena no longer reaches S3 as the caller: Lake
  # Formation vends credentials through its service-linked role, and every
  # Athena data query failed with
  #
  #     PERMISSION_DENIED: User: .../AWSServiceRoleForLakeFormationDataAccess/...
  #     is not authorized to perform: kms:Decrypt on resource: <the lake CMK>
  #
  # The AllowAnalyticsServices statement above does not cover this, and that is
  # the part worth remembering: it authorises the *services*, but the request
  # now arrives as the service-linked ROLE, which is a different principal.
  # athena.amazonaws.com being listed there buys nothing on this path.
  #
  # Scoped to that single role - not the account, not a role-path wildcard, and
  # not kms:*. Decrypt is the action the observed failure named; GenerateDataKey
  # covers writes back to the encrypted location and DescribeKey the negotiation
  # S3 performs before either.
  statement {
    sid    = "AllowLakeFormationDataAccessRole"
    effect = "Allow"

    actions = [
      "kms:Decrypt",
      "kms:GenerateDataKey*",
      "kms:DescribeKey",
    ]

    resources = ["*"]

    principals {
      type = "AWS"
      identifiers = [
        "arn:aws:iam::${local.account_id}:role/aws-service-role/lakeformation.amazonaws.com/AWSServiceRoleForLakeFormationDataAccess",
      ]
    }
  }
}

resource "aws_kms_key_policy" "lake" {
  key_id = aws_kms_key.lake.id
  policy = data.aws_iam_policy_document.lake_key.json
}
