"""Run the Phase 7 warehouse SQL through the Redshift Data API.

Executes scripts/redshift_warehouse.sql statement by statement against the
Serverless workgroup and writes the results, timings and row counts to
docs/evidence/phase-07/warehouse.json.

WHY THE DATA API AND NOT JDBC
-----------------------------
The Data API is a control-plane endpoint: it authenticates with the caller's
IAM identity and needs no database password, no driver and - the part that
matters here - no network path into the VPC. A JDBC client would have needed
either a public endpoint or a NAT gateway, and this project decided against
NAT in Phase 0 for exactly the reason it would apply here: ~$33/month for a
capability the work does not need.

RECONCILIATION IS THE POINT OF THE COUNTS
-----------------------------------------
The warehouse is a copy of data that already exists in the lake, so the only
interesting question about the load is whether it is faithful. `count_fact`
must match what Athena reports for curated_sales exactly - not approximately.
A star schema that quietly loses or duplicates rows during normalisation is
worse than no warehouse, because the numbers still look plausible.

Usage::

    ./scripts/de.sh warehouse
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any

import boto3

REPO_ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = REPO_ROOT / "sql" / "redshift"

# The Day 7 stages, in the order they must run. Named rather than globbed so a
# stray file in the directory cannot silently join the sequence.
STAGES = {
    "ddl": "01_ddl.sql",
    "load": "02_load.sql",
    "analytics": "03_analytics.sql",
    "spectrum": "04_spectrum.sql",
}

# Statements whose failure invalidates everything after them, so the runner
# stops rather than emitting a cascade of confusing follow-on errors.
BLOCKING_PREFIXES = ("drop_", "create_", "dim_", "fact_", "stg_", "copy_", "load_", "truncate_")


def load_statements(path: Path, **subs: str) -> list[tuple[str, str]]:
    text = path.read_text(encoding="utf-8")
    for key, value in subs.items():
        text = text.replace("{" + key + "}", value)
    parts = re.split(r"^--\s*@name\s+(\S+)\s*$", text, flags=re.M)
    statements = []
    for name, body in zip(parts[1::2], parts[2::2]):
        sql = "\n".join(
            line for line in body.splitlines() if not line.strip().startswith("--")
        ).strip().rstrip(";")
        if sql:
            statements.append((name, sql))
    return statements


def execute(client: Any, sql: str, workgroup: str, database: str) -> dict[str, Any]:
    started = client.execute_statement(
        WorkgroupName=workgroup, Database=database, Sql=sql
    )
    sid = started["Id"]
    while True:
        detail = client.describe_statement(Id=sid)
        status = detail["Status"]
        if status in ("FINISHED", "FAILED", "ABORTED"):
            break
        time.sleep(1)

    out: dict[str, Any] = {
        "state": status,
        "duration_ms": round(detail.get("Duration", 0) / 1_000_000),
        "rows_affected": detail.get("ResultRows"),
        "error": detail.get("Error"),
        "id": sid,
    }
    if status == "FINISHED" and detail.get("HasResultSet"):
        result = client.get_statement_result(Id=sid)
        out["columns"] = [c["name"] for c in result["ColumnMetadata"]]
        out["rows"] = [
            [next(iter(f.values())) if f and "isNull" not in f else None for f in row]
            for row in result.get("Records", [])[:10]
        ]
    return out


def env(name: str, default: str) -> str:
    for line in (REPO_ROOT / "config" / "project.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(f"export {name}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return default


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "stages",
        nargs="*",
        default=list(STAGES),
        help=f"stages to run, in order. Any of: {', '.join(STAGES)}. Default: all.",
    )
    parser.add_argument("--phase", default="07")
    args = parser.parse_args(argv)

    unknown = [s for s in args.stages if s not in STAGES]
    if unknown:
        parser.error(f"unknown stage(s): {', '.join(unknown)} (choose from {', '.join(STAGES)})")

    region = env("AWS_REGION", "us-east-2")
    project = env("PROJECT", "de-training")

    session = boto3.Session()
    account = session.client("sts", region_name=region).get_caller_identity()["Account"]
    lake_bucket = f"{project}-{account}"
    redshift_role = f"arn:aws:iam::{account}:role/{project}-redshift-role"
    workgroup = f"{project}-warehouse-wg"
    database = "retail"

    glue_database = env("GLUE_DATABASE", "training_db")
    client = session.client("redshift-data", region_name=region)

    statements: list[tuple[str, str, str]] = []
    for stage in args.stages:
        path = SQL_DIR / STAGES[stage]
        for name, sql in load_statements(
            path,
            lake_bucket=lake_bucket,
            redshift_role=redshift_role,
            glue_database=glue_database,
        ):
            statements.append((stage, name, sql))

    print(f"== {len(statements)} statements across {len(args.stages)} stage(s) "
          f"against {workgroup}/{database}")

    results = []
    current_stage = None
    for stage, name, sql in statements:
        if stage != current_stage:
            current_stage = stage
            print(f"-- {stage} ({STAGES[stage]})")
        outcome = execute(client, sql, workgroup, database)
        outcome["name"] = name
        outcome["stage"] = stage
        results.append(outcome)

        mark = "ok  " if outcome["state"] == "FINISHED" else "FAIL"
        print(f"  {mark} {outcome['duration_ms']:>7} ms  {name}")
        if outcome["state"] != "FINISHED":
            print(f"           {str(outcome['error'])[:160]}")
            # A failed DDL statement invalidates everything after it, so stop
            # rather than emit a cascade of confusing follow-on errors.
            if name.startswith(BLOCKING_PREFIXES):
                print("           ^^ a setup step failed; stopping rather than cascading")
                break
        elif outcome.get("rows"):
            for row in outcome["rows"][:3]:
                print(f"             {row}")

    failed = [r["name"] for r in results if r["state"] != "FINISHED"]

    # The sort-key comparison.
    #
    # Reported WITHOUT implying a direction. An earlier version printed
    # "pruned X ms vs unpruned Y ms", which reads as a result even when the
    # numbers say the opposite - and on this dataset they often do: twelve
    # thousand rows is small enough that cache state and fixed overhead dominate,
    # and whichever query runs second tends to look faster.
    #
    # The planner's estimated cost is the honest evidence at this scale, because
    # it describes work the engine expects to avoid rather than wall-clock noise.
    # So the timings are printed as observations, and the script says so out loud
    # when the "optimised" query was not actually quicker.
    by_name = {r["name"]: r for r in results if r["state"] == "FINISHED"}
    pruned, unpruned = by_name.get("pruned_by_sortkey"), by_name.get("unpruned_same_answer")
    if pruned and unpruned:
        print()
        print(f"  sort-key pair (same answer, different route):")
        print(f"    sortkey-usable predicate : {pruned['duration_ms']:>6} ms")
        print(f"    function-wrapped         : {unpruned['duration_ms']:>6} ms")
        if pruned["duration_ms"] >= unpruned["duration_ms"]:
            print("    NOTE: the sortkey-usable query was not faster in wall clock.")
            print("    At this row count timings are dominated by overhead - compare the")
            print("    EXPLAIN costs (explain_pruned vs explain_unpruned) instead.")

    for name in ("explain_pruned", "explain_unpruned"):
        rec = by_name.get(name)
        if rec and rec.get("rows"):
            first = " ".join(str(rec["rows"][0][0]).split())
            print(f"    {name:18} {first[:70]}")

    target = REPO_ROOT / "docs" / "evidence" / f"phase-{args.phase}"
    target.mkdir(parents=True, exist_ok=True)
    out = target / "warehouse.json"
    out.write_bytes(
        (json.dumps({"workgroup": workgroup, "database": database,
                     "statements": results, "failed": failed}, indent=2, default=str) + "\n"
         ).encode("utf-8")
    )
    print(f"\n  evidence -> {out}")

    if failed:
        print(f"  statements that did not finish: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
