"""Tests for the synthetic dataset generator and the defect injector.

The generator is load-bearing: every AWS resource is destroyed between
sessions, so the raw layer is rebuilt from this code each time. If it drifts,
every downstream baseline — row counts, bookmark behaviour, data quality
expectations — drifts with it silently. These tests exist to make that loud.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src" / "generate"))

import generate_retail_data as gen  # noqa: E402
import inject_bad_data as inject  # noqa: E402

# Small but structurally identical to the real dataset: enough partitions and
# enough rows for every assertion below, fast enough to run on every commit.
SMALL = [
    "--customers", "300",
    "--products", "80",
    "--orders", "1500",
    "--days", "4",
    "--end-date", "2026-08-19",
]


@pytest.fixture(scope="module")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("dataset")
    gen.main([*SMALL, "--out", str(out)])
    return out


def test_row_counts_match_arguments(dataset: Path) -> None:
    manifest = json.loads((dataset / "manifest.json").read_text())
    assert manifest["row_counts"] == {"customers": 300, "products": 80, "orders": 1500}


def test_generation_is_reproducible(tmp_path: Path) -> None:
    """Same arguments, same bytes. This is the whole contract."""
    first, second = tmp_path / "a", tmp_path / "b"
    gen.main([*SMALL, "--out", str(first)])
    gen.main([*SMALL, "--out", str(second)])

    manifest_a = json.loads((first / "manifest.json").read_text())
    manifest_b = json.loads((second / "manifest.json").read_text())
    assert manifest_a["files"] == manifest_b["files"]
    assert manifest_a["files"], "no checksums were recorded"


def test_changing_order_count_leaves_customers_untouched(tmp_path: Path) -> None:
    """Separate RNG streams per dataset.

    Without this, tuning --orders would silently rewrite every customer row and
    invalidate any comparison against a previous run.
    """
    base, more = tmp_path / "base", tmp_path / "more"
    gen.main([*SMALL, "--out", str(base), "--formats", "csv"])

    # Same arguments except a different order volume.
    variant = [*SMALL]
    variant[variant.index("--orders") + 1] = "3000"
    gen.main([*variant, "--out", str(more), "--formats", "csv"])

    left = (base / "csv" / "customers" / "customers.csv").read_bytes()
    right = (more / "csv" / "customers" / "customers.csv").read_bytes()
    assert left == right


@pytest.mark.parametrize(
    ("dataset_name", "columns"),
    [
        ("customers", ["customer_id", "customer_name", "email", "country", "created_date"]),
        ("products", ["product_id", "product_name", "category", "price"]),
    ],
)
def test_schema_matches_the_brief(dataset: Path, dataset_name: str, columns: list[str]) -> None:
    frame = pd.read_csv(dataset / "csv" / dataset_name / f"{dataset_name}.csv")
    assert list(frame.columns) == columns


def test_orders_schema_and_partition_layout(dataset: Path) -> None:
    files = sorted((dataset / "csv" / "orders").glob("year=*/month=*/day=*/orders.csv"))
    assert len(files) == 4, "expected one partition directory per day"

    frame = pd.read_csv(files[0])
    assert list(frame.columns) == [
        "order_id", "customer_id", "product_id", "quantity", "order_date", "status",
    ]

    # Partition values must live in the path only. Repeating them inside every
    # row wastes storage and lets the two disagree.
    assert not {"year", "month", "day"} & set(frame.columns)


def test_partition_path_agrees_with_row_contents(dataset: Path) -> None:
    for path in (dataset / "csv" / "orders").glob("year=*/month=*/day=*/orders.csv"):
        year, month, day = (part.split("=")[1] for part in path.parts[-4:-1])
        frame = pd.read_csv(path)
        dates = pd.to_datetime(frame["order_date"]).dt.strftime("%Y-%m-%d")
        assert set(dates) == {f"{year}-{month}-{day}"}


def test_identifiers_are_unique_before_corruption(dataset: Path) -> None:
    customers = pd.read_csv(dataset / "csv" / "customers" / "customers.csv")
    assert customers["customer_id"].is_unique
    assert customers["email"].is_unique, "duplicate emails would mask real dedup failures"

    orders = pd.concat(
        pd.read_csv(p) for p in (dataset / "csv" / "orders").glob("year=*/*/*/orders.csv")
    )
    assert orders["order_id"].is_unique


def test_all_formats_carry_the_same_data(dataset: Path) -> None:
    csv = pd.read_csv(dataset / "csv" / "products" / "products.csv")
    parquet = pd.read_parquet(dataset / "parquet" / "products" / "products.parquet")
    lines = pd.read_json(dataset / "json" / "products" / "products.json", lines=True)

    pd.testing.assert_frame_equal(csv, parquet)
    assert list(lines["product_id"]) == list(csv["product_id"])


def test_prices_are_positive_and_categorised(dataset: Path) -> None:
    products = pd.read_csv(dataset / "csv" / "products" / "products.csv")
    assert (products["price"] > 0).all()
    assert products["category"].nunique() > 1


# --------------------------------------------------------------------------
# defect injection
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def dirty(dataset: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("dirty")
    inject.main(["--in", str(dataset / "csv"), "--out", str(out)])
    return out


def test_every_defect_class_is_injected(dirty: Path) -> None:
    manifest = json.loads((dirty / "defects.json").read_text())
    assert set(manifest["summary"]) == {
        "null_customer_id",
        "duplicate_order_id",
        "negative_quantity",
        "orphan_customer_id",
        "orphan_product_id",
        "malformed_order_date",
        "invalid_order_status",
        "unknown_price",
        "negative_price",
    }
    assert all(count > 0 for count in manifest["summary"].values())


def test_ground_truth_matches_what_is_on_disk(dirty: Path) -> None:
    """The manifest is what Phase 5 asserts against, so it must not lie."""
    manifest = json.loads((dirty / "defects.json").read_text())
    orders = pd.concat(
        pd.read_csv(p, dtype=str)
        for p in (dirty).glob("orders/year=*/month=*/day=*/orders.csv")
    )

    expected_nulls = sum(1 for d in manifest["defects"] if d["defect"] == "null_customer_id")
    assert orders["customer_id"].isna().sum() == expected_nulls

    expected_dupes = sum(1 for d in manifest["defects"] if d["defect"] == "duplicate_order_id")
    assert orders["order_id"].duplicated().sum() == expected_dupes

    negatives = orders["quantity"].astype(int) < 0
    expected_negatives = sum(1 for d in manifest["defects"] if d["defect"] == "negative_quantity")
    assert negatives.sum() == expected_negatives


def test_invalid_status_is_a_plausible_new_enum_value(dirty: Path) -> None:
    """Not a typo - a value an upstream system might genuinely start sending.

    That is the failure mode worth rehearsing: it passes every type check and
    every null check, and only a value-domain rule catches it.
    """
    orders = pd.concat(
        pd.read_csv(p, dtype=str)
        for p in dirty.glob("orders/year=*/month=*/day=*/orders.csv")
    )
    known = {"DELIVERED", "SHIPPED", "PENDING", "CANCELLED", "RETURNED"}
    unexpected = set(orders["status"]) - known
    assert unexpected == {"PROCESSING"}, unexpected


def test_orphan_product_ids_break_the_price_join(dirty: Path) -> None:
    """No product row means no price, so order_total cannot be computed."""
    orders = pd.concat(
        pd.read_csv(p, dtype=str)
        for p in dirty.glob("orders/year=*/month=*/day=*/orders.csv")
    )
    products = set(pd.read_csv(dirty / "products" / "products.csv", dtype=str)["product_id"])
    orphans = set(orders["product_id"]) - products
    assert orphans == {"P999999"}, orphans


def test_unknown_price_breaks_numeric_typing(dirty: Path) -> None:
    """This is the exact condition that makes a Glue crawler retype the column."""
    products = pd.read_csv(dirty / "products" / "products.csv", dtype=str)
    assert (products["price"] == "UNKNOWN").any()
    with pytest.raises(ValueError):
        products["price"].astype(float)


def test_duplicate_orders_conflict_rather_than_repeat(dirty: Path) -> None:
    """Duplicates carry differing quantities on purpose.

    An exact-copy duplicate is removed by any naive `distinct`. A conflicting
    duplicate forces a deliberate choice of which record wins, which is the
    thing worth demonstrating.
    """
    orders = pd.concat(
        pd.read_csv(p, dtype=str)
        for p in (dirty).glob("orders/year=*/month=*/day=*/orders.csv")
    )
    duplicated = orders[orders["order_id"].duplicated(keep=False)]
    conflicting = duplicated.groupby("order_id")["quantity"].nunique()
    assert (conflicting > 1).any(), "no conflicting duplicates were produced"


def test_customers_are_left_clean(dataset: Path, dirty: Path) -> None:
    original = (dataset / "csv" / "customers" / "customers.csv").read_bytes()
    assert (dirty / "customers" / "customers.csv").read_bytes() == original


# --------------------------------------------------------------------------
# shell entrypoints
# --------------------------------------------------------------------------


def _posix_bash() -> str | None:
    """Locate a real POSIX bash.

    On Windows, plain `bash` on PATH is usually the WSL shim, which cannot see
    the Windows filesystem the way this repo needs. Git for Windows ships the
    bash these scripts actually run under, so prefer it explicitly.
    """
    candidates = [
        Path("C:/Program Files/Git/bin/bash.exe"),
        Path("C:/Program Files (x86)/Git/bin/bash.exe"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    found = shutil.which("bash")
    return found if found and "System32" not in found else None


@pytest.mark.parametrize(
    "script",
    ["de.sh", "lib.sh", "verify_teardown.sh", "load_raw.sh", "empty_versioned_bucket.sh"],
)
def test_shell_scripts_parse(script: str) -> None:
    """Catches the `[ test ] && action` class of set -e bug before it runs live."""
    bash = _posix_bash()
    if bash is None:
        pytest.skip("no POSIX bash available")
    result = subprocess.run(
        [bash, "-n", str(REPO_ROOT / "scripts" / script)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
