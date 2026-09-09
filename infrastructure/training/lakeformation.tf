# ---------------------------------------------------------------------------
# Lake Formation grants - Phase 6.
#
# Account-level configuration (admin, registration, defaults) is in the
# persistent layer. Everything here grants access to specific catalog objects,
# which are rebuilt every session, so the grants are rebuilt with them.
#
# TWO GATES, AND GATE 2 IS WHY
# ----------------------------
# Grants are validated against the object they name, so a grant on a table can
# only be created once that table exists. `training_db` is Terraform's own
# resource and exists at apply time; `customers_raw`, `orders_raw`,
# `products_raw` and `curated_sales` are produced later by the crawler,
# publish_catalog.py and the Phase 5 Athena DDL. That alone would justify
# gating only the table grants.
#
# The grants are split across two variables, and the split is evidence-driven
# rather than tidy:
#
#   var.lf_pipeline_grants_enabled  the Glue role's location, database and
#                                   table grants - what the PIPELINE needs to
#                                   run at all
#   var.lf_grants_enabled           the persona matrix, the column restriction
#                                   and the LF-Tag - the GOVERNANCE being
#                                   demonstrated
#
# These were briefly combined, on the assumption that IAM_ALLOWED_PRINCIPALS
# would carry the pipeline until the persona work was done and both sets could
# arrive together. Gate 2 disproved it. With the location registered, the
# fallback still enabled and no grants at all:
#
#   crawler   SUCCEEDED   reads S3 directly as the crawler role
#   publish   SUCCEEDED   catalog API calls as dev-user, an LF admin
#   Athena    FAILED      LF-vended read, kms:Decrypt refused for the SLR
#   Glue ETL  FAILED      LFCredential fetch failed with status code: 400
#
# The Glue failure is the informative one. It asks Lake Formation for
# credentials to read orders_raw with SELECT and is refused before the
# filesystem initialises, because the role holds no LF grant on that table.
# IAM_ALLOWED_PRINCIPALS does not help: the fallback governs authorization for
# principals reading S3 *directly*, and does nothing for a service requesting
# credentials *through* Lake Formation.
#
# So the Glue grants are not part of the governance demonstration that can wait
# for a later gate - they are a prerequisite for the pipeline running at all
# once the location is registered. Hence the separate variable.
#
# The same deployment-ordering constraint as the Phase 5 DQ rulesets, for the
# same reason, expressed the same way. It is an ordering problem, not an
# architecture.
#
# WHY THE GLUE ROLE NEEDS GRANTS AT ALL
# -------------------------------------
# It already has IAM permissions for S3 and the catalog. Under Lake Formation
# that stops being sufficient: LF arbitrates, and an ungranted principal is
# refused however good its IAM policy is. Miss one of these and the crawler
# fails on a permissions error in code that did not change.
# ---------------------------------------------------------------------------

# --- pipeline grants: var.lf_pipeline_grants_enabled -----------------------

# The registered S3 location. Without this the crawler can read the bucket
# through IAM but cannot register what it finds as catalog tables.
resource "aws_lakeformation_permissions" "glue_location" {
  count = var.lf_pipeline_grants_enabled ? 1 : 0

  principal   = local.glue_role
  permissions = ["DATA_LOCATION_ACCESS"]

  data_location {
    arn = local.lake_arn
  }
}

# Creating and updating tables during a crawl, and dropping them during
# publish_catalog.py's rename. DROP is included deliberately: publish deletes
# the crawler's intermediate tables, and without it that step fails halfway,
# leaving the catalog holding both names.
resource "aws_lakeformation_permissions" "glue_database" {
  count = var.lf_pipeline_grants_enabled ? 1 : 0

  principal   = local.glue_role
  permissions = ["CREATE_TABLE", "ALTER", "DESCRIBE", "DROP"]

  database {
    name = aws_glue_catalog_database.training.name
  }
}

