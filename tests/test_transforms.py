"""Unit tests for the curated-sales transformations.

Real Spark, tiny fixed DataFrames, no AWS and no cost. Each test exercises one
rule with data constructed so that the expected answer is obvious by
inspection - if a test needs arithmetic to check, the fixture is too big.

The session is module-scoped because starting Spark costs a few seconds and
starting it per test would make the suite slow enough to skip.
"""

from __future__ import annotations

import os
import sys
from decimal import Decimal
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

pyspark = pytest.importorskip("pyspark", reason="pyspark not installed")

from pyspark.sql import SparkSession  # noqa: E402
from pyspark.sql.types import StructType  # noqa: E402

try:  # pyspark >= 4 moved it; the deployed Glue runtime is 3.5
    from pyspark.errors import AnalysisException  # noqa: E402
except ImportError:  # pragma: no cover
    from pyspark.sql.utils import AnalysisException  # type: ignore[no-redef]  # noqa: E402

from retail_pipeline import config as C  # noqa: E402
from retail_pipeline import transforms as T  # noqa: E402


@pytest.fixture(scope="module")
def spark():
    # Spark launches its Python workers by running `python3`, which does not
    # exist on this machine - the project uses `py -3` and there is no bare
    # `python` on PATH either. Without this the session starts, the driver
    # works, and every task fails with:
    #
    #     java.io.IOException: Cannot run program "python3":
    #     CreateProcess error=2, The system cannot find the file specified
    #
    # Pointing both variables at the running interpreter makes the suite
    # self-contained rather than dependent on how the shell was set up.
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    os.environ.setdefault("PYSPARK_DRIVER_PYTHON", sys.executable)

    session = (
        SparkSession.builder.master("local[1]")
        .appName("transforms-tests")
        # Match the Glue runtime rather than the local default. Spark 4 turns
        # ANSI mode on, which raises on invalid casts instead of returning
        # null; setting it explicitly means the test asserts the behaviour the
        # job will actually have, whichever pyspark happens to be installed.
        .config("spark.sql.ansi.enabled", "false")
        .config("spark.sql.shuffle.partitions", "1")
        .getOrCreate()
    )
    yield session
    session.stop()


ORDER_COLS = ["order_id", "customer_id", "product_id", "quantity", "order_date", "status"]


def orders_df(spark, rows):
    return spark.createDataFrame(rows, ORDER_COLS)


@pytest.fixture
def customers(spark):
    return spark.createDataFrame(
        [("C1", "US", "2025-01-01"), ("C2", "GB", "2025-02-02")],
        ["customer_id", "country", "created_date"],
    )


@pytest.fixture
def products(spark):
    return spark.createDataFrame(
        [("P1", "Widget", "Home", 10.0), ("P2", "Gadget", "Tech", 2.5)],
        ["product_id", "product_name", "category", "price"],
    )


# --------------------------------------------------------------------------
# deduplicate
# --------------------------------------------------------------------------


def test_deduplicate_keeps_latest_and_is_deterministic(spark):
    """Conflicting duplicates must resolve the same way every run.

    dropDuplicates() would keep whichever row Spark read first, so the same
    input could produce different revenue on different runs.
    """
    rows = [
        ("O1", "C1", "P1", 1, "2026-08-19 10:00:00", "PENDING"),
        ("O1", "C1", "P1", 9, "2026-08-19 12:00:00", "DELIVERED"),
        ("O2", "C2", "P2", 3, "2026-08-19 09:00:00", "SHIPPED"),
    ]
    result = T.deduplicate(orders_df(spark, rows)).collect()
    assert len(result) == 2
    winner = next(r for r in result if r["order_id"] == "O1")
    assert winner["quantity"] == 9, "latest order_date should win"

    # Same input twice must give the same answer.
    again = T.deduplicate(orders_df(spark, rows)).collect()
    assert sorted(r["quantity"] for r in result) == sorted(r["quantity"] for r in again)


# --------------------------------------------------------------------------
# clean
# --------------------------------------------------------------------------


def test_clean_nulls_types_and_dates(spark):
    rows = [
        ("O1", "C1", "P1", "2", "2026-08-19 10:00:00", "delivered"),
        ("O2", "", "P2", "abc", "19-08-2026 25:61:00", "SHIPPED"),
    ]
    out = {r["order_id"]: r for r in T.clean(orders_df(spark, rows)).collect()}

    good = out["O1"]
    assert good["quantity"] == 2, "numeric string cast to int"
    assert good["status"] == "DELIVERED", "status upper-cased"
    assert good["order_date"] is not None

    bad = out["O2"]
    assert bad["customer_id"] is None, "empty string normalised to NULL"
    assert bad["quantity"] is None, "non-numeric quantity becomes NULL, not 0"
    assert bad["order_date"] is None, "unparseable timestamp becomes NULL, not an error"


