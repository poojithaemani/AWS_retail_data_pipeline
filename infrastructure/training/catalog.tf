# Glue Data Catalog database. Free to hold, instant to create and destroy -
# which makes it the ideal resource for proving the apply/destroy round trip
# in Phase 0. Crawlers and tables land here from Phase 2 onward.
resource "aws_glue_catalog_database" "training" {
  name        = local.database_name
  description = "Retail e-commerce data lake catalog: customers, products, orders"

  location_uri = "s3://${local.lake_bucket}/"
}
