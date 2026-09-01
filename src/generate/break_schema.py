#!/usr/bin/env python3
"""Phase 2: reproduce schema drift without touching the clean raw dataset.

The brief (p.7) asks for `price = UNKNOWN` in products, a re-crawl, and an
observation of what Glue does about it.

This does **not** put that value anywhere near `raw/`. Two reasons, and the
second matters more than the first:

1. `raw/` is immutable by enforced policy, so a file added there could not
   afterwards be removed without break-glass credentials.
2. More importantly, the clean Phase 1 dataset is the baseline every later
   phase reconciles against. Contaminating it to demonstrate a concept would
   trade a permanent asset for a temporary one.

Instead the fixture is a **copy** of products, plus the bad delivery, written
to `fixtures/products_drift/` - outside the raw layer, mutable, and deletable
with ordinary permissions. The crawler has that prefix as a target, so a table
appears the moment the fixture lands and disappears when it is cleared.

The comparison is then side by side rather than before and after:

    products_raw     price  double     the clean dataset
    products_drift   price  string     the same data plus one bad row

which is stronger evidence than a single table mutating, because both schemas
exist simultaneously and can be queried against each other.

Glue infers the narrowest type that fits every value it sampled. One
non-numeric token in one row of one file widens the whole column to string,
and every downstream `sum(price * quantity)` then fails or silently returns
nothing.

Usage::

    ./scripts/de.sh breakschema             build the fixture locally
    ./scripts/de.sh breakschema --upload    build and deliver it
    ./scripts/de.sh breakschema --restore   remove it from S3 again
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL_DIR = REPO_ROOT / "data" / "test_fixtures" / "products_drift"
CLEAN_COPY = "products.csv"
DELIVERY = "products_20260901.csv"
S3_PREFIX = "fixtures/products_drift"

# Product ids well above anything the generator produces, so these are new
# rows rather than conflicting duplicates of existing ones. The exercise is
# about type inference, not deduplication - mixing the two would make the
# result ambiguous.
ROWS = [
    {"product_id": "P900001", "product_name": "Refurbished Desk Lamp", "category": "Home & Kitchen", "price": "10.50"},
    {"product_id": "P900002", "product_name": "Clearance Yoga Mat", "category": "Sports & Outdoors", "price": "25.00"},
    # The row that does the damage. A single token.
    {"product_id": "P900003", "product_name": "Pending Price Headphones", "category": "Electronics", "price": "UNKNOWN"},
    {"product_id": "P900004", "product_name": "Discounted Cookbook", "category": "Books", "price": "42.00"},
]


def env(name: str, default: str) -> str:
    for line in (REPO_ROOT / "config" / "project.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(f"export {name}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return default


def lake_bucket() -> str:
    account = subprocess.run(
        ["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return f"{env('PROJECT', 'de-training')}-{account}"


def build_fixture() -> Path:
    """A copy of the clean products data, plus the one bad row.

    The copy matters. Crawling a prefix that held only four rows would infer a
    schema from four rows, and the drift would be indistinguishable from simply
    having different data. The fixture is the real dataset with one value
    changed, so the *only* variable is that value.
    """
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)

    clean_source = REPO_ROOT / "data" / "raw" / "csv" / "products" / "products.csv"
    if not clean_source.exists():
        raise SystemExit(
            f"clean products data not found at {clean_source}\n"
            "run ./scripts/de.sh gen first - this script copies it, never edits it"
        )

    clean = pd.read_csv(clean_source, dtype=str)
    clean.to_csv(LOCAL_DIR / CLEAN_COPY, index=False, lineterminator="\n")

    bad = pd.DataFrame(ROWS)
    bad.to_csv(LOCAL_DIR / DELIVERY, index=False, lineterminator="\n")

    print(f"  {LOCAL_DIR / CLEAN_COPY}")
    print(f"    {len(clean)} clean rows, copied - the original is untouched")
    print(f"  {LOCAL_DIR / DELIVERY}")
    print(f"    {len(bad)} rows, one carrying price = UNKNOWN")
    return LOCAL_DIR


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upload", action="store_true", help="deliver the fixture to S3")
    parser.add_argument("--restore", action="store_true", help="remove the fixture from S3")
    args = parser.parse_args(argv)

    region = env("AWS_REGION", "us-east-2")

    if args.restore:
        target = f"s3://{lake_bucket()}/{S3_PREFIX}/"
        print(f"  clearing {target}")
        print("  outside raw/, so this is an ordinary delete - no elevated")
        print("  permission is needed and the raw-layer protection is untouched")
        subprocess.run(
            ["aws", "s3", "rm", target, "--recursive", "--region", region, "--only-show-errors"],
            check=True,
        )

        # Clearing S3 is not enough, and the tables cannot be removed by name.
        #
        # Two reasons. DeleteBehavior = DEPRECATE_IN_DATABASE means a re-crawl
        # of an emptied prefix deprecates rather than removes, so the tables
        # would linger. And - the part a hardcoded name would have missed - the
        # crawler did not create one table for this prefix. The two files have
        # incompatible schemas, so it created one table PER FILE:
        #
        #     products_csv            from products.csv          price double
        #     products_20260901_csv   from products_20260901.csv  col0..col3
        #     products_drift          from an earlier run
        #
        # Schema drift causes table proliferation, and the generated names
        # depend on whichever filenames happened to arrive. So cleanup matches
        # on LOCATION - anything under fixtures/ - which is a property we
        # control, rather than on names the crawler invents.
        import boto3

        glue = boto3.client("glue", region_name=region)
        database = env("GLUE_DATABASE", "training_db")
        root = S3_PREFIX.split("/")[0]
        print(f"\n  removing catalog tables located under {root}/")

        removed = 0
        for page in glue.get_paginator("get_tables").paginate(DatabaseName=database):
            for table in page.get("TableList", []):
                location = table.get("StorageDescriptor", {}).get("Location", "")
                if f"/{root}/" not in location:
                    continue
                glue.delete_table(DatabaseName=database, Name=table["Name"])
                print(f"    removed {table['Name']}")
                removed += 1

        print(f"    {removed} table(s) removed" if removed else "    none found")
        print("  catalog metadata only - no S3 object affected")

        print("\n  restored. Verify with: ./scripts/de.sh publish --check")
        return 0

    build_fixture()

    if not args.upload:
        print("\n  --upload not given; nothing sent to S3.")
        return 0

    target = f"s3://{lake_bucket()}/{S3_PREFIX}/"
    print(f"\n  delivering to {target}")
    print("  note the prefix: fixtures/, not raw/. The clean dataset is not")
    print("  modified, and cleanup later needs no special permission.")
    subprocess.run(
        ["aws", "s3", "sync", str(LOCAL_DIR), target, "--region", region, "--only-show-errors"],
        check=True,
    )
    print("  delivered. Re-run the crawler to see what it infers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