def test_clean_never_drops_rows(spark):
    """Cleaning normalises; only validate() decides a row is unacceptable."""
    rows = [
        ("O1", "", "", None, "nonsense", ""),
        ("O2", "C1", "P1", "1", "2026-08-19 10:00:00", "DELIVERED"),
    ]
    assert T.clean(orders_df(spark, rows)).count() == 2


# --------------------------------------------------------------------------
# validate
# --------------------------------------------------------------------------


def test_validate_splits_and_accounts_for_every_row(spark, customers, products):
    rows = [
        ("O1", "C1", "P1", 2, "2026-08-19 10:00:00", "DELIVERED"),   # valid
        ("O2", None, "P1", 1, "2026-08-19 10:00:00", "DELIVERED"),   # null customer
        ("O3", "C1", "P1", -1, "2026-08-19 10:00:00", "DELIVERED"),  # non-positive qty
        ("O4", "C1", "P1", 1, None, "DELIVERED"),                    # malformed date
        ("O5", "C1", "P1", 1, "2026-08-19 10:00:00", "PROCESSING"),  # invalid status
        ("O6", "C9", "P1", 1, "2026-08-19 10:00:00", "DELIVERED"),   # orphan customer
        ("O7", "C1", "P9", 1, "2026-08-19 10:00:00", "DELIVERED"),   # orphan product
    ]
    cleaned = T.clean(orders_df(spark, rows))
    valid, rejected = T.validate(cleaned, customers, products)

    assert valid.count() + rejected.count() == len(rows), "no row may vanish"
    assert [r["order_id"] for r in valid.collect()] == ["O1"]

    reasons = {r["order_id"]: r["rejection_reason"] for r in rejected.collect()}
    assert reasons == {
        "O2": "null_customer_id",
        "O3": "non_positive_quantity",
        "O4": "malformed_order_date",
        "O5": "invalid_order_status",
        "O6": "orphan_customer_id",
        "O7": "orphan_product_id",
    }


def test_orphan_product_is_rejected_not_left_joined(spark, customers, products):
    """The design decision this phase turns on.

    An orphan must not survive into curated with a null price. A left join
    would produce exactly that: a row that reads as a completed sale worth
    nothing.
    """
    rows = [("O1", "C1", "P9", 5, "2026-08-19 10:00:00", "DELIVERED")]
    valid, rejected = T.validate(T.clean(orders_df(spark, rows)), customers, products)

    assert valid.count() == 0, "orphan must not reach curated"
    assert rejected.collect()[0]["rejection_reason"] == "orphan_product_id"
    assert "price" not in valid.columns, "no null-price row is manufactured"


# --------------------------------------------------------------------------
# enrich + transform
# --------------------------------------------------------------------------


def test_enrich_cannot_drop_rows_after_validate(spark, customers, products):
    rows = [
        ("O1", "C1", "P1", 2, "2026-08-19 10:00:00", "DELIVERED"),
        ("O2", "C2", "P2", 1, "2026-08-19 11:00:00", "SHIPPED"),
    ]
    valid, _ = T.validate(T.clean(orders_df(spark, rows)), customers, products)
    assert T.enrich(valid, customers, products).count() == valid.count()


def test_transform_order_total_and_partitions(spark, customers, products):
    rows = [("O1", "C1", "P1", 3, "2026-08-19 10:00:00", "DELIVERED")]
    valid, _ = T.validate(T.clean(orders_df(spark, rows)), customers, products)
    out = T.transform(T.enrich(valid, customers, products)).collect()[0]

    # 3 x 10.00, checked as an exact decimal rather than a float comparison.
    assert out["order_total"] == Decimal("30.00")
    assert (out["year"], out["month"], out["day"]) == ("2026", "08", "19")


def test_partition_values_are_zero_padded_strings(spark, customers, products):
    """Integers would drop the padding and stop matching the S3 prefixes."""
    rows = [("O1", "C1", "P1", 1, "2026-01-05 08:00:00", "DELIVERED")]
    valid, _ = T.validate(T.clean(orders_df(spark, rows)), customers, products)
    out = T.transform(T.enrich(valid, customers, products)).collect()[0]
    assert (out["month"], out["day"]) == ("01", "05")


# --------------------------------------------------------------------------
# reconcile
# --------------------------------------------------------------------------


def test_reconcile_balances_and_detects_loss():
    # 12 source rows, 2 duplicates removed, 10 deduplicated = 7 valid + 3 rejected.
    ok = T.reconcile(12, 10, 7, 3, 7)
    assert ok["balanced"] is True
    assert ok["duplicates_removed"] == 2
    assert ok["every_source_row_explained"] is True

    lost = T.reconcile(12, 10, 7, 2, 7)
    assert lost["balanced"] is False
    assert lost["accounted_for"] is False

    unwritten = T.reconcile(12, 10, 7, 3, 6)
    assert unwritten["balanced"] is False
    assert unwritten["curated_matches_valid"] is False


