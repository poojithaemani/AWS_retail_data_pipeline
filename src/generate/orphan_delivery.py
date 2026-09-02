"""Phase 3 failure exercise: a delivery of orders with orphan product ids.

The scenario, which is ordinary rather than exotic: a day's orders arrive
referencing products the product feed has not sent yet. Nothing is malformed -
every field parses, every type is right, the file is valid CSV. The rows are
simply unjoinable.

That is what makes it the right failure to rehearse. It breaks *two* things at
once, and both quietly:

    the join        an inner join drops the row, so revenue comes out lower
                    and no error is raised anywhere
    order_total     there is no price to multiply by, so the figure cannot be
                    computed even in principle

A left join is not the fix. It would keep the row with a null price, producing
a curated record that reads as a completed sale worth nothing - a wrong number
where there was previously a missing one. The pipeline instead rejects orphans
explicitly in `validate()`, with a reason, and reconciles them:

    source rows == curated rows + rejected rows

WHY THIS GOES INTO raw/ AND STAYS THERE
---------------------------------------
Unlike the Phase 2 drift fixture, this delivery belongs in the raw layer.

Raw holds what the source actually sent, and the source really did send these
rows. Diverting them to a fixture prefix would be pretending the bad delivery
did not happen, which is the opposite of what an immutable raw layer is for.

It is an append - a new dated partition - so the immutability control is
respected rather than worked around. It is also permanent, by design: these
rows become Phase 5's quarantine input, and re-deriving curated from raw next
month must reproduce exactly the same rejections. A raw layer you clean up
after an exercise is not a raw layer.

Usage::

    ./scripts/de.sh orphans                build the partition locally
    ./scripts/de.sh orphans --upload       deliver it into raw/orders/
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]

# A day after the existing raw data, so it lands in a partition of its own and
# the before/after comparison is unambiguous.
DELIVERY_DATE = "2026-09-02"
LOCAL_DIR = (
    REPO_ROOT / "data" / "raw" / "csv" / "orders"
    / "year=2026" / "month=09" / "day=02"
)

# Product ids that exist in no products file. P9xxxxx is well outside the
# generator's range, so these cannot collide with real products now or later.
ORPHAN_PRODUCTS = ["P900101", "P900102", "P900103"]

# Customers that DO exist. Only one thing is wrong with these rows, so the
# rejection counts attribute cleanly to a single cause - mixing defects would
# make the reconciliation ambiguous.
KNOWN_CUSTOMERS = ["C0000001", "C0000002", "C0000003", "C0000004", "C0000005"]

ORPHAN_ROWS = 40
CLEAN_ROWS = 60


def build() -> Path:
    """One partition: mostly joinable orders, some orphans.

    Deliberately mixed. A partition of nothing but orphans would be rejected
    wholesale and prove little; the interesting case is a normal delivery in
    which a minority of rows cannot be joined, because that is what silently
    understates revenue rather than obviously failing.
    """
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    path = LOCAL_DIR / "orders.csv"

    rows = []
    counter = 90_000_000

    for index in range(CLEAN_ROWS):
        counter += 1
        rows.append(
            {
                "order_id": f"O{counter:08d}",
                "customer_id": KNOWN_CUSTOMERS[index % len(KNOWN_CUSTOMERS)],
                "product_id": f"P{(index % 200) + 1:06d}",  # a real product
                "quantity": (index % 4) + 1,
                "order_date": f"{DELIVERY_DATE} {8 + index % 12:02d}:15:00",
                "status": "DELIVERED",
            }
        )

    for index in range(ORPHAN_ROWS):
        counter += 1
        rows.append(
            {
                "order_id": f"O{counter:08d}",
                "customer_id": KNOWN_CUSTOMERS[index % len(KNOWN_CUSTOMERS)],
                "product_id": ORPHAN_PRODUCTS[index % len(ORPHAN_PRODUCTS)],
                "quantity": (index % 4) + 1,
                "order_date": f"{DELIVERY_DATE} {8 + index % 12:02d}:45:00",
                "status": "DELIVERED",
            }
        )

    frame = pd.DataFrame(rows).sort_values("order_id", kind="stable")
    frame.to_csv(path, index=False, lineterminator="\n")

    print(f"  wrote {path}")
    print(f"    {len(frame)} orders: {CLEAN_ROWS} joinable, {ORPHAN_ROWS} orphaned")
    print(f"    orphan product ids: {', '.join(ORPHAN_PRODUCTS)}")
    print()
    print("  expected after the ETL runs:")
    print(f"    curated gains  {CLEAN_ROWS} rows")
    print(f"    rejected gains {ORPHAN_ROWS} rows, reason orphan_product_id")
    print(f"    reconciliation stays balanced: {len(frame)} = {CLEAN_ROWS} + {ORPHAN_ROWS}")
    return path


def env(name: str, default: str) -> str:
    for line in (REPO_ROOT / "config" / "project.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(f"export {name}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return default


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upload", action="store_true", help="deliver it into raw/orders/")
    args = parser.parse_args(argv)

    path = build()

    if not args.upload:
        print("\n  --upload not given; nothing sent to S3.")
        return 0

    region = env("AWS_REGION", "us-east-2")
    project = env("PROJECT", "de-training")
    account = subprocess.run(
        ["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    key = f"raw/orders/year=2026/month=09/day=02/orders.csv"
    target = f"s3://{project}-{account}/{key}"

    print(f"\n  delivering to {target}")
    print("  an APPEND to raw/ - a new partition, nothing overwritten and")
    print("  nothing deleted, so the immutability control is respected")
    print("  this stays permanently: raw holds what the source actually sent")
    subprocess.run(
        ["aws", "s3", "cp", str(path), target, "--region", region, "--only-show-errors"],
        check=True,
    )
    print("  delivered. Re-crawl so the new partition is registered, then re-run the job.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
