#!/usr/bin/env python3
"""Generate the synthetic retail dataset used throughout this project.

Why synthetic and not a public dataset: the training brief explicitly rules out
the usual suspects (NYC taxi and friends), and - more importantly - every AWS
resource in this project is destroyed at the end of each session. The raw layer
has to be reconstructible on demand, identically, or the incremental-processing
and data-quality exercises lose their baseline.

Determinism
-----------
Every value derives from ``random.Random(seed)`` streams, one per dataset, so
changing ``--orders`` does not perturb the customers or products. Two runs with
the same arguments on the same day produce byte-identical CSV and JSON. The
manifest records a SHA-256 per file so that can be asserted rather than assumed.

The one input that is not fixed by default is ``--end-date`` (defaults to today
in UTC) so that partitions look current. Pin it with ``--end-date`` for a fully
frozen dataset.

Layout produced under ``--out`` (default ``data/raw/``)::

    data/raw/<format>/customers/customers.<ext>
    data/raw/<format>/products/products.<ext>
    data/raw/<format>/orders/year=YYYY/month=MM/day=DD/orders.<ext>

Clean data lives under ``data/raw/``. Deliberately broken copies live under
``data/test_fixtures/`` and are produced by ``inject_bad_data.py`` - the two
are never mixed, so the clean baseline stays trustworthy.

Usage::

    ./scripts/de.sh gen
    ./scripts/de.sh gen --orders 50000 --formats csv
    ./scripts/de.sh gen --end-date 2026-08-19
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import random
import shutil
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vocab  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]

# Independent seed offsets keep the three datasets from sharing an RNG stream.
SEED_OFFSETS = {"customers": 1, "products": 2, "orders": 3}

EXTENSIONS = {"csv": "csv", "json": "json", "parquet": "parquet"}


# --------------------------------------------------------------------------
# weighted choice
# --------------------------------------------------------------------------


class Weighted:
    """Deterministic weighted picker.

    ``random.choices`` would work, but its internal algorithm is an
    implementation detail that could change between CPython releases. A
    cumulative-weight bisect is stable forever, which is the whole point.
    """

    def __init__(self, pairs: list[tuple[str, int]]) -> None:
        self.values = [v for v, _ in pairs]
        self.cumulative: list[int] = []
        total = 0
        for _, weight in pairs:
            total += weight
            self.cumulative.append(total)
        self.total = total

    def pick(self, rng: random.Random) -> str:
        index = bisect.bisect_right(self.cumulative, rng.randrange(self.total))
        return self.values[index]


COUNTRY_PICKER = Weighted(vocab.COUNTRIES)
STATUS_PICKER = Weighted(vocab.ORDER_STATUSES)


# --------------------------------------------------------------------------
# generators
# --------------------------------------------------------------------------


def make_customers(n: int, seed: int, end: date) -> pd.DataFrame:
    rng = random.Random(seed + SEED_OFFSETS["customers"])
    start = end - timedelta(days=730)  # two years of signups
    span = (end - start).days

    rows = []
    for i in range(1, n + 1):
        first = rng.choice(vocab.FIRST_NAMES)
        last = rng.choice(vocab.LAST_NAMES)
        domain = rng.choice(vocab.EMAIL_DOMAINS)
        rows.append(
            {
                "customer_id": f"C{i:07d}",
                "customer_name": f"{first} {last}",
                # Suffixed with the id so emails stay unique across 100k rows.
                # Accidental duplicate emails would mask the real duplicates
                # that the deduplication exercise is supposed to find.
                "email": f"{first.lower()}.{last.lower()}{i}@{domain}",
                "country": COUNTRY_PICKER.pick(rng),
                "created_date": (start + timedelta(days=rng.randrange(span))).isoformat(),
            }
        )
    return pd.DataFrame(rows)


def make_products(n: int, seed: int) -> pd.DataFrame:
    rng = random.Random(seed + SEED_OFFSETS["products"])

    rows = []
    for i in range(1, n + 1):
        category = rng.choice(vocab.CATEGORIES)
        adjective = rng.choice(vocab.PRODUCT_ADJECTIVES)
        noun = rng.choice(vocab.PRODUCT_NOUNS[category])
        low, high = vocab.CATEGORY_PRICE_BANDS[category]
        rows.append(
            {
                "product_id": f"P{i:06d}",
                "product_name": f"{adjective} {noun} {i}",
                "category": category,
                "price": round(rng.uniform(low, high), 2),
            }
        )
    return pd.DataFrame(rows)


def make_orders(
    n: int, seed: int, n_customers: int, n_products: int, end: date, days: int
) -> pd.DataFrame:
    rng = random.Random(seed + SEED_OFFSETS["orders"])
    start = end - timedelta(days=days - 1)

    # Order volume varies across the window rather than sitting flat, so
    # partition sizes differ and partition pruning has something to prune.
    day_weights = Weighted([(str(d), 50 + (d * 3) % 47) for d in range(days)])

    rows = []
    for i in range(1, n + 1):
        offset = int(day_weights.pick(rng))
        order_day = start + timedelta(days=offset)
        seconds = rng.randrange(86400)
        stamp = datetime.combine(order_day, datetime.min.time()) + timedelta(seconds=seconds)
        rows.append(
            {
                "order_id": f"O{i:08d}",
                "customer_id": f"C{rng.randrange(1, n_customers + 1):07d}",
                "product_id": f"P{rng.randrange(1, n_products + 1):06d}",
                "quantity": rng.randint(1, 5),
                "order_date": stamp.strftime("%Y-%m-%d %H:%M:%S"),
                "status": STATUS_PICKER.pick(rng),
            }
        )

    frame = pd.DataFrame(rows)
    # Sorting by order_id makes output stable regardless of how pandas happens
    # to order the partition groups.
    return frame.sort_values("order_id", kind="stable").reset_index(drop=True)


# --------------------------------------------------------------------------
# writers
# --------------------------------------------------------------------------


def write_frame(frame: pd.DataFrame, path: Path, fmt: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "csv":
        # lineterminator is pinned: pandas otherwise follows the platform,
        # which would make Windows and Linux output differ byte for byte.
        frame.to_csv(path, index=False, lineterminator="\n")
    elif fmt == "json":
        # JSON Lines, one object per row - what a Glue crawler expects.
        frame.to_json(path, orient="records", lines=True, date_format="iso")
    elif fmt == "parquet":
        frame.to_parquet(path, index=False, engine="pyarrow", compression="snappy")
    else:
        raise ValueError(f"unsupported format: {fmt}")
    return path


def write_partitioned_orders(frame: pd.DataFrame, root: Path, fmt: str) -> list[Path]:
    """Write orders under Hive-style year=/month=/day= directories.

    Partition values live in the directory names only, not as columns inside
    the files. That is what the Glue crawler and Athena both expect, and
    duplicating them inside every row wastes storage for no benefit.
    """
    ext = EXTENSIONS[fmt]
    parts = pd.to_datetime(frame["order_date"]).dt

    stamped = frame.assign(
        part_year=parts.strftime("%Y"),
        part_month=parts.strftime("%m"),
        part_day=parts.strftime("%d"),
    )
    partition_cols = ["part_year", "part_month", "part_day"]

    written = []
    for (year, month, day), group in stamped.groupby(partition_cols, sort=True):
        target = root / f"year={year}" / f"month={month}" / f"day={day}" / f"orders.{ext}"
        written.append(write_frame(group.drop(columns=partition_cols), target, fmt))
    return written


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------
# entrypoint
# --------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the synthetic retail dataset.")
    parser.add_argument(
        "--seed", type=int, default=20260819, help="identical seeds produce identical data"
    )
    # Development-scale defaults. The training-scale figures from the brief
    # (100k / 5k / 1M) are passed explicitly when the Spark scaling exercise
    # is reached - the safe size is the one you get by accident.
    parser.add_argument("--customers", type=int, default=1_000)
    parser.add_argument("--products", type=int, default=200)
    parser.add_argument("--orders", type=int, default=10_000)
    parser.add_argument(
        "--days", type=int, default=30, help="number of daily order partitions to spread across"
    )
    parser.add_argument(
        "--end-date", default=None, help="last order date, YYYY-MM-DD (default: today UTC)"
    )
    parser.add_argument(
        "--formats", default="csv,json,parquet", help="comma-separated subset of csv,json,parquet"
    )
    parser.add_argument("--out", default=str(REPO_ROOT / "data" / "raw"))
    parser.add_argument(
        "--clean", action="store_true", help="delete the output directory before generating"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    end = (
        date.fromisoformat(args.end_date)
        if args.end_date
        else datetime.now(timezone.utc).date()
    )
    formats = [f.strip() for f in args.formats.split(",") if f.strip()]
    for fmt in formats:
        if fmt not in EXTENSIONS:
            raise SystemExit(f"unknown format: {fmt}")

    out = Path(args.out)
    if args.clean and out.exists():
        shutil.rmtree(out)

    print(f"seed={args.seed} end_date={end} days={args.days} formats={','.join(formats)}")

    print(f"  customers ... {args.customers:,}")
    customers = make_customers(args.customers, args.seed, end)
    print(f"  products  ... {args.products:,}")
    products = make_products(args.products, args.seed)
    print(f"  orders    ... {args.orders:,}")
    orders = make_orders(args.orders, args.seed, args.customers, args.products, end, args.days)

    checksums: dict[str, str] = {}
    manifest = {
        "seed": args.seed,
        "end_date": end.isoformat(),
        "days": args.days,
        "row_counts": {
            "customers": len(customers),
            "products": len(products),
            "orders": len(orders),
        },
        "files": checksums,
    }

    for fmt in formats:
        ext = EXTENSIONS[fmt]
        base = out / fmt
        paths = [
            write_frame(customers, base / "customers" / f"customers.{ext}", fmt),
            write_frame(products, base / "products" / f"products.{ext}", fmt),
        ]
        paths += write_partitioned_orders(orders, base / "orders", fmt)

        total = sum(p.stat().st_size for p in paths)
        print(f"  {fmt:<8} {len(paths):>4} files  {total / 1e6:8.2f} MB")

        # One unpartitioned Parquet file alongside the partitioned tree.
        #
        # This is the control for the Phase 1 benchmark: comparing partitioned
        # Parquet against CSV measures columnar storage and partition pruning
        # at the same time, and reporting that single number credits Parquet
        # with a saving that partitioning produced. The flat copy separates
        # them.
        #
        # Written here rather than by Athena CTAS because the workgroup sets
        # enforce_workgroup_configuration, which forbids the external_location
        # a CTAS would need. Weakening that setting to make the benchmark
        # easier would remove the per-query scan ceiling it exists to enforce.
        if fmt == "parquet":
            flat = out / "parquet_flat" / "orders" / "orders.parquet"
            flat_frame = orders.copy()
            stamp = pd.to_datetime(flat_frame["order_date"])
            # Partition values become ordinary columns: still filterable, but
            # with no directories to skip, which is the point of the control.
            flat_frame["year"] = stamp.dt.strftime("%Y")
            flat_frame["month"] = stamp.dt.strftime("%m")
            flat_frame["day"] = stamp.dt.strftime("%d")
            write_frame(flat_frame, flat, "parquet")
            print(
                f"  {'flat':<8} {1:>4} files  {flat.stat().st_size / 1e6:8.2f} MB"
                "  (unpartitioned control)"
            )

        # Parquet embeds writer metadata, so its bytes can shift with a library
        # upgrade. Checksums are recorded for the text formats, which are the
        # ones the reproducibility check asserts on.
        if fmt in ("csv", "json"):
            for path in sorted(paths):
                key = str(path.relative_to(out)).replace("\\", "/")
                checksums[key] = sha256(path)

    manifest_path = out / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    print(f"  manifest  -> {manifest_path}")
    print(f"  order partitions: {orders['order_date'].str[:10].nunique()} days")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
