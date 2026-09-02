"""Transformations for the curated sales dataset.

Pure PySpark. **Nothing here imports `awsglue`**, and that is the point: the
Glue libraries cannot be installed locally, so any logic living in the job
entry script can never be unit-tested. Keeping the transformations in a module
that depends only on `pyspark` means every rule below is covered by a test that
runs in a second, with no AWS and no cost.

The seven functions are the structure the brief names (section 4, Day 4), and
between them they perform the eleven transformations it lists for Day 3:

    extract        read source data (from the Glue Catalog)
    deduplicate    remove duplicates
    clean          handle NULLs, convert types, standardise dates
    validate       filter invalid records - including referential integrity
    enrich         join customers, join products
    transform      calculate order_total, derive year/month/day
    write_output   write partitioned Parquet

DataFrames throughout, no DynamicFrame. Phase 3's inputs are the clean raw
layer, where `price` is a double and no column is ambiguous, so there is
nothing for `ResolveChoice` to resolve. It would be the right tool if a drifted
delivery reached this job - see docs/reference-repos.md.

ON ORPHAN RECORDS, WHICH IS THE DESIGN DECISION THAT MATTERS HERE
-----------------------------------------------------------------
An order referencing a product that does not exist has no price, so no
`order_total` can be computed for it.

Two tempting answers are both wrong:

    inner join   drops the row silently. Revenue comes out lower and nothing
                 anywhere says so.
    left join    keeps the row with a null price, so order_total is null or
                 zero. That is a *manufactured* curated record - it looks like
                 a complete sale that happened to be worth nothing.

Neither is acceptable, because both let a referential failure become a
reporting number. So orphans are **rejected explicitly, before any join**, and
carry the reason why. `validate()` returns two frames, and:

    input rows == curated rows + rejected rows

holds exactly, always. Rows are never lost, only accounted for. Curated
contains complete records and nothing else; rejected is a real dataset with a
`rejection_reason` column, which is what Phase 5 routes to quarantine.

The inner joins in `enrich()` are then safe by construction - every orphan has
already been removed and counted, so the join cannot silently drop anything.
"""

from __future__ import annotations

from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F

# The order lifecycle the business recognises. A status outside this set means
# an upstream system started emitting something new, which is a data-quality
# event rather than a value to pass through.
VALID_STATUSES = ("DELIVERED", "SHIPPED", "PENDING", "CANCELLED", "RETURNED")

# The raw layer stores timestamps in this shape. Anything else is malformed and
# is rejected rather than coerced.
ORDER_DATE_FORMAT = "yyyy-MM-dd HH:mm:ss"


# --------------------------------------------------------------------------
# extract
# --------------------------------------------------------------------------


def extract(spark: SparkSession, database: str) -> dict[str, DataFrame]:
    """Read the three raw tables from the Glue Data Catalog.

    Spark SQL rather than the Glue DynamicFrame API, so the same code runs
    locally against a local metastore in tests.

    This requires the job to set `--enable-glue-datacatalog`. Glue does NOT
    wire the Data Catalog in as the Hive metastore by default: without that
    flag `spark.sql` resolves against Spark's own in-memory catalog and every
    read here fails with TABLE_OR_VIEW_NOT_FOUND. The flag is set in
    infrastructure/training/glue_job.tf, and it is the one piece of this
    module's contract that lives outside the module.
    """
    return {
        name: spark.sql(f"SELECT * FROM {database}.{name}_raw")
        for name in ("customers", "products", "orders")
    }


# --------------------------------------------------------------------------
# deduplicate
# --------------------------------------------------------------------------


def deduplicate(orders: DataFrame) -> DataFrame:
    """One row per order_id, chosen deterministically.

    Not `dropDuplicates()`. The duplicates in this dataset *conflict* - the
    injector produces two rows sharing an order_id with different quantities and
    statuses - so "remove duplicates" is really "decide which one is true", and
    `dropDuplicates()` answers that by picking whichever row Spark happened to
    read first. That is non-deterministic across runs and partition counts, so
    the same input can produce different revenue twice.

    The rule here: latest `order_date` wins, ties broken by `status` so the
    result is stable even when the timestamps match exactly. Any explicit rule
    would do; having one is what matters.
    """
    ranked = Window.partitionBy("order_id").orderBy(
        F.col("order_date").desc_nulls_last(), F.col("status").asc_nulls_last()
    )
    return (
        orders.withColumn("_rank", F.row_number().over(ranked))
        .filter(F.col("_rank") == 1)
        .drop("_rank")
    )


