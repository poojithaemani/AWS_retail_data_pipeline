# ---------------------------------------------------------------------------
# Glue Crawler - Phase 2.
#
# One crawler with three S3 targets, not three crawlers. Crawlers bill at
# $0.44 per DPU-hour with a ten-minute minimum *per run*, so three crawlers is
# three minimums for the same work: roughly $0.21 a run instead of $0.07. The
# tables produced are identical either way.
#
# The trade-off accepted: the three datasets can no longer be crawled on
# independent schedules. Nothing here needs that - they arrive together.
# ---------------------------------------------------------------------------

resource "aws_glue_crawler" "raw" {
  name          = "${var.project}-raw-crawler"
  role          = local.glue_role
  database_name = aws_glue_catalog_database.training.name

  # NO CUSTOM CLASSIFIER, and that is a decision rather than an omission.
  #
  # One was written for customers.csv, whose all-string columns give the
  # built-in CSV classifier no way to tell the header from the data. It failed
  # twice over:
  #
  #   1. It did not fix customers - that table still came back as col0..col4
  #      with the header row counted as data.
  #   2. It WAS applied to fixtures/products_drift, a four-column file, whose
  #      columns became customer_id, customer_name, email, country - the
  #      customers header list, truncated. Real column names, destroyed.
  #
  # Custom classifiers attach to the CRAWLER, not to an S3 target. There is no
  # way to scope one to a single include path, so a classifier written for one
  # dataset is evaluated against every dataset the crawler touches. On a shared
  # crawler that makes them dangerous.
  #
  # Both crawls reported SUCCEEDED.
  #
  # customers_raw is handled instead by a declared schema in
  # scripts/publish_catalog.py, which affects exactly one table and cannot
  # reach any other.
  #
  # Set to an EMPTY LIST rather than omitted, and that distinction cost a
  # failed crawl. Deleting the classifier resource and dropping this argument
  # left the crawler still pointing at it:
  #
  #     ERROR : Classifier de-training-customers-csv not found.
  #     Status: FAILED
  #
  # Terraform sends nothing for an omitted optional argument, and AWS's
  # UpdateCrawler leaves the existing value in place. Omission means "do not
  # change this", not "clear this". An explicit [] is what removes it.
  classifiers = []
  description = "Discovers the retail raw layer: customers, products, orders"

  # TABLE NAMING - verified against the AWS Glue Developer Guide, not assumed.
  #
  #   "The name of the table is based on the Amazon S3 prefix or folder name."
  #   "If duplicate table names are encountered, the crawler adds a hash string
  #    suffix to the name."
  #     - docs.aws.amazon.com/glue/latest/dg/add-crawler.html
  #
  # There is no documented mechanism for matching an existing table by its
  # StorageDescriptor.Location, and no custom naming beyond an optional
  # *prefix*. So a target of raw/customers produces a table called `customers`,
  # and the brief's required `customers_raw` cannot come from the crawler.
  #
  # An earlier design assumed the crawler would adopt a pre-created table whose
  # location matched. The documentation does not support that, and building on
  # it would have produced either stale hand-written tables sitting beside
  # crawler-created ones, or hash-suffixed duplicates.
  #
  # The documented workaround is to rename through the Glue API after the
  # crawl, which is what scripts/publish_catalog.py does: the crawler performs
  # the schema *discovery*, and the discovered schema is then published under
  # the contract-required names. Renaming the S3 prefixes instead would mean
  # copying and deleting raw data, which the immutability control forbids and
  # which would duplicate the dataset for a cosmetic reason.

  # Include paths are already narrow - three explicit dataset prefixes, not
  # raw/ and not the bucket root. The exclusions are defence in depth: if a
  # target is ever widened by mistake, these keep the crawler out of the
  # measurement corpora, the derived layers and Athena's own output.
  #
  # Crawling athena-results/ would be the expensive mistake: every query writes
  # a CSV there, so the crawler would discover thousands of one-off tables and
  # bill for the privilege.
  s3_target {
    path = "s3://${local.lake_bucket}/raw/customers"
    exclusions = [
      "**/_SUCCESS",
      "**/.hive-staging*",
      "**/*.tmp",
    ]
  }

  s3_target {
    path = "s3://${local.lake_bucket}/raw/products"
    exclusions = [
      "**/_SUCCESS",
      "**/.hive-staging*",
      "**/*.tmp",
    ]
  }

  s3_target {
    path = "s3://${local.lake_bucket}/raw/orders"
    exclusions = [
      "**/_SUCCESS",
      "**/.hive-staging*",
      "**/*.tmp",
    ]
  }

  # The schema-drift fixture, deliberately OUTSIDE raw/.
  #
  # The failure exercise needs a products dataset carrying price = UNKNOWN.
  # Putting that in raw/ would mean either mutating the clean Phase 1 dataset
  # or adding a file that then has to be deleted - and raw/ forbids deletion.
  # A fixture prefix keeps the clean dataset untouched and makes cleanup an
  # ordinary delete outside the protected layer.
  #
  # This prefix is empty until the exercise runs, and a crawler target with no
  # objects produces no table. So the drift table appears only when the fixture
  # is delivered, which is exactly the observable event the phase is about.
  s3_target {
    path = "s3://${local.lake_bucket}/fixtures/products_drift"
  }

  # UPDATE_IN_DATABASE is what makes the Phase 2 exercise possible: when the
  # products delivery arrives containing price = UNKNOWN, the crawler is
  # allowed to retype the column and the breakage becomes visible downstream.
  #
  # LOG is the production-safe setting, and the contrast between the two *is*
  # the schema-evolution answer. LOG detects the change and records it without
  # applying it, so a bad upstream delivery cannot silently repoint a column
  # from double to string underneath a running pipeline.
  #
  # DEPRECATE_IN_DATABASE on delete rather than DELETE_FROM_DATABASE: a
  # partition disappearing from S3 is far more often a failed upload than an
  # intentional removal, and a deprecated table can be inspected where a
  # deleted one cannot.
  schema_change_policy {
    update_behavior = "UPDATE_IN_DATABASE"
    delete_behavior = "DEPRECATE_IN_DATABASE"
  }

  # CRAWL_EVERYTHING rather than CRAWL_NEW_FOLDERS_ONLY. The incremental mode
  # is cheaper and is right for an append-only lake in production, but it would
  # skip the modified products prefix and the schema break would never be
  # detected. Correctness of the exercise beats a few cents.
  recrawl_policy {
    recrawl_behavior = "CRAWL_EVERYTHING"
  }

  # Partitions inherit the table schema instead of being tracked separately.
  # Without this, a schema change on one partition creates a partition-level
  # schema that diverges from the table, and Athena reports HIVE_PARTITION_
  # SCHEMA_MISMATCH rather than showing the retyped column - which would hide
  # the very thing this phase is trying to observe.
  # Partitions inherit the table schema instead of being tracked separately.
  # Without this, a schema change on one partition creates a partition-level
  # schema that diverges from the table, and Athena reports
  # HIVE_PARTITION_SCHEMA_MISMATCH rather than showing the retyped column.
  #
  # TableGroupingPolicy = CombineCompatibleSchemas was here and has been
  # REMOVED, because it silently defeated the schema-drift exercise.
  #
  # That option collapses *compatible* schemas across include paths into a
  # single table. The drift fixture has the same four columns as raw/products,
  # so the crawler judged them compatible and merged them: no products_drift
  # table was created, TablesCreated came back None, and price stayed double.
  # The crawl succeeded and produced nothing to look at.
  #
  # It had been added defensively while reasoning about partition schema
  # mismatch - but that concern is handled by the CrawlerOutput setting below,
  # not by grouping. Two settings that sound related and are not.
  #
  # The general lesson: a crawler option chosen for one reason can quietly
  # change what the catalog contains for an entirely different reason. The
  # crawl reported SUCCEEDED either way.
  configuration = jsonencode({
    Version = 1.0
    CrawlerOutput = {
      Partitions = { AddOrUpdateBehavior = "InheritFromTable" }
    }
  })

  tags = {
    Name    = "${var.project}-raw-crawler"
    Purpose = "Schema discovery over the retail raw layer"
  }
}
