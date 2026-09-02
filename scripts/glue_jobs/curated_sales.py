"""Glue entry point: raw orders -> curated sales.

This file is the thin part. It wires Glue's context to the transformations in
`retail_pipeline.transforms` and does nothing clever itself, because everything
in here imports `awsglue` and therefore cannot be unit-tested locally. Every
rule worth testing lives in the module; this file only sequences it.

Job parameters (a brief Day 3 topic):

    --database          Glue Data Catalog database          default training_db
    --lake_bucket       target bucket                       required
    --curated_prefix    output prefix                       default curated/sales
    --rejected_prefix   where rejected rows go              default quarantine/sales

Rejected rows are written even though quarantine is Phase 5's subject. Writing
them costs nothing extra and means a row is never merely counted as missing -
it exists somewhere with the reason attached. A reconciliation that says "412
rows were dropped" is far less useful than one you can query.
"""

import sys

from awsglue.context import GlueContext
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext

from retail_pipeline import config as C
from retail_pipeline import transforms as T

REQUIRED = ["JOB_NAME", "lake_bucket"]
OPTIONAL = {
    "database": "training_db",
    "curated_prefix": "curated/sales",
    "rejected_prefix": "quarantine/sales",
    # The data contract. An s3:// URI so the contract can be corrected and the
    # job re-run without repackaging code - which is the point of driving
    # behaviour from configuration rather than from constants.
    "pipeline_config": "",
}


def resolve_options(argv: list[str]) -> dict[str, str]:
    """getResolvedOptions raises on an absent argument, so optionals are probed."""
    supplied = {a.lstrip("-").split("=")[0] for a in argv}
    names = list(REQUIRED) + [k for k in OPTIONAL if k in supplied]
    options = getResolvedOptions(argv, names)
    for key, default in OPTIONAL.items():
        options.setdefault(key, default)
    return options


