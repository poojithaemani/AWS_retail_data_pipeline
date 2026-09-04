# ---------------------------------------------------------------------------
# Lake Formation - account-level configuration. Phase 6.
#
# This file is in the persistent layer because everything in it is account
# scoped and survives teardown: who administers the lake, which S3 location it
# governs, and what the catalog does by default. Grants against individual
# tables live in the training layer, because the tables themselves are rebuilt
# every session.
#
# THE ORDER MATTERS MORE HERE THAN ANYWHERE ELSE IN THE PROJECT
# ------------------------------------------------------------
# Lake Formation changes how access is *decided*. Before registration, IAM
# alone answers "may this principal read this table". After registration, Lake
# Formation arbitrates, and a principal with flawless IAM policies is refused
# unless it also holds an LF grant.
#
# The admin assignment therefore comes first, and not as a formality. The
# account currently has NO data lake administrators - the default state. The
# principal that runs Terraform (dev-user) is also the principal that must
# create every grant below, and once a location is registered, ungranted
# principals lose access to it. Registering before assigning an admin is how
# an account ends up needing console intervention to recover.
# ---------------------------------------------------------------------------

resource "aws_lakeformation_data_lake_settings" "this" {
  # dev-user, the identity Terraform and every script in this repo runs as.
  # An LF administrator has implicit access to everything in the catalog,
  # which is what keeps scripts/publish_catalog.py working: it creates and
  # deletes tables, and would otherwise need explicit grants for both.
  admins = [data.aws_caller_identity.current.arn]

  # IAM_ALLOWED_PRINCIPALS is Lake Formation's backwards-compatibility hatch:
  # while it is present as a default, any principal whose IAM policy permits
  # an action is allowed, and LF grants are advisory rather than binding.
  #
  # It stays ON until the persona grants exist and the pipeline has been proven
  # to work under them. Removing it earlier would cut off the Glue role
  # mid-phase; removing it later is a deliberate, separately reviewed step.
  #
  # It is also what makes the negative test meaningful. With this in place,
  # MarketingAnalystRole can read `email` regardless of any column restriction,
  # because IAM permits it - so a denial test run now would pass access and
  # prove nothing. The column restriction only becomes real once this is gone.
  #
  # NOTE: clearing these defaults affects only tables created afterwards.
  # Tables that already exist keep their own IAM_ALLOWED_PRINCIPALS grant and
  # must be revoked individually. See docs/learnings.md, Phase 6.
  dynamic "create_database_default_permissions" {
    for_each = var.lf_iam_allowed_principals ? [1] : []
    content {
      permissions = ["ALL"]
      principal   = "IAM_ALLOWED_PRINCIPALS"
    }
  }

  dynamic "create_table_default_permissions" {
    for_each = var.lf_iam_allowed_principals ? [1] : []
    content {
      permissions = ["ALL"]
      principal   = "IAM_ALLOWED_PRINCIPALS"
    }
  }
}

# Registering the bucket is what puts it under Lake Formation's control. Until
# this exists, LF grants on tables backed by it are recorded but not enforced.
#
# use_service_linked_role lets LF create and manage
# AWSServiceRoleForLakeFormationDataAccess itself. The alternative is nominating
# a role with S3 and KMS access to the lake, which would be a second thing to
# keep correct for no benefit here.
resource "aws_lakeformation_resource" "lake" {
  arn                     = aws_s3_bucket.lake.arn
  use_service_linked_role = true

  # The admin must exist before the location is governed - see the header.
  depends_on = [aws_lakeformation_data_lake_settings.this]
}