# The publisher. scripts/publish_catalog.py runs as the caller - dev-user - and
# renames the crawler's discovered tables into the graded names, which means
# deleting the intermediates once the copies exist.
#
# It needs an explicit DROP and being a Data Lake Administrator is not enough.
# That is worth stating precisely, because it is the opposite of what the word
# "administrator" suggests:
#
#   customers_raw   CreatedBy dev-user    -> dev-user holds ALL/ALTER/DROP/...
#   customers       CreatedBy AWS-Crawler -> ONLY the glue role holds them
#
# Lake Formation gives the CREATING principal implicit full permissions on a
# table. dev-user's database-level DROP does not cascade to a table another
# principal created, and admin status does not substitute for the table-level
# check. Recovery after the lifecycle incident is where this surfaced:
#
#   AccessDeniedException on DeleteTable:
#   Insufficient Lake Formation permission(s): Required Drop on customers
#
# It had worked in Phase 6 only because dev-user still held grants from before
# IAM_ALLOWED_PRINCIPALS was removed. `down` destroyed those with the rest of
# the training layer, so without this resource the rename breaks in every
# session from now on - the catalog is left holding both names, halfway through.
#
# wildcard = true is ALL_TABLES in training_db, and it is deliberate rather than
# lazy: the tables this needs to drop are created by the crawler AFTER this
# grant is applied, so they cannot be named here. The scope is one database that
# is rebuilt every session, and the permission is DROP alone - not ALL, not
# SELECT, and nothing at all outside training_db.
resource "aws_lakeformation_permissions" "publisher_drop" {
  count = var.lf_pipeline_grants_enabled ? 1 : 0

  principal   = data.aws_caller_identity.current.arn
  permissions = ["DROP"]

  table {
    database_name = aws_glue_catalog_database.training.name
    wildcard      = true
  }
}

# Redshift Spectrum, added in Phase 7 in response to an actual denial.
#
# COPY and Spectrum read the same curated bytes by different routes, and only
# one of them is governed:
#
#   COPY      S3 directly, under the role's own IAM policy. Worked first time.
#   Spectrum  Redshift -> Glue Catalog -> Lake Formation -> S3. Refused:
#
#     AccessDeniedException from glue
#     Insufficient Lake Formation permission(s) on orders_raw
#
# That is the whole point of the exercise made concrete. The role's IAM policy
# already allows the catalog and the objects; after Phase 6 removed
# IAM_ALLOWED_PRINCIPALS, IAM alone stopped being sufficient for anything
# reached through the catalog.
#
# Note where it did NOT fail. Creating the external schema and listing tables
# both succeeded - metadata is reachable. Only the data read is refused, which
# is the distinction between knowing a table exists and being allowed to read
# it, and it is visible here rather than merely asserted.
#
# ONE THING AT A TIME, DELIBERATELY
# ---------------------------------
# The role is missing TWO things: this grant, and the IAM action
# lakeformation:GetDataAccess (currently implicitDeny). Phase 6 established
# that a Glue job needs both.
#
# Only the grant is added here. If Spectrum then works, GetDataAccess was never
# required on this path and we have learned something real about how Spectrum
# differs from a Glue job reading the same table. If it instead fails with
# "LFCredential fetch failed", that names the second requirement precisely and
# earns its own change. Granting both at once would work and would prove
# nothing about which was necessary.
#
# SELECT and DESCRIBE only, on the three raw tables. Not curated_sales - the
# warehouse already holds that data via COPY, and Spectrum's purpose here is to
# reach what was never loaded.
resource "aws_lakeformation_permissions" "spectrum_tables" {
  for_each = var.lf_pipeline_grants_enabled ? toset(["customers_raw", "products_raw", "orders_raw"]) : toset([])

  principal   = local.redshift_role
  permissions = ["SELECT", "DESCRIBE"]

  table {
    database_name = aws_glue_catalog_database.training.name
    name          = each.value
  }
}

# --- governance grants: var.lf_grants_enabled - the matrix (p.15) ---------
#
#   DataEngineerRole      customers, products, orders
#   FinanceAnalystRole    sales, orders
#   MarketingAnalystRole  customers: customer_id, country - NOT email
#
# Granted by name rather than by tag. The matrix is three roles over four
# tables; naming them says exactly what is intended, and a tag layer would add
# indirection without removing any decision. One LF-Tag is demonstrated
# separately below so tag-based access control is covered as a concept.

