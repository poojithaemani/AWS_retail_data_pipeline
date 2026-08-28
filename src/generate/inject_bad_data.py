#!/usr/bin/env python3
"""Inject deliberate defects into the generated dataset.

The training brief asks for failures to be introduced on purpose and then
found. Doing that by hand-editing a CSV makes the result unrepeatable and,
worse, unverifiable - you end up asserting that the pipeline "found some bad
rows" instead of asserting that it found *exactly the rows that were broken*.

So every corruption is recorded in ``defects.json`` alongside the output. That
file is the ground truth the Data Quality phase asserts against:

    expected_quarantined == actually_quarantined

Defect classes, each mapped to the exercise that consumes it:

    null_customer_id       orders   Phase 5  completeness rule
    duplicate_order_id     orders   Phase 5  uniqueness rule / dedup logic
    negative_quantity      orders   Phase 5  range rule
    orphan_customer_id     orders   Phase 3  join behaviour, referential check
    orphan_product_id      orders   Phase 3  join behaviour; also breaks the
                                             order_total calculation, since
                                             there is no price to multiply by
    malformed_order_date   orders   Phase 3  type conversion / date parsing
    invalid_order_status   orders   Phase 5  an upstream system starts
                                             emitting a status the pipeline
                                             has never seen
    unknown_price          products Phase 2  schema inference - the crawler
                                             retypes price from double to
                                             string and downstream breaks
    negative_price         products Phase 5  produces a negative order_total

The clean dataset is never modified in place: output goes to a separate tree so
the raw layer stays reproducible and the two can be diffed.

Usage::

    ./scripts/de.sh gen
    py -3 src/generate/inject_bad_data.py
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFECT_SEED_OFFSET = 97


def _orders_files(root: Path) -> list[Path]:
    return sorted(root.glob("orders/year=*/month=*/day=*/orders.csv"))


def corrupt_orders(root: Path, out: Path, rng: random.Random, count: int) -> list[dict]:
    """Spread order defects across partitions, recording each one."""
    files = _orders_files(root)
    if not files:
        raise SystemExit(f"no order partitions found under {root}")

    defects: list[dict] = []
    # Concentrate defects in the most recent partitions: that is where new,
    # unvalidated data actually arrives in a real pipeline.
    targets = files[-min(len(files), 5) :]
    per_file = max(1, count // (len(targets) * 4))

    for path in targets:
        frame = pd.read_csv(path, dtype=str)
        relative = str(path.relative_to(root)).replace("\\", "/")
        destination = out / path.relative_to(root)
        destination.parent.mkdir(parents=True, exist_ok=True)

        # Every defect row is drawn from one disjoint pool, so no row ever
        # carries two defects. That keeps the ground truth unambiguous: a
        # quarantined row has exactly one reason, and the expected count per
        # rule can be compared directly against what the pipeline reports.
        half = max(1, per_file // 2)
        wanted = per_file * 3 + half * 4
        pool = rng.sample(range(len(frame)), min(wanted, len(frame)))
        cursor = 0

        def take(n: int) -> list[int]:
            nonlocal cursor
            chunk = pool[cursor : cursor + n]
            cursor += len(chunk)
            return chunk

        def record(defect: str, order_id: str) -> None:
            defects.append({"defect": defect, "file": relative, "order_id": order_id})

        # 1. NULL customer_id - completeness
        for i in take(per_file):
            record("null_customer_id", frame.at[i, "order_id"])
            frame.at[i, "customer_id"] = None

        # 2. Negative quantity - range violation
        for i in take(per_file):
            record("negative_quantity", frame.at[i, "order_id"])
            frame.at[i, "quantity"] = str(-abs(int(frame.at[i, "quantity"])))

        # 3. Orphan customer_id - referential integrity. The join in the
        #    transformation phase must not silently drop or duplicate these.
        for i in take(half):
            record("orphan_customer_id", frame.at[i, "order_id"])
            frame.at[i, "customer_id"] = "C9999999"

        # 4. Orphan product_id - referential integrity on the other join.
        #    Worse than an orphan customer: with no product there is no
        #    price, so order_total cannot be computed at all.
        for i in take(half):
            record("orphan_product_id", frame.at[i, "order_id"])
            frame.at[i, "product_id"] = "P999999"

        # 5. Malformed order_date - type conversion
        for i in take(half):
            record("malformed_order_date", frame.at[i, "order_id"])
            frame.at[i, "order_date"] = "19-08-2026 25:61:00"

        # 6. Invalid order status. Not a typo but a plausible new enum
        #    value from an upstream system - the failure mode that breaks
        #    pipelines quietly, because it looks like valid data.
        for i in take(half):
            record("invalid_order_status", frame.at[i, "order_id"])
            frame.at[i, "status"] = "PROCESSING"

        # 7. Duplicate order_id - appended rather than overwritten, so the row
        #    count grows and deduplication has something real to collapse. The
        #    copy carries a different quantity, which makes a naive
        #    drop_duplicates() insufficient and forces a deliberate choice of
        #    which record wins.
        duplicates = []
        for i in take(per_file):
            row = frame.loc[i].copy()
            original = int(row["quantity"])
            row["quantity"] = str(original % 5 + 1)  # guaranteed to differ
            duplicates.append(row)
            record("duplicate_order_id", str(row["order_id"]))
        if duplicates:
            frame = pd.concat([frame, pd.DataFrame(duplicates)], ignore_index=True)

        frame.to_csv(destination, index=False, lineterminator="\n")

    return defects



def corrupt_products(root: Path, out: Path, rng: random.Random, count: int) -> list[dict]:
    """Break the products price column in two different ways."""
    source = root / "products" / "products.csv"
    if not source.exists():
        raise SystemExit(f"missing {source}")

    frame = pd.read_csv(source, dtype=str)
    defects: list[dict] = []

    # UNKNOWN prices: exactly the case in the brief. A Glue crawler infers
    # price as double on the clean file and as string once these appear, which
    # is the schema-evolution failure the catalog phase troubleshoots.
    for i in rng.sample(range(len(frame)), min(count, len(frame))):
        defects.append({"defect": "unknown_price", "product_id": frame.at[i, "product_id"]})
        frame.at[i, "price"] = "UNKNOWN"

    for i in rng.sample(range(len(frame)), min(max(1, count // 2), len(frame))):
        if frame.at[i, "price"] == "UNKNOWN":
            continue
        defects.append({"defect": "negative_price", "product_id": frame.at[i, "product_id"]})
        frame.at[i, "price"] = str(-abs(float(frame.at[i, "price"])))

    destination = out / "products" / "products.csv"
    destination.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(destination, index=False, lineterminator="\n")
    return defects


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inject deliberate defects for DQ exercises.")
    parser.add_argument(
        "--in", dest="source", default=str(REPO_ROOT / "data" / "raw" / "csv")
    )
    parser.add_argument(
        "--out", dest="target", default=str(REPO_ROOT / "data" / "test_fixtures")
    )
    parser.add_argument("--seed", type=int, default=20260819)
    parser.add_argument(
        "--orders-defects", type=int, default=200, help="approximate order defects to inject"
    )
    parser.add_argument("--product-defects", type=int, default=25)
    parser.add_argument("--clean", action="store_true", help="wipe the output tree first")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    source, target = Path(args.source), Path(args.target)

    if not source.exists():
        raise SystemExit(f"{source} not found - run './scripts/de.sh gen' first")
    if args.clean and target.exists():
        shutil.rmtree(target)

    rng = random.Random(args.seed + DEFECT_SEED_OFFSET)

    # Customers pass through untouched: keeping one clean dimension makes it
    # obvious that failures come from orders and products, not from everywhere.
    customers_out = target / "customers" / "customers.csv"
    customers_out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source / "customers" / "customers.csv", customers_out)

    defects = corrupt_orders(source, target, rng, args.orders_defects)
    defects += corrupt_products(source, target, rng, args.product_defects)

    summary: dict[str, int] = {}
    for entry in defects:
        summary[entry["defect"]] = summary.get(entry["defect"], 0) + 1

    manifest = {"seed": args.seed, "summary": summary, "defects": defects}
    manifest_path = target / "defects.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))

    print(f"defects written to {target}")
    for name in sorted(summary):
        print(f"  {name:<24} {summary[name]:>5}")
    print(f"  {'TOTAL':<24} {len(defects):>5}")
    print(f"  ground truth -> {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
