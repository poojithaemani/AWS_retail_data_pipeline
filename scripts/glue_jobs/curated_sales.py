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

from retail_pipeline import transforms as T

REQUIRED = ["JOB_NAME", "lake_bucket"]
OPTIONAL = {
    "database": "training_db",
    "curated_prefix": "curated/sales",
    "rejected_prefix": "quarantine/sales",
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
    curated_path = f"s3://{bucket}/{options['curated_prefix']}"
    rejected_path = f"s3://{bucket}/{options['rejected_prefix']}"

    # Replace only the partitions this run produces. Without this, a re-run of
    # one day would overwrite the whole curated prefix.
    spark.conf.set("spark.sql.sources.partitionOverwriteMode", "dynamic")

    log.info(f"reading catalog database {options['database']}")
    tables = T.extract(spark, options["database"])
    customers, products, orders = tables["customers"], tables["products"], tables["orders"]

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
    curated = T.transform(enriched)
    curated_rows = curated.count()

    T.write_output(curated, curated_path)
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
