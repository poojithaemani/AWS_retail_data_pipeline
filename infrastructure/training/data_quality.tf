# ---------------------------------------------------------------------------
# Glue Data Quality - Phase 5.
#
# Two rulesets, written in DQDL, evaluated on demand against the published raw
# tables. No schedule, no trigger, no Step Functions - orchestration is Phase 8.
#
# WHY THIS EXISTS WHEN validate() ALREADY QUARANTINES
# ---------------------------------------------------
# They do different jobs and the distinction is the point of this phase.
#
#   validate()  routes. It decides, row by row, what may enter curated and
#               writes the rest to quarantine with a reason. It is the control.
#   DQDL        measures. It answers "how healthy is this dataset" as a
#               published, queryable verdict against a declared standard.
#
# The clearest case is duplicates. `deduplicate()` collapses duplicate
# order_ids before validation, so they never appear as rejections - operational
# behaviour that is correct and completely silent. Only an explicit uniqueness
# rule reports that 3,761 of 13,861 rows were duplicates. A pipeline can handle
# a problem and still owe someone a number for it.
#
# A DEPLOYMENT DEPENDENCY, NOT AN ARCHITECTURAL ONE
# -------------------------------------------------
# The variable below exists to order two applies correctly. It adds no
# component, no indirection and no runtime behaviour - remove the ordering
# problem and the variable goes with it.
#
# A ruleset is bound to a catalog table, and AWS validates that the table
# exists when the ruleset is created. `orders_raw` and `products_raw` are not
# Terraform-managed: they are discovered by the crawler and renamed by
# scripts/publish_catalog.py, both of which run *after* the training layer is
# applied. Creating these on the first apply would therefore fail on a table
# that does not exist yet.
#
# So the phase applies in two passes:
#
#   ./scripts/de.sh up 05                       crawler, job, database
#   ./scripts/de.sh crawl && ./scripts/de.sh publish
#   TF_VAR_data_quality_enabled=true ./scripts/de.sh up 05      rulesets
#
# A depends_on cannot express this - the dependency is on a resource Terraform
# does not own.
# ---------------------------------------------------------------------------

resource "aws_glue_data_quality_ruleset" "orders_raw" {
  count = var.data_quality_enabled ? 1 : 0

  name        = "${var.project}-orders-quality"
  description = "Retail order completeness, uniqueness and quantity sanity"

  # Three of the brief's four Day 5 detections. The fourth is monetary and
  # cannot be expressed here - orders_raw has no price column - so it lives on
  # products_raw below, where the number actually is.
  #
  # Nothing else is asserted. Completeness of order_id or product_id would be
  # reasonable rules in general, but they are not what this phase is measuring,
  # and a ruleset padded with rules nobody asked for makes the real findings
  # harder to read.
  #
  # Uniqueness is deliberately asserted at 1.0 and is EXPECTED TO FAIL against
  # the real lake, which carries duplicate order_ids across two generator runs.
  # That failure is the measurement working. A threshold tuned down until it
  # passes would describe the data rather than state the standard, and would
  # report nothing on the day duplicates doubled.
  ruleset = <<-DQDL
    Rules = [
        IsComplete "customer_id",
        Uniqueness "order_id" = 1.0,
        ColumnValues "quantity" > 0
    ]
  DQDL

  target_table {
    database_name = aws_glue_catalog_database.training.name
    table_name    = "orders_raw"
  }

  tags = {
    Name    = "${var.project}-orders-quality"
    Purpose = "Declared quality standard for the retail order feed"
  }
}

resource "aws_glue_data_quality_ruleset" "products_raw" {
  count = var.data_quality_enabled ? 1 : 0

  name        = "${var.project}-products-quality"
  description = "Retail product price sanity - the brief's negative amount case"

  # The monetary rule. `order_total = quantity x price` is derived, so a
  # negative amount can only originate here, which is also why validate()
  # rejects on price rather than on the computed total.
  ruleset = <<-DQDL
    Rules = [
        ColumnValues "price" > 0
    ]
  DQDL

  target_table {
    database_name = aws_glue_catalog_database.training.name
    table_name    = "products_raw"
  }

  tags = {
    Name    = "${var.project}-products-quality"
    Purpose = "Declared quality standard for product pricing"
  }
}