# The ETL job reads the raw tables. Same necessity as the database grant above:
# under LF the job's IAM policy alone will not get it a row.
resource "aws_lakeformation_permissions" "glue_tables" {
  for_each = var.lf_pipeline_grants_enabled ? toset(["customers_raw", "products_raw", "orders_raw"]) : toset([])

  principal   = local.glue_role
  permissions = ["SELECT", "DESCRIBE"]

  table {
    database_name = aws_glue_catalog_database.training.name
    name          = each.value
  }
}

resource "aws_lakeformation_permissions" "data_engineer" {
  for_each = var.lf_grants_enabled ? toset(["customers_raw", "products_raw", "orders_raw"]) : toset([])

  principal   = local.personas["data_engineer"]
  permissions = ["SELECT", "DESCRIBE"]

  table {
    database_name = aws_glue_catalog_database.training.name
    name          = each.value
  }
}

# "sales" in the brief is this project's curated_sales - the fact table the
# Phase 3 pipeline produces. It reaches the catalog through the Phase 5 Athena
# DDL rather than the crawler, because curated/ and quarantine/ are both named
# `sales` in S3 and would collide on one crawled table name.
resource "aws_lakeformation_permissions" "finance_analyst" {
  for_each = var.lf_grants_enabled ? toset(["curated_sales", "orders_raw"]) : toset([])

  principal   = local.personas["finance_analyst"]
  permissions = ["SELECT", "DESCRIBE"]

  table {
    database_name = aws_glue_catalog_database.training.name
    name          = each.value
  }
}

# The column-level grant, and the point of the whole phase.
#
# `table_with_columns` names the columns that ARE permitted, rather than
# excluding the ones that are not. That distinction matters: a future column
# added to customers_raw is inaccessible to this role by default, which is the
# correct failure direction for a permission model. Excluded_column_names would
# silently expose anything added later.
#
# customers_raw carries customer_id, customer_name, email, country,
# created_date. Marketing gets two of the five. `email` is the one the brief
# calls out, and the one the negative test targets.
resource "aws_lakeformation_permissions" "marketing_analyst" {
  count = var.lf_grants_enabled ? 1 : 0

  principal   = local.personas["marketing_analyst"]
  permissions = ["SELECT"]

  table_with_columns {
    database_name = aws_glue_catalog_database.training.name
    name          = "customers_raw"
    column_names  = ["customer_id", "country"]
  }
}

# --- one LF-Tag, to demonstrate tag-based access control -------------------
#
# Deliberately minimal. TBAC is a listed Day 6 topic and is the model that
# scales - you tag data once and grant against the tag, instead of maintaining
# a grant per role per table. At three roles and four tables the named grants
# above are clearer, so this exists to show the mechanism rather than to carry
# the authorisation model.
#
# The tag is attached to the quarantine table, which is a genuinely reasonable
# thing to classify: it holds rejected rows, and who may read failed records is
# a real access question rather than an invented one.

resource "aws_lakeformation_lf_tag" "sensitivity" {
  count = var.lf_grants_enabled ? 1 : 0

  key    = "sensitivity"
  values = ["public", "internal", "restricted"]
}

resource "aws_lakeformation_resource_lf_tags" "quarantine_restricted" {
  count = var.lf_grants_enabled ? 1 : 0

  table {
    database_name = aws_glue_catalog_database.training.name
    name          = "quarantine_sales"
  }

  lf_tag {
    key   = aws_lakeformation_lf_tag.sensitivity[0].key
    value = "restricted"
  }
}

# Granted against the TAG, not the table: DataEngineerRole may read anything
# classified restricted, whatever that turns out to be. Adding a second
# restricted table later grants access to it automatically - which is the
# property that makes TBAC worth the indirection at scale, and the reason the
# same property is a liability if the tagging is careless.
resource "aws_lakeformation_permissions" "restricted_by_tag" {
  count = var.lf_grants_enabled ? 1 : 0

  principal   = local.personas["data_engineer"]
  permissions = ["SELECT", "DESCRIBE"]

  lf_tag_policy {
    resource_type = "TABLE"

    expression {
      key    = aws_lakeformation_lf_tag.sensitivity[0].key
      values = ["restricted"]
    }
  }

  depends_on = [aws_lakeformation_resource_lf_tags.quarantine_restricted]
}
