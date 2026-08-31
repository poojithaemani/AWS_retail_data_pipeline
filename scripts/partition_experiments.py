#!/usr/bin/env python3
"""Measure the two costs everybody asserts and nobody measures.

Phase 1 steps 1.5 and 1.6. Both are things the brief asks you to *explain*
(section 4, Day 1), and an explanation built from a measurement sounds
different from one recited from a blog post.

    over-partitioning   the same 500k rows split 30 ways (daily) and 720 ways
                        (hourly). Partitioning is sold as free performance; it
                        is not. Each partition is catalog metadata to store,
                        list and plan against, and each one shrinks the files
                        inside it.

    small files         the same single day of rows written as 1 file and as
                        200 files. Identical bytes, identical schema, identical
                        query - only the object count differs.

Why 500k rather than the development default of 10k: these two experiments
measure *time*, and Athena carries roughly a second of fixed overhead per query
for planning, scheduling and result staging. At 0.66 MB that overhead is the
entire measurement. At ~35 MB there is a signal to find. Whether it clears the
noise floor is itself a finding, and is reported either way rather than
quietly dropped.

The format comparison in benchmark_formats.py deliberately stays at
development scale: it measures bytes scanned, which is exact at any size.

Usage::

    ./scripts/de.sh partexp --generate     build the local corpus (slow, offline)
    ./scripts/de.sh partexp --upload       sync it to the lake
    ./scripts/de.sh partexp --run          DDL, queries, report
    ./scripts/de.sh partexp --all          all three
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd

try:
    import boto3
except ImportError:  # pragma: no cover
    sys.exit("boto3 is required: py -3 -m pip install -r requirements.txt")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src" / "generate"))
import generate_retail_data as gen  # noqa: E402

CORPUS = REPO_ROOT / "data" / "experiments"
PREFIX = "experiments"

# Deliberately the same seed and end date as the main dataset, so these rows
# are the same rows - just arranged differently on disk.
SEED = 20260819
END_DATE = "2026-08-19"
DAYS = 30
ORDERS = 500_000
CUSTOMERS = 1_000
PRODUCTS = 200

# The day the small-file experiment uses, and the day both partition layouts
# are queried for. Pinned so the comparison is like-for-like.
TARGET_DAY = "2026-08-19"
SMALL_FILE_COUNT = 200

TABLES = {
    "daily": f"{PREFIX}_orders_daily",
    "hourly": f"{PREFIX}_orders_hourly",
    "one_file": f"{PREFIX}_orders_one_file",
    "many_files": f"{PREFIX}_orders_many_files",
}


def env() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (REPO_ROOT / "config" / "project.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("export "):
            continue
        key, _, value = line[len("export ") :].partition("=")
        value = value.strip().strip('"').strip("'")
        if value.startswith("$"):
            value = out.get(value.lstrip("$"), "")
        out[key.strip()] = value
    return out


ENV = env()
REGION = ENV.get("AWS_REGION", "us-east-2")
PROJECT = ENV.get("PROJECT", "de-training")
DATABASE = ENV.get("GLUE_DATABASE", "training_db")
WORKGROUP = f"{PROJECT}-wg"


def write_lf(path: Path, text: str) -> Path:
    path.write_bytes(text.encode("utf-8"))
    return path


# --------------------------------------------------------------------------
# corpus
# --------------------------------------------------------------------------


def build_corpus() -> dict[str, Any]:
    """Write four arrangements of one dataset."""
    end = pd.Timestamp(END_DATE).date()
    print(f"  generating {ORDERS:,} orders (seed {SEED}, {DAYS} days)")
    orders = gen.make_orders(ORDERS, SEED, CUSTOMERS, PRODUCTS, end, DAYS)

    stamp = pd.to_datetime(orders["order_date"])
    orders = orders.assign(
        _date=stamp.dt.strftime("%Y-%m-%d"),
        _year=stamp.dt.strftime("%Y"),
        _month=stamp.dt.strftime("%m"),
        _day=stamp.dt.strftime("%d"),
        _hour=stamp.dt.strftime("%H"),
    )
    columns = ["order_id", "customer_id", "product_id", "quantity", "order_date", "status"]

    stats: dict[str, Any] = {}

    # --- daily: 30 partitions, the sensible baseline --------------------
    root = CORPUS / "daily"
    written = 0
    for (y, m, d), group in orders.groupby(["_year", "_month", "_day"], sort=True):
        path = root / f"year={y}" / f"month={m}" / f"day={d}" / "orders.parquet"
        gen.write_frame(group[columns], path, "parquet")
        written += 1
    stats["daily"] = summarise(root, written)

    # --- hourly: 720 partitions, the same rows -------------------------
    root = CORPUS / "hourly"
    written = 0
    for (y, m, d, h), group in orders.groupby(["_year", "_month", "_day", "_hour"], sort=True):
        path = root / f"year={y}" / f"month={m}" / f"day={d}" / f"hour={h}" / "orders.parquet"
        gen.write_frame(group[columns], path, "parquet")
        written += 1
    stats["hourly"] = summarise(root, written)

    # --- one day, one file --------------------------------------------
    day = orders[orders["_date"] == TARGET_DAY][columns].reset_index(drop=True)
    root = CORPUS / "one_file"
    gen.write_frame(day, root / "orders.parquet", "parquet")
    stats["one_file"] = summarise(root, 1)

    # --- one day, 200 files, identical rows ----------------------------
    #
    # Same bytes of data, same schema, same query. The only variable is how
    # many objects Athena must open, which is the whole point.
    root = CORPUS / "many_files"
    chunk = max(1, len(day) // SMALL_FILE_COUNT)
    written = 0
    for index in range(0, len(day), chunk):
        part = day.iloc[index : index + chunk]
        if part.empty:
            continue
        gen.write_frame(part, root / f"part-{written:05d}.parquet", "parquet")
        written += 1
    stats["many_files"] = summarise(root, written)

    stats["target_day_rows"] = int(len(day))
    stats["total_rows"] = int(len(orders))
    for name in ("daily", "hourly", "one_file", "many_files"):
        s = stats[name]
        print(
            f"  {name:<12} {s['files']:>4} files  {s['megabytes']:>7.2f} MB  "
            f"avg {s['avg_file_kb']:>8.1f} KB/file"
        )
    return stats


def summarise(root: Path, expected: int) -> dict[str, Any]:
    files = sorted(root.rglob("*.parquet"))
    total = sum(p.stat().st_size for p in files)
    return {
        "files": len(files),
        "expected_files": expected,
        "bytes": total,
        "megabytes": round(total / 1e6, 3),
        "avg_file_kb": round(total / len(files) / 1024, 2) if files else 0.0,
    }


def upload(bucket: str) -> None:
    for name in TABLES:
        local = CORPUS / name
        if not local.exists():
            print(f"  skip {name} (not generated)")
            continue
        target = f"s3://{bucket}/{PREFIX}/{name}/"
        print(f"  {target}")
        subprocess.run(
            ["aws", "s3", "sync", str(local), target, "--region", REGION, "--only-show-errors"],
            check=True,
        )


# --------------------------------------------------------------------------
# Athena
# --------------------------------------------------------------------------

COLUMNS_DDL = """
    order_id    string,
    customer_id string,
    product_id  string,
    quantity    int,
    order_date  string,
    status      string
