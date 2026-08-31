#!/usr/bin/env python3
"""Measure what file format and partitioning actually cost.

The claim "Parquet is faster and cheaper" is easy to repeat and rarely
measured. This runs the *same logical query* against the same rows stored four
ways and records what Athena reports it scanned.

Three effects are deliberately kept apart, because quoting them together is the
usual way this comparison is reported wrongly:

    columnar storage   flat Parquet vs CSV      -- same scan scope, different encoding
    partition pruning  partitioned vs flat      -- same encoding, different scan scope
    both together      partitioned Parquet vs CSV

Reporting only the last number credits Parquet with a saving that partitioning
produced.

Two facts about Athena that shape how the results must be read:

    Bytes scanned is *actual*, not billed. Athena bills a 10 MB minimum per
    query, so at development scale every query below costs the same. The ratio
    is the finding; the dollar figure is not.

    Runtime includes fixed overhead of roughly a second - planning, scheduling,
    result staging. On a small dataset that overhead dominates and runtime
    comparisons are noise. Bytes scanned is the honest metric at this size.

Usage::

    ./scripts/de.sh benchmark --ddl        create the four tables, then measure
    ./scripts/de.sh benchmark              measure only (tables already exist)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:  # pragma: no cover
    sys.exit("boto3 is required: py -3 -m pip install -r requirements.txt")

REPO_ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = REPO_ROOT / "sql" / "athena"

TABLES = {
    "csv": "bench_orders_csv",
    "json": "bench_orders_json",
    "parquet_partitioned": "bench_orders_parquet",
    "parquet_flat": "bench_orders_parquet_flat",
}

# Prefixes holding each format, for the on-disk size column.
PREFIXES = {
    "csv": "benchmark/csv/orders/",
    "json": "benchmark/json/orders/",
    "parquet_partitioned": "benchmark/parquet/orders/",
    "parquet_flat": "benchmark/parquet_flat/orders/",
}

# One day inside the generated window. Pinned so the query is reproducible.
PRUNE_DAY = ("2026", "08", "19")

QUERIES = {
    "pruned_single_day": (
        "SELECT status, count(*) AS orders, sum(quantity) AS units "
        "FROM {table} "
        "WHERE year = '{y}' AND month = '{m}' AND day = '{d}' "
        "GROUP BY status ORDER BY status"
    ),
    "full_scan_no_filter": (
        "SELECT status, count(*) AS orders, sum(quantity) AS units "
        "FROM {table} GROUP BY status ORDER BY status"
    ),
    "single_column_aggregate": "SELECT sum(quantity) AS units FROM {table}",
}


def load_project_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in (REPO_ROOT / "config" / "project.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("export "):
            continue
        key, _, value = line[len("export ") :].partition("=")
        value = value.strip().strip('"').strip("'")
        if value.startswith("$"):
            value = env.get(value.lstrip("$"), "")
        env[key.strip()] = value
    return env


ENV = load_project_env()
REGION = ENV.get("AWS_REGION", "us-east-2")
PROJECT = ENV.get("PROJECT", "de-training")
DATABASE = ENV.get("GLUE_DATABASE", "training_db")
WORKGROUP = f"{PROJECT}-wg"


def write_lf(path: Path, text: str) -> Path:
    """UTF-8, LF, on every platform. See capture_evidence.py for why."""
    path.write_bytes(text.encode("utf-8"))
    return path


# --------------------------------------------------------------------------
# Athena
# --------------------------------------------------------------------------


def run_query(athena: Any, sql: str, *, poll: float = 0.5) -> dict[str, Any]:
    """Execute one statement and return its execution record.

    Returns rather than raises on failure: a benchmark that dies on the first
    error tells you less than one that records which variants worked.
    """
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
        "query_execution_id": qid,
        "state": state,
        "reason": detail["Status"].get("StateChangeReason"),
        "bytes_scanned": stats.get("DataScannedInBytes"),
        "engine_ms": stats.get("EngineExecutionTimeInMillis"),
        "planning_ms": stats.get("QueryPlanningTimeInMillis"),
        "total_ms": stats.get("TotalExecutionTimeInMillis"),
        "sql": " ".join(sql.split())[:400],
    }


def fetch_rows(athena: Any, qid: str) -> list[list[str]]:
    """Result rows as plain lists, header included."""
    try:
        page = athena.get_query_results(QueryExecutionId=qid, MaxResults=100)
    except (ClientError, BotoCoreError):
        return []
    rows = []
    for row in page.get("ResultSet", {}).get("Rows", []):
        rows.append([cell.get("VarCharValue", "") for cell in row.get("Data", [])])
    return rows


def split_statements(sql_text: str) -> list[str]:
    """Split a .sql file into statements, dropping comments and blanks."""
    without_comments = "\n".join(
        line for line in sql_text.splitlines() if not line.strip().startswith("--")
    )
    return [s.strip() for s in without_comments.split(";") if s.strip()]


def apply_ddl(athena: Any, s3: Any, bucket: str) -> list[dict[str, Any]]:
    results = []
    for path in sorted(SQL_DIR.glob("*.sql")):
        text = path.read_text(encoding="utf-8").replace("${LAKE}", bucket)

        for statement in split_statements(text):
            record = run_query(athena, statement)
            record["file"] = path.name
            results.append(record)
            flag = "ok " if record["state"] == "SUCCEEDED" else "FAIL"
            print(f"  {flag} {path.name:<34} {record['sql'][:52]}")
            if record["state"] != "SUCCEEDED":
                print(f"       {record['reason']}")
    return results


def clear_prefix(s3: Any, bucket: str, prefix: str) -> int:
    """Delete every object under a prefix. Never used on raw/ - see lake.tf."""
    assert not prefix.startswith("raw/"), "raw/ is immutable"
    deleted = 0
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        keys = [{"Key": item["Key"]} for item in page.get("Contents", [])]
        if keys:
            s3.delete_objects(Bucket=bucket, Delete={"Objects": keys})
            deleted += len(keys)
    return deleted


def prefix_size(s3: Any, bucket: str, prefix: str) -> dict[str, Any]:
    objects, size = 0, 0
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            objects += 1
            size += item["Size"]
    return {"objects": objects, "bytes": size}


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------


def mb(value: int | None) -> str:
    return "-" if value is None else f"{value / 1e6:.3f}"


def build_report(snapshot: dict[str, Any]) -> str:
    disk = snapshot["on_disk"]
    runs = snapshot["queries"]
    baseline_key = "csv"

    lines = [
        "# Phase 1 - format comparison",
        "",
        f"Database `{DATABASE}`, workgroup `{WORKGROUP}`, region `{REGION}`.",
        f"Captured {snapshot['captured_at_utc']}.",
        "",
        "All four tables describe the **same rows**. Only the encoding and the",
        "partition layout differ.",
        "",
        "## On disk",
        "",
        "| format | objects | MB | vs CSV |",
        "| --- | ---: | ---: | ---: |",
    ]
    csv_bytes = disk.get(baseline_key, {}).get("bytes") or 0
    for name, stats in disk.items():
        ratio = f"{100 * stats['bytes'] / csv_bytes:.1f}%" if csv_bytes else "-"
        lines.append(f"| {name} | {stats['objects']} | {mb(stats['bytes'])} | {ratio} |")

    for query_name in QUERIES:
        lines += [
            "",
            f"## Query: `{query_name}`",
            "",
            "| format | bytes scanned (MB) | vs CSV | planning ms | engine ms |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
        base = (runs.get(baseline_key, {}).get(query_name, {}) or {}).get("bytes_scanned") or 0
        for fmt in TABLES:
            record = runs.get(fmt, {}).get(query_name, {}) or {}
            scanned = record.get("bytes_scanned")
            if record.get("state") != "SUCCEEDED":
                lines.append(f"| {fmt} | failed | - | - | - |")
                continue
            ratio = f"{100 * scanned / base:.2f}%" if base and scanned is not None else "-"
            lines.append(
                f"| {fmt} | {mb(scanned)} | {ratio} | "
                f"{record.get('planning_ms', '-')} | {record.get('engine_ms', '-')} |"
            )

    # The three effects, separated arithmetically rather than described. Each
    # ratio holds exactly one variable constant.
    def scanned(fmt: str, query: str) -> int | None:
        return (runs.get(fmt, {}).get(query, {}) or {}).get("bytes_scanned")

    def pct(numerator: int | None, denominator: int | None) -> str:
        if not numerator or not denominator:
            return "-"
        return f"{100 * numerator / denominator:.2f}%"

    csv_full = scanned("csv", "full_scan_no_filter")
    csv_pruned = scanned("csv", "pruned_single_day")
    flat_full = scanned("parquet_flat", "full_scan_no_filter")
    flat_pruned = scanned("parquet_flat", "pruned_single_day")
    part_pruned = scanned("parquet_partitioned", "pruned_single_day")

    lines += [
        "",
        "## The three effects, isolated",
        "",
        "| effect | comparison | result |",
        "| --- | --- | ---: |",
        f"| columnar storage alone | flat Parquet vs CSV, both full scans | **{pct(flat_full, csv_full)}** |",
        f"| partition pruning alone | partitioned vs flat Parquet, both filtered | **{pct(part_pruned, flat_pruned)}** |",
        f"| pruning on a row format | CSV filtered vs CSV full scan | **{pct(csv_pruned, csv_full)}** |",
        f"| both together | partitioned Parquet vs CSV, both filtered | **{pct(part_pruned, csv_pruned)}** |",
        "",
        "The third row is the one usually left out. CSV prunes too - pruning is a",
        "property of the partition layout, not of Parquet. Quoting only the last",
        "row credits the file format with a saving the directory structure",
        "produced.",
        "",
    ]

    # Flag the counterintuitive case rather than leaving a reader to assume a
    # mistake: on an unpartitioned columnar table, a WHERE clause on ordinary
    # columns *increases* bytes scanned, because those columns must be read.
    if flat_pruned and flat_full and flat_pruned > flat_full:
        lines += [
            "### Filtering the flat Parquet table made it scan MORE",
            "",
            f"`pruned_single_day` scanned **{flat_pruned:,} bytes**; "
            f"`full_scan_no_filter` scanned **{flat_full:,}**. The filtered query "
            f"read **{flat_pruned / flat_full:.1f}x more data**.",
            "",
            "This is not an error, and it is the clearest demonstration of columnar",
            "behaviour in the whole comparison. In the flat table `year`, `month`",
            "and `day` are ordinary columns, so:",
            "",
            "- `full_scan_no_filter` reads 2 columns: `status`, `quantity`",
            "- `pruned_single_day` reads 5: those two plus the three it filters on",
            "",
            "A columnar engine only reads the columns a query names. Adding a",
            "predicate on a column you were not otherwise reading therefore *costs*",
            "bytes. The same predicate against the partitioned table costs nothing,",
            "because the values live in the S3 prefix rather than in the file - the",
            "engine skips whole directories without opening anything.",
            "",
            "The practical rule: partition on the columns you filter by, and the",
            "filter becomes free. Leave them as data columns and every filter is a",
            "read.",
            "",
        ]

    lines += [
        "## Reading these numbers",
        "",
        "- **Bytes scanned is actual, not billed.** Athena bills a 10 MB minimum",
        "  per query, so at this data size every row above costs the same. The",
        "  ratio generalises to production volumes; the cost does not.",
        "- **`parquet_flat` vs `csv`** isolates columnar storage: same scan scope,",
        "  different encoding.",
        "- **`parquet_partitioned` vs `parquet_flat`** isolates partition pruning:",
        "  same encoding, different scan scope.",
        "- **`pruned_single_day` vs `full_scan_no_filter`** on the same table shows",
        "  what the WHERE clause is worth. Note that CSV prunes too - pruning is a",
        "  property of the partition layout, not of Parquet.",
        "- Runtime at this scale is dominated by roughly a second of fixed Athena",
        "  overhead. Treat the millisecond columns as indicative only.",
        "",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ddl", action="store_true", help="create the tables first")
    parser.add_argument("--phase", default="01")
    args = parser.parse_args(argv)

    session = boto3.Session(region_name=REGION)
    athena = session.client("athena")
    s3 = session.client("s3")
    account = session.client("sts").get_caller_identity()["Account"]
    bucket = f"{PROJECT}-{account}"

    snapshot: dict[str, Any] = {
        "captured_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "database": DATABASE,
        "workgroup": WORKGROUP,
        "bucket": bucket,
        "ddl": [],
        "on_disk": {},
        "queries": {},
        "result_equality": {},
    }

    if args.ddl:
        print("== creating tables")
        snapshot["ddl"] = apply_ddl(athena, s3, bucket)
        failed = [r for r in snapshot["ddl"] if r["state"] != "SUCCEEDED"]
        if failed:
            print(f"\n{len(failed)} DDL statement(s) failed; continuing to measure what exists.")

    print("== on-disk size")
    for fmt, prefix in PREFIXES.items():
        snapshot["on_disk"][fmt] = prefix_size(s3, bucket, prefix)
        stats = snapshot["on_disk"][fmt]
        print(f"  {fmt:<22} {stats['objects']:>3} objects  {mb(stats['bytes'])} MB")

    print("== queries")
    fingerprints: dict[str, list[list[str]]] = {}
    for fmt, table in TABLES.items():
        snapshot["queries"][fmt] = {}
        for name, template in QUERIES.items():
            sql = template.format(
                table=table, y=PRUNE_DAY[0], m=PRUNE_DAY[1], d=PRUNE_DAY[2]
            )
            record = run_query(athena, sql)
            snapshot["queries"][fmt][name] = record
            state = "ok " if record["state"] == "SUCCEEDED" else "FAIL"
            print(
                f"  {state} {fmt:<22} {name:<24} "
                f"{mb(record['bytes_scanned']):>8} MB  {record.get('engine_ms', '-')} ms"
            )
            if record["state"] != "SUCCEEDED":
                print(f"       {record['reason']}")
            elif name == "pruned_single_day":
                fingerprints[fmt] = fetch_rows(athena, record["query_execution_id"])

    # Criterion 1: four different encodings must agree on the answer. If they
    # do not, the benchmark is comparing different data and every ratio below
    # is meaningless.
    reference = fingerprints.get("csv")
    for fmt, rows in fingerprints.items():
        same = rows == reference
        snapshot["result_equality"][fmt] = same
        print(f"  {'ok ' if same else 'FAIL'} {fmt:<22} results match CSV: {same}")

    out_dir = REPO_ROOT / "docs" / "evidence" / f"phase-{args.phase}"
    out_dir.mkdir(parents=True, exist_ok=True)
    write_lf(out_dir / "benchmark.json", json.dumps(snapshot, indent=2, default=str) + "\n")
    report = write_lf(out_dir / "format-comparison.md", build_report(snapshot))
    print(f"\nreport -> {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