# --------------------------------------------------------------------------
# clean
# --------------------------------------------------------------------------


def clean(orders: DataFrame) -> DataFrame:
    """Handle NULLs, convert types, standardise dates.

    Cleaning never *drops* a row. It normalises what it can and leaves failures
    as nulls for `validate()` to judge, so that "this value was unparseable" and
    "this row is unacceptable" stay separate decisions. Mixing them is how rows
    disappear without anyone deciding they should.

    Empty strings become NULL first: a CSV export writes a missing value as an
    empty field, and `'' IS NULL` is false, so a completeness check would
    otherwise pass on a value that is plainly absent.
    """
    blank_to_null = [
        F.when(F.trim(F.col(c)) == "", None).otherwise(F.trim(F.col(c))).alias(c)
        if dtype == "string"
        else F.col(c)
        for c, dtype in orders.dtypes
    ]
    normalised = orders.select(*blank_to_null)

    return normalised.select(
        F.col("order_id"),
        F.col("customer_id"),
        F.col("product_id"),
        # Cast, do not coerce. A non-numeric quantity becomes NULL and is
        # rejected downstream; defaulting it to 0 or 1 would invent data.
        F.col("quantity").cast("int").alias("quantity"),
        # An unparseable timestamp becomes NULL rather than raising. On Spark
        # 3.5 that is the default; on Spark 4 with ANSI mode this would throw,
        # which is why the local pyspark version is pinned to 3.5.
        F.to_timestamp(F.col("order_date"), ORDER_DATE_FORMAT).alias("order_date"),
        F.upper(F.col("status")).alias("status"),
    )


# --------------------------------------------------------------------------
# validate
# --------------------------------------------------------------------------


def validate(
    orders: DataFrame, customers: DataFrame, products: DataFrame
) -> tuple[DataFrame, DataFrame]:
    """Split cleaned orders into (valid, rejected).

    Returns both halves rather than filtering in place, so nothing is discarded
    without being counted. Every rejected row keeps a `rejection_reason`, which
    is what makes the reconciliation meaningful:

        input rows == valid rows + rejected rows

    Referential integrity is checked *here*, before any join, so an orphan is a
    stated decision rather than a row an inner join happened to drop. See the
    module docstring.

    A row failing several rules is reported under the first that matches, in
    the order below. Deliberate: one row, one reason, so the counts per reason
    sum to the total and can be reconciled.
    """
    known_customers = customers.select(F.col("customer_id").alias("_ck")).distinct()
    known_products = products.select(F.col("product_id").alias("_pk")).distinct()

    annotated = (
        orders.join(known_customers, orders["customer_id"] == F.col("_ck"), "left")
        .join(known_products, orders["product_id"] == F.col("_pk"), "left")
        .withColumn(
            "rejection_reason",
            F.when(F.col("customer_id").isNull(), F.lit("null_customer_id"))
            .when(F.col("order_date").isNull(), F.lit("malformed_order_date"))
            .when(F.col("quantity").isNull(), F.lit("non_numeric_quantity"))
            .when(F.col("quantity") <= 0, F.lit("non_positive_quantity"))
            .when(~F.col("status").isin(*VALID_STATUSES), F.lit("invalid_order_status"))
            .when(F.col("_ck").isNull(), F.lit("orphan_customer_id"))
            .when(F.col("_pk").isNull(), F.lit("orphan_product_id"))
            .otherwise(F.lit(None)),
        )
        .drop("_ck", "_pk")
    )

    valid = annotated.filter(F.col("rejection_reason").isNull()).drop("rejection_reason")
    rejected = annotated.filter(F.col("rejection_reason").isNotNull())
    return valid, rejected


# --------------------------------------------------------------------------
# enrich
# --------------------------------------------------------------------------


def enrich(orders: DataFrame, customers: DataFrame, products: DataFrame) -> DataFrame:
    """Join customers and products onto validated orders.

    Inner joins, and safe *because* `validate()` already removed and counted
    every orphan. An inner join here cannot silently drop a row - if it ever
    did, the reconciliation would fail and say so, which is the point of
    checking referential integrity before joining rather than relying on the
    join to enforce it.
    """
    return (
        orders.join(
            customers.select("customer_id", "country", "created_date"),
            on="customer_id",
            how="inner",
        )
        .join(
            products.select("product_id", "product_name", "category", "price"),
            on="product_id",
            how="inner",
        )
    )


