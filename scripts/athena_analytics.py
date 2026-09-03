"""Run the Phase 5 Athena queries and record what each one scanned.

Reads scripts/athena_analytics.sql, executes every statement in file order
through the training workgroup, and writes the timings and bytes scanned to
docs/evidence/phase-05/athena-analytics.json.

The bytes are the interesting column. A query that returns the right answer
having read forty times more data than it needed is a correct query and a bad
one, and that difference is invisible unless it is measured. The SQL file pairs
queries deliberately - pruned against unpruned, one column against several,
CTAS summary against the full fact scan - so the report shows both halves.

Deliberately self-contained. scripts/partition_experiments.py has a similar
runner, but importing it drags in pandas and a sys.path insert for the
generator, neither of which this needs.

Usage::

    ./scripts/de.sh analytics
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
SQL_FILE = REPO_ROOT / "scripts" / "athena_analytics.sql"

# Statements whose value is a side effect rather than an answer. Reported, but
# not compared on bytes - a CREATE TABLE scanning nothing is not a win.
DDL_PREFIXES = ("ddl_", "ctas_drop")


def load_statements(path: Path, lake_bucket: str) -> list[tuple[str, str]]:
    """Split the file on `-- @name` markers, keeping file order."""
    text = path.read_text(encoding="utf-8").replace("{lake_bucket}", lake_bucket)
    parts = re.split(r"^--\s*@name\s+(\S+)\s*$", text, flags=re.M)
    # re.split with one capture group yields [preamble, name, body, name, body...]
    statements = []
    for name, body in zip(parts[1::2], parts[2::2]):
        sql = "\n".join(
            line for line in body.splitlines() if not line.strip().startswith("--")
        ).strip().rstrip(";")
        if sql:
            statements.append((name, sql))
    return statements


def run_query(athena: Any, sql: str, database: str, workgroup: str, poll: float = 0.4) -> dict:
    started = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": database},
        WorkGroup=workgroup,
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
        # None rather than 0 on failure: a failed query still reports a runtime
        # and zero bytes, and averaging that in would look like a fast query.
        "bytes_scanned": stats.get("DataScannedInBytes") if state == "SUCCEEDED" else None,
        "engine_ms": stats.get("EngineExecutionTimeInMillis") if state == "SUCCEEDED" else None,
        "query_id": qid,
    }


def first_rows(athena: Any, query_id: str, limit: int = 4) -> list[list[str]]:
    """A few result rows, so the evidence shows answers and not only costs."""
    try:
        page = athena.get_query_results(QueryExecutionId=query_id, MaxResults=limit + 1)
    except Exception:  # noqa: BLE001 - DDL statements have no result set
        return []
    return [
        [c.get("VarCharValue", "") for c in row.get("Data", [])]
        for row in page.get("ResultSet", {}).get("Rows", [])
    ]


def env(name: str, default: str) -> str:
    for line in (REPO_ROOT / "config" / "project.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(f"export {name}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return default


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", default="05")
    args = parser.parse_args(argv)

    region = env("AWS_REGION", "us-east-2")
    database = env("GLUE_DATABASE", "training_db")
    project = env("PROJECT", "de-training")

    session = boto3.Session()
    account = session.client("sts", region_name=region).get_caller_identity()["Account"]
    lake_bucket = f"{project}-{account}"
    workgroup = f"{project}-wg"
    athena = session.client("athena", region_name=region)

    statements = load_statements(SQL_FILE, lake_bucket)
    print(f"== {len(statements)} statements against {database} via {workgroup}")

    results = []
    for name, sql in statements:
        outcome = run_query(athena, sql, database, workgroup)
        outcome["name"] = name
        outcome["is_ddl"] = name.startswith(DDL_PREFIXES)
        if outcome["state"] == "SUCCEEDED" and not outcome["is_ddl"]:
            outcome["sample_rows"] = first_rows(athena, outcome["query_id"])
        results.append(outcome)

        mb = outcome["bytes_scanned"]
        shown = f"{mb / 1_048_576:>8.3f} MB" if mb is not None else "       - "
        print(f"  {outcome['state']:9} {shown}  {name}")
        if outcome["state"] != "SUCCEEDED":
            print(f"             {str(outcome['reason'])[:140]}")

    # The paired comparisons the SQL file was built around.
    by_name = {r["name"]: r for r in results if r["bytes_scanned"] is not None}

    def compare(cheap: str, dear: str) -> dict | None:
        a, b = by_name.get(cheap), by_name.get(dear)
        if not a or not b or not a["bytes_scanned"]:
            return None
        return {
            "cheaper": cheap,
            "cheaper_bytes": a["bytes_scanned"],
            "costlier": dear,
            "costlier_bytes": b["bytes_scanned"],
            "times_more_data": round(b["bytes_scanned"] / a["bytes_scanned"], 1),
        }

    comparisons = [
        c
        for c in (
            compare("pruned_single_day", "full_scan_same_answer"),
            compare("single_column_projection", "many_column_projection"),
            compare("ctas_read_back", "revenue_by_category"),
        )
        if c
    ]

    print()
    for c in comparisons:
        print(f"  {c['costlier']} read {c['times_more_data']}x the data of {c['cheaper']}")

    failed = [r["name"] for r in results if r["state"] != "SUCCEEDED"]
    if failed:
        print(f"\n  queries that did not succeed: {', '.join(failed)}")

    target = REPO_ROOT / "docs" / "evidence" / f"phase-{args.phase}"
    target.mkdir(parents=True, exist_ok=True)
    out = target / "athena-analytics.json"
    payload = {
        "database": database,
        "workgroup": workgroup,
        "statements": results,
        "comparisons": comparisons,
        "failed": failed,
    }
    out.write_bytes((json.dumps(payload, indent=2, default=str) + "\n").encode("utf-8"))
    print(f"\n  evidence -> {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