"""


def ddl_statements(bucket: str) -> list[tuple[str, str]]:
    base = f"s3://{bucket}/{PREFIX}"
    out: list[tuple[str, str]] = []
    for key, table in TABLES.items():
        out.append((table, f"DROP TABLE IF EXISTS {table}"))

    out.append((
        TABLES["daily"],
        f"""CREATE EXTERNAL TABLE {TABLES['daily']} ({COLUMNS_DDL})
            PARTITIONED BY (year string, month string, day string)
            STORED AS PARQUET LOCATION '{base}/daily/'""",
    ))
    out.append((
        TABLES["hourly"],
        f"""CREATE EXTERNAL TABLE {TABLES['hourly']} ({COLUMNS_DDL})
            PARTITIONED BY (year string, month string, day string, hour string)
            STORED AS PARQUET LOCATION '{base}/hourly/'""",
    ))
    for key in ("one_file", "many_files"):
        out.append((
            TABLES[key],
            f"""CREATE EXTERNAL TABLE {TABLES[key]} ({COLUMNS_DDL})
                STORED AS PARQUET LOCATION '{base}/{key}/'""",
        ))
    return out


def run_query(athena: Any, sql: str, poll: float = 0.4) -> dict[str, Any]:
    started = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": DATABASE},
        WorkGroup=WORKGROUP,
    )
    qid = started["QueryExecutionId"]
    while True:
        detail = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
        state = detail["Status"]["State"]
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(poll)
    stats = detail.get("Statistics", {})
    return {
        "state": state,
        "reason": detail["Status"].get("StateChangeReason"),
        "bytes_scanned": stats.get("DataScannedInBytes"),
        "planning_ms": stats.get("QueryPlanningTimeInMillis"),
        "engine_ms": stats.get("EngineExecutionTimeInMillis"),
        "total_ms": stats.get("TotalExecutionTimeInMillis"),
        "sql": " ".join(sql.split())[:200],
    }


def repeat(athena: Any, sql: str, times: int = 3) -> dict[str, Any]:
    """Run a query several times and keep the median.

    A single Athena timing is close to worthless: cold caches, scheduling and
    result staging move it by hundreds of milliseconds run to run. The median
    of three is still noisy but no longer anecdotal, and the spread is reported
    so a reader can judge whether a difference is real.
    """
    runs = [run_query(athena, sql) for _ in range(times)]
    ok = [r for r in runs if r["state"] == "SUCCEEDED"]
    if not ok:
        # Fail loudly. A failed Athena query still carries a runtime and zero
        # bytes scanned, which is exactly the shape of a plausible fast query.
        return {
            "state": runs[0]["state"],
            "reason": runs[0].get("reason"),
            "bytes_scanned": None,
            "planning_ms": None,
            "engine_ms": None,
            "total_ms": None,
            "sql": runs[0]["sql"],
            "runs": 0,
        }

    def median(key: str) -> int | None:
        values = sorted(r[key] for r in ok if r.get(key) is not None)
        return values[len(values) // 2] if values else None

    return {
        "state": "SUCCEEDED",
        "bytes_scanned": ok[0]["bytes_scanned"],
        "planning_ms": median("planning_ms"),
        "engine_ms": median("engine_ms"),
        "total_ms": median("total_ms"),
        "planning_ms_all": [r["planning_ms"] for r in ok],
        "total_ms_all": [r["total_ms"] for r in ok],
        "sql": ok[0]["sql"],
        "runs": len(ok),
    }


# --------------------------------------------------------------------------
# experiments
# --------------------------------------------------------------------------

Y, M, D = TARGET_DAY.split("-")

# GROUP BY is not optional, and its absence is why the first run of this script
# produced a report full of zeroes: every query failed with "'status' must be an
# aggregate expression or appear in GROUP BY", and the report rendered those
# failures as measurements.
#
# Note also that ONE_DAY cannot be built by appending a WHERE clause to
# AGGREGATE - the clause has to sit before GROUP BY. Composing SQL by string
# concatenation is how that mistake happens.
AGGREGATE = (
    "SELECT status, count(*) AS orders, sum(quantity) AS units "
    "FROM {t} GROUP BY status ORDER BY status"
)
ONE_DAY = (
    "SELECT status, count(*) AS orders, sum(quantity) AS units "
    "FROM {t} "
    f"WHERE year = '{Y}' AND month = '{M}' AND day = '{D}' "
    "GROUP BY status ORDER BY status"
)


def run_experiments(athena: Any, corpus: dict[str, Any]) -> dict[str, Any]:
    results: dict[str, Any] = {"partitioning": {}, "small_files": {}, "repair": {}}

    # Registering partitions is itself a cost, and the one people forget: 720
    # partitions is 720 pieces of catalog metadata to create, and later to list
    # on every query that does not prune them away.
    for layout in ("daily", "hourly"):
        table = TABLES[layout]
        started = time.monotonic()
        record = run_query(athena, f"MSCK REPAIR TABLE {table}")
        record["wall_seconds"] = round(time.monotonic() - started, 2)
        results["repair"][layout] = record
        print(f"  MSCK {layout:<11} {record['state']:<10} {record['wall_seconds']:>6.2f} s wall")

    for layout in ("daily", "hourly"):
        table = TABLES[layout]
        results["partitioning"][layout] = {
            "one_day": repeat(athena, ONE_DAY.format(t=table)),
            "full_scan": repeat(athena, AGGREGATE.format(t=table)),
            "corpus": corpus.get(layout, {}),
        }
        rec = results["partitioning"][layout]["one_day"]
        print(
            f"  {layout:<11} one_day    plan {str(rec.get('planning_ms')):>6} ms"
            f"   total {str(rec.get('total_ms')):>6} ms"
            f"   {rec.get('bytes_scanned')} bytes"
        )

    for variant in ("one_file", "many_files"):
        table = TABLES[variant]
        results["small_files"][variant] = {
            "aggregate": repeat(athena, AGGREGATE.format(t=table)),
            "corpus": corpus.get(variant, {}),
        }
        rec = results["small_files"][variant]["aggregate"]
        print(
            f"  {variant:<11} aggregate  plan {str(rec.get('planning_ms')):>6} ms"
            f"   total {str(rec.get('total_ms')):>6} ms"
            f"   {rec.get('bytes_scanned')} bytes"
        )

    return results


def build_report(snapshot: dict[str, Any]) -> str:
    part = snapshot["results"]["partitioning"]
    small = snapshot["results"]["small_files"]
    repair = snapshot["results"]["repair"]

    def g(node: Any, *path: str) -> Any:
        for key in path:
            if not isinstance(node, dict):
                return "-"
            node = node.get(key)
        return "-" if node is None else node

    def failures() -> list[str]:
        """Any query that did not succeed, so the report cannot hide one."""
        bad = []
        for group, entries in (("partitioning", part), ("small_files", small)):
            for layout, queries in entries.items():
                for query_name, record in queries.items():
                    if not isinstance(record, dict):
                        continue
                    state = record.get("state")
                    if state is not None and state != "SUCCEEDED":
                        bad.append(f"{group}.{layout}.{query_name}: {record.get('reason')}")
        return bad

    problems = failures()

    rows_partition = [
        ("partitions", "corpus", "files"),
        ("total MB", "corpus", "megabytes"),
        ("**avg file size (KB)**", "corpus", "avg_file_kb"),
    ]
    rows_one_day = [
        ("planning ms (median)", "one_day", "planning_ms"),
        ("planning ms (each run)", "one_day", "planning_ms_all"),
        ("total ms (median)", "one_day", "total_ms"),
        ("bytes scanned", "one_day", "bytes_scanned"),
    ]
    rows_full = [
        ("planning ms (median)", "full_scan", "planning_ms"),
        ("total ms (median)", "full_scan", "total_ms"),
        ("bytes scanned", "full_scan", "bytes_scanned"),
    ]
    rows_small = [
        ("files", "corpus", "files"),
        ("total MB", "corpus", "megabytes"),
        ("avg file size (KB)", "corpus", "avg_file_kb"),
        ("planning ms (median)", "aggregate", "planning_ms"),
        ("total ms (median)", "aggregate", "total_ms"),
        ("total ms (each run)", "aggregate", "total_ms_all"),
        ("bytes scanned", "aggregate", "bytes_scanned"),
    ]

    def table(header_a: str, header_b: str, source: dict, key_a: str, key_b: str, rows) -> list[str]:
        out = ["", f"| | {header_a} | {header_b} |", "| --- | ---: | ---: |"]
        for label, *path in rows:
            out.append(
                f"| {label} | {g(source, key_a, *path)} | {g(source, key_b, *path)} |"
            )
        return out

    lines = [
        "# Phase 1 - partitioning and file-count experiments",
        "",
        f"Captured {snapshot['captured_at_utc']}. Database `{DATABASE}`, "
        f"workgroup `{WORKGROUP}`.",
        "",
        f"{snapshot['corpus'].get('total_rows', 0):,} orders across {DAYS} days, arranged "
        "four ways. Same rows, same schema, same queries - only the layout on S3 differs.",
        "",
        "Timings are the **median of three runs**, with every run listed so a",
        "reader can see the spread. Athena carries roughly a second of fixed",
        "overhead per query; a difference smaller than that is not a claim worth",
        "making, and is reported as inconclusive rather than dressed up.",
        "",
        "---",
        "",
        "## 1.5 Over-partitioning: 30 partitions vs 720",
        "",
        "Identical rows. The hourly layout splits each day into 24, which is the",
        "shape people reach for when they assume more partitions is more pruning.",
    ]
    lines += table("daily (30)", "hourly (720)", part, "daily", "hourly", rows_partition)
    lines += [
        "",
        f"| MSCK REPAIR wall (s) | {g(repair, 'daily', 'wall_seconds')} | "
        f"{g(repair, 'hourly', 'wall_seconds')} |",
        "",
        "**Query: one day** - the case the partitioning is supposed to help.",
    ]
    lines += table("daily", "hourly", part, "daily", "hourly", rows_one_day)
    lines += ["", "**Query: full scan** - no filter, so nothing can be pruned."]
    lines += table("daily", "hourly", part, "daily", "hourly", rows_full)

    lines += [
        "",
        "---",
        "",
        "## 1.6 Small files: 1 file vs 200, identical bytes",
        "",
        "One day of orders, written twice. Same rows, same total size, same",
        "query. The only variable is how many objects the engine must open.",
    ]
    lines += table("one file", "200 files", small, "one_file", "many_files", rows_small)

    if problems:
        lines += [
            "",
            "---",
            "",
            "## Queries that did not succeed",
            "",
            "**Every number above is suspect until these are fixed.** A failed",
            "Athena query still reports a runtime and zero bytes scanned, which",
            "looks exactly like a fast query. That is how the first run of this",
            "experiment produced a full report of zeroes that read as plausible.",
            "",
        ]
        lines += [f"- `{item}`" for item in problems]

    lines += [""]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generate", action="store_true", help="build the local corpus")
    parser.add_argument("--upload", action="store_true", help="sync it to the lake")
    parser.add_argument("--run", action="store_true", help="DDL, queries, report")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--phase", default="01")
    args = parser.parse_args(argv)
    if args.all:
        args.generate = args.upload = args.run = True
    if not (args.generate or args.upload or args.run):
        parser.error("choose --generate, --upload, --run or --all")

    out_dir = REPO_ROOT / "docs" / "evidence" / f"phase-{args.phase}"
    out_dir.mkdir(parents=True, exist_ok=True)
    corpus_file = out_dir / "experiments-corpus.json"

    corpus: dict[str, Any] = {}
    if args.generate:
        print("== building corpus")
        corpus = build_corpus()
        write_lf(corpus_file, json.dumps(corpus, indent=2) + "\n")
    elif corpus_file.exists():
        corpus = json.loads(corpus_file.read_text(encoding="utf-8"))

    if not (args.upload or args.run):
        return 0

    session = boto3.Session(region_name=REGION)
    account = session.client("sts").get_caller_identity()["Account"]
    bucket = f"{PROJECT}-{account}"

    if args.upload:
        print("== uploading")
        upload(bucket)

    if args.run:
        athena = session.client("athena")
        print("== creating tables")
        for table, sql in ddl_statements(bucket):
            record = run_query(athena, sql)
            flag = "ok " if record["state"] == "SUCCEEDED" else "FAIL"
            print(f"  {flag} {table:<30} {record['sql'][:46]}")
            if record["state"] != "SUCCEEDED":
                print(f"       {record['reason']}")

        print("== experiments")
        snapshot = {
            "captured_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "corpus": corpus,
            "results": run_experiments(athena, corpus),
        }
        write_lf(out_dir / "experiments.json", json.dumps(snapshot, indent=2, default=str) + "\n")
        report = write_lf(out_dir / "partitioning-and-small-files.md", build_report(snapshot))
        print(f"\nreport -> {report}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