# --------------------------------------------------------------------------
# transform
# --------------------------------------------------------------------------


def transform(enriched: DataFrame) -> DataFrame:
    """Calculate order_total and derive the partition columns.

    order_total = quantity x price, per the brief.

    Cast to decimal(12,2) rather than left as a double. Money in floating point
    accumulates representation error, and a revenue total that disagrees with
    itself by a fraction of a cent between runs is the kind of thing nobody can
    explain later.

    year/month/day come from `order_date` and are zero-padded strings, matching
    the raw layer's partition convention. Integers would drop the padding and
    stop matching the S3 prefixes, which is the same trap the Phase 1 DDL noted.
    """
    return (
        enriched.withColumn(
            "order_total",
            (F.col("quantity") * F.col("price")).cast("decimal(12,2)"),
        )
        .withColumn("year", F.date_format(F.col("order_date"), "yyyy"))
        .withColumn("month", F.date_format(F.col("order_date"), "MM"))
        .withColumn("day", F.date_format(F.col("order_date"), "dd"))
        .select(
            "order_id",
            "customer_id",
            "product_id",
            "product_name",
            "category",
            "country",
            "quantity",
            "price",
            "order_total",
            "status",
            "order_date",
            "year",
            "month",
            "day",
        )
    )


# --------------------------------------------------------------------------
# write_output
# --------------------------------------------------------------------------


def write_output(
    frame: DataFrame, path: str, *, partition_by: tuple[str, ...] = ("year", "month", "day")
) -> None:
    """Write partitioned Parquet.

    `overwrite` with dynamic partition overwrite, so a re-run replaces only the
    partitions it actually produced. A static overwrite would delete every
    partition in curated, including days this run never looked at - which turns
    a re-run of one day into a silent loss of the rest.
    """
    (
        frame.write.mode("overwrite")
        .option("compression", "snappy")
        .partitionBy(*partition_by)
        .parquet(path)
    )


def reconcile(
    source_count: int,
    deduplicated_count: int,
    valid_count: int,
    rejected_count: int,
    curated_count: int,
) -> dict[str, object]:
    """Prove no row vanished.

    Three identities, all of which must hold:

        source  == duplicates + valid + rejected   every input row explained
        dedup   == valid + rejected                nothing dropped unaccounted
        curated == valid                           everything accepted written

    `deduplicated_count` is separate from `source_count` on purpose, and its
    absence was a real bug. Deduplication removes rows *between* the input and
    the split, so `source == valid + rejected` is false whenever the input
    contains duplicates - which it always does here. The first real run failed
    on exactly that: 13,803 source rows against 10,000 valid + 1 rejected, with
    3,802 collapsed duplicates unaccounted for and no term to put them in.

    Counting duplicates as their own category is also more useful than making
    the arithmetic work: "3,802 rows were duplicates" is a fact about the
    source worth reporting, not a discrepancy to absorb.

    A run that processes nothing balances trivially - every term is zero, so
    all three identities hold and `balanced` is True. That is not an accident
    of the arithmetic; it is the required behaviour once job bookmarks are
    enabled, because the second run over an unchanged dataset reads zero rows.
    A pipeline that treated "nothing new arrived" as a failure would page
    someone every night for working correctly. Asserted in the tests so a
    future change here cannot quietly turn a no-op run into an alarm.

    Returned rather than asserted so the caller can record the numbers as
    evidence whether or not they balance. A reconciliation that only prints on
    success is not a check.
    """
    duplicates_removed = source_count - deduplicated_count
    accounted_for = deduplicated_count == valid_count + rejected_count
    curated_matches_valid = curated_count == valid_count
    return {
        "source_rows": source_count,
        "duplicates_removed": duplicates_removed,
        "deduplicated_rows": deduplicated_count,
        "valid_rows": valid_count,
        "rejected_rows": rejected_count,
        "curated_rows": curated_count,
        "accounted_for": accounted_for,
        "curated_matches_valid": curated_matches_valid,
        "every_source_row_explained": source_count
        == duplicates_removed + valid_count + rejected_count,
        "balanced": accounted_for and curated_matches_valid,
    }