def test_reconcile_counts_duplicates_rather_than_losing_them():
    """The bug that failed the first successful-writing Glue run.

    Deduplication removes rows between the source count and the valid/rejected
    split, so `source == valid + rejected` is false on any input with
    duplicates. The real run produced 13,803 source rows against 10,000 valid
    and 1 rejected; the 3,802 collapsed duplicates had nowhere to go and the
    job failed itself. Duplicates must be their own term.
    """
    report = T.reconcile(13803, 10001, 10000, 1, 10000)
    assert report["duplicates_removed"] == 3802
    assert report["balanced"] is True
    assert report["every_source_row_explained"] is True


def test_end_to_end_reconciliation_balances(spark, customers, products):
    """The property the whole design exists to guarantee."""
    rows = [
        ("O1", "C1", "P1", 2, "2026-08-19 10:00:00", "DELIVERED"),
        ("O2", "C2", "P2", 4, "2026-08-19 11:00:00", "SHIPPED"),
        ("O3", "C1", "P9", 1, "2026-08-19 12:00:00", "DELIVERED"),  # orphan product
        ("O3", "C1", "P9", 7, "2026-08-19 13:00:00", "DELIVERED"),  # duplicate of O3
        ("O4", None, "P1", 1, "2026-08-19 14:00:00", "DELIVERED"),  # null customer
    ]
    source = orders_df(spark, rows)
    deduped = T.deduplicate(source)
    valid, rejected = T.validate(T.clean(deduped), customers, products)
    curated = T.transform(T.enrich(valid, customers, products))

    report = T.reconcile(
        source.count(), deduped.count(), valid.count(), rejected.count(), curated.count()
    )
    assert report["balanced"] is True
    assert report["every_source_row_explained"] is True
    assert report["source_rows"] == 5, "the raw input, duplicate included"
    assert report["duplicates_removed"] == 1
    assert report["deduplicated_rows"] == 4, "the duplicate collapsed before validation"
    assert report["valid_rows"] == 2
    assert report["rejected_rows"] == 2


def test_reconcile_treats_a_zero_row_run_as_balanced():
    """A bookmarked re-run over unchanged data must not look like a failure.

    Once job bookmarks are on, the second run of an unchanged dataset reads
    zero rows. Every term is zero, all three identities hold, and the job must
    succeed - a pipeline that raises on "nothing new arrived" would page
    someone every night for working correctly.
    """
    report = T.reconcile(0, 0, 0, 0, 0)
    assert report["balanced"] is True
    assert report["accounted_for"] is True
    assert report["curated_matches_valid"] is True
    assert report["every_source_row_explained"] is True
    assert report["duplicates_removed"] == 0


def test_a_bookmarked_read_with_no_new_files_has_no_columns(spark):
    """What Glue actually hands back when a bookmark excludes every file.

    This test exists because the previous version of it was wrong, and the
    wrongness cost a job run. It built an empty DataFrame *with the orders
    schema* and passed, which said nothing useful: the case that occurs in
    production is `create_dynamic_frame.from_catalog(...).toDF()` returning a
    frame with zero rows AND zero columns, because there were no files to infer
    a schema from.

    So `deduplicate()` does not merely return empty - it raises, because
    `order_id` does not exist to window over. That is correct behaviour and is
    asserted here deliberately: the transformations should not be made to
    tolerate a schemaless frame. The entry script is what must notice there is
    no new data and skip the pipeline entirely, and this test is the record of
    why that guard is there.
    """
    schemaless = spark.createDataFrame([], StructType([]))

    assert schemaless.columns == [], "a bookmarked no-op read carries no schema"
    assert schemaless.count() == 0
    assert not schemaless.columns, "the guard the entry script uses"

    with pytest.raises(AnalysisException):
        T.deduplicate(schemaless).count()


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------


def test_contract_supplies_the_values_that_were_hardcoded():
    """The real config/pipeline.json must drive the job, not just exist.

    It sat unread from Phase 0 to Phase 3 while the same values were repeated
    as an f-string, two default arguments and four Terraform job arguments.
    """
    contract = C.load_contract(REPO_ROOT / "config" / "pipeline.json")

    assert C.catalog_tables(contract) == {
        "customers": "customers_raw",
        "products": "products_raw",
        "orders": "orders_raw",
    }
    output = C.curated_output(contract)
    assert output["prefix"] == "curated/sales"
    assert output["partition_by"] == ("year", "month", "day")
    assert output["compression"] == "snappy"
    assert C.quarantine_prefix(contract) == "quarantine/sales"


def test_contract_falls_back_rather_than_crashing_on_an_older_file():
    """A contract missing optional keys must degrade to the documented default.

    The deployed contract and the deployed code are two artifacts that can be
    updated independently, so the code cannot assume it is reading the version
    it shipped with.
    """
    empty: dict = {}
    assert C.catalog_tables(empty)["orders"] == "orders_raw"
    assert C.curated_output(empty)["partition_by"] == ("year", "month", "day")
    assert C.quarantine_prefix(empty) == "quarantine/sales"
    assert C.valid_statuses(empty) == ()