def main() -> None:
    options = resolve_options(sys.argv)

    sc = SparkContext.getOrCreate()
    glue_context = GlueContext(sc)
    spark = glue_context.spark_session
    job = Job(glue_context)
    job.init(options["JOB_NAME"], options)

    log = glue_context.get_logger()
    bucket = options["lake_bucket"]

    # The contract wins where it speaks; the job arguments remain the fallback
    # so a run without --pipeline_config behaves exactly as Phase 3 did.
    contract = {}
    if options.get("pipeline_config"):
        contract = C.load_contract(options["pipeline_config"])
        log.info(f"loaded data contract from {options['pipeline_config']}")
    else:
        log.info("no --pipeline_config given; using job-argument defaults")

    tables = C.catalog_tables(contract) if contract else None
    curated = C.curated_output(contract) if contract else {}

    curated_prefix = curated.get("prefix") or options["curated_prefix"]
    rejected_prefix = (
        C.quarantine_prefix(contract) if contract else options["rejected_prefix"]
    )
    partition_by = curated.get("partition_by") or ("year", "month", "day")

    # write_output writes snappy Parquet. If the contract ever says otherwise,
    # say so loudly rather than writing something the contract does not
    # describe - a silent disagreement between the two is worse than either.
    compression = curated.get("compression", "snappy")
    if compression != "snappy":
        raise ValueError(
            f"contract asks for {compression!r} compression but the pipeline writes snappy"
        )

    curated_path = f"s3://{bucket}/{curated_prefix}"
    rejected_path = f"s3://{bucket}/{rejected_prefix}"
    log.info(
        f"output {curated_path} partitioned by {partition_by} ({compression}); "
        f"rejects to {rejected_path}"
    )

    # Replace only the partitions this run produces. Without this, a re-run of
    # one day would overwrite the whole curated prefix.
    spark.conf.set("spark.sql.sources.partitionOverwriteMode", "dynamic")

    log.info(f"reading catalog database {options['database']}")
    names = tables or {n: f"{n}_raw" for n in ("customers", "products", "orders")}
    database = options["database"]

    # Dimensions: full read, every run, deliberately NOT bookmarked.
    #
    # A bookmark on customers or products would be actively harmful. The second
    # run would read zero dimension rows, every order would fail its referential
    # check, and the job would quarantine the entire tranche as orphans while
    # reporting a balanced reconciliation and SUCCEEDED. Bookmarks belong on the
    # fact table that grows, not on the lookups that do not.
    customers = spark.sql(f"SELECT * FROM {database}.{names['customers']}")
    products = spark.sql(f"SELECT * FROM {database}.{names['products']}")

    # Orders: read through the Glue reader so bookmarks apply.
    #
    # This is the one place a DynamicFrame appears, and it exists for exactly
    # one reason: job bookmarks are driven by `transformation_ctx` on Glue's
    # own readers. A plain spark.sql() read bypasses the bookmark machinery
    # entirely, so --job-bookmark-enable would silently do nothing - every run
    # reprocessing everything while still succeeding, which is the worst shape
    # a failure can take. Converted to a DataFrame on the next line, so
    # transforms.py stays pure PySpark and every unit test stays valid.
    orders = glue_context.create_dynamic_frame.from_catalog(
        database=database,
        table_name=names["orders"],
        transformation_ctx="orders_source",
    ).toDF()

    # A bookmarked run with nothing new reads no files, so Glue has no schema
    # to infer and hands back a frame with zero rows AND zero columns. The
    # pipeline cannot run on that - deduplicate() windows over order_id, which
    # does not exist - and it should not have to: "no new data" is a successful
    # outcome, not an error. Commit the bookmark and finish.
    if not orders.columns:
        log.info("no new data since the last bookmark; nothing to process")
        log.info(f"reconciliation: {T.reconcile(0, 0, 0, 0, 0)}")
        job.commit()
        return

    source_rows = orders.count()
    log.info(f"source orders: {source_rows}")

    deduped = T.deduplicate(orders)

    # Counted separately from source_rows, because deduplication removes rows
    # between the two. Without this term the reconciliation cannot balance on
    # any input containing duplicates - which is every run against this lake.
    deduplicated_rows = deduped.count()
    log.info(f"after dedupe: {deduplicated_rows} ({source_rows - deduplicated_rows} duplicates)")

    cleaned = T.clean(deduped)
    valid, rejected = T.validate(cleaned, customers, products)

    # Counted before writing. Spark is lazy, and counting after a write would
    # re-execute the whole plan rather than report what was written.
    valid_rows = valid.count()
    rejected_rows = rejected.count()

    enriched = T.enrich(valid, customers, products)
    curated_frame = T.transform(enriched)
    curated_rows = curated_frame.count()

    # A bookmarked re-run over unchanged data reads nothing. Writing an empty
    # partitioned frame is harmless but pointless, and an empty write in the
    # log is indistinguishable from a broken one - so say so explicitly.
    if curated_rows:
        T.write_output(curated_frame, curated_path, partition_by=tuple(partition_by))
    else:
        log.info("no new rows to write; curated left untouched")

    if rejected_rows:
        # Rejected rows are not partitioned by date: a row rejected *for* a
        # malformed date has no date to partition on.
        rejected.write.mode("overwrite").option("compression", "snappy").parquet(
            rejected_path
        )

    report = T.reconcile(
        source_rows, deduplicated_rows, valid_rows, rejected_rows, curated_rows
    )
    log.info(f"reconciliation: {report}")

    # Reasons are logged individually so the failure exercise can be read off
    # the job log without querying anything.
    if rejected_rows:
        for row in rejected.groupBy("rejection_reason").count().collect():
            log.info(f"rejected {row['count']:>6}  {row['rejection_reason']}")

    if not report["balanced"]:
        # Fail loudly. An unbalanced reconciliation means rows went somewhere
        # unaccounted for, and a curated dataset that silently lost rows is
        # worse than a job that failed.
        raise RuntimeError(f"reconciliation did not balance: {report}")

    log.info(f"curated {curated_rows} rows -> {curated_path}")
    job.commit()


if __name__ == "__main__":
    main()
