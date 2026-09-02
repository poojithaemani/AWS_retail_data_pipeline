#!/usr/bin/env python3
"""Capture proof that the pipeline ran, before it is destroyed.

Everything in this project is torn down at the end of a session, so there is
never a live environment to demonstrate. That makes evidence capture a
deliverable in its own right rather than an afterthought - by the end, the
``docs/evidence/`` tree *is* the demo.

Each collector is independent and failure-tolerant: it records whatever exists
right now and notes what it could not reach. Running this against a
half-built or already-destroyed environment is expected and produces a valid,
if sparse, snapshot.

Collectors are added in the phase that first has something for them to
collect. Writing all ten up front produced eight that returned empty.

Usage::

    ./scripts/de.sh evidence 03
    ./scripts/de.sh evidence 03 --note "job bookmark re-run, 0 rows reprocessed"
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:  # pragma: no cover
    sys.exit("boto3 is required: py -3 -m pip install -r requirements.txt")

REPO_ROOT = Path(__file__).resolve().parents[1]
AWS_ERRORS = (ClientError, BotoCoreError)


def write_text_lf(path: Path, text: str) -> Path:
    """Write UTF-8 with LF endings, regardless of platform.

    ``Path.write_text()`` on Windows does two unhelpful things: it encodes with
    the ANSI codepage rather than UTF-8, and it translates newlines to CRLF.
    Evidence files are a committed deliverable, so both would show up as noise
    in every diff. Writing bytes sidesteps both.
    """
    path.write_bytes(text.encode("utf-8"))
    return path


def load_project_env() -> dict[str, str]:
    """Read config/project.env without needing a shell.

    The file is the single source of truth for region and project name, and
    this script may be invoked directly rather than through de.sh.
    """
    env: dict[str, str] = {}
    path = REPO_ROOT / "config" / "project.env"
    for line in path.read_text(encoding="utf-8").splitlines():
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
REGION = os.environ.get("AWS_REGION") or ENV.get("AWS_REGION", "us-east-2")
PROJECT = os.environ.get("PROJECT") or ENV.get("PROJECT", "de-training")


# --------------------------------------------------------------------------
# collectors
# --------------------------------------------------------------------------


def collect_context(session: boto3.Session) -> dict[str, Any]:
    identity = session.client("sts", region_name=REGION).get_caller_identity()
    return {
        "captured_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "account": identity["Account"],
        "caller": identity["Arn"],
        "region": REGION,
        "project": PROJECT,
        "git_commit": _git("rev-parse", "HEAD"),
        "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain")),
    }


def _git(*args: str) -> str:
    try:
        out = subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=15
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""



def collect_lake(session: boto3.Session) -> dict[str, Any]:
    """Object counts and bytes per lake layer.

    This is the row-count reconciliation's physical counterpart: if curated is
    empty after a run that claimed success, it shows up here.
    """
    s3 = session.client("s3", region_name=REGION)
    account = session.client("sts", region_name=REGION).get_caller_identity()["Account"]
    bucket = f"{PROJECT}-{account}"

    layers: dict[str, Any] = {}
    # benchmark/ is not a lake layer - it is the Phase 1 measurement corpus.
    # Recorded because its size is half the finding: the same rows occupy
    # very different space depending on encoding.
    for prefix in (
        "raw/",
        "processed/",
        "curated/",
        "quarantine/",
        "benchmark/",
        "experiments/",
        "athena-results/",
    ):
        objects, size, partitions = 0, 0, set()
        try:
            paginator = s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                for item in page.get("Contents", []):
                    objects += 1
                    size += item["Size"]
                    partitions.add(item["Key"].rsplit("/", 1)[0])
        except AWS_ERRORS as exc:
            layers[prefix] = {"error": str(exc)[:200]}
            continue
        layers[prefix] = {
            "objects": objects,
            "bytes": size,
            "megabytes": round(size / 1e6, 3),
            "distinct_prefixes": len(partitions),
        }
    return {"bucket": bucket, "layers": layers}




def collect_athena(session: boto3.Session) -> dict[str, Any]:
    """Recent queries with bytes scanned - the cost story, in numbers.

    Bytes scanned is the headline metric for the format and partitioning
    exercises: the same logical query against CSV and against partitioned
    Parquet should differ by orders of magnitude.
    """
    athena = session.client("athena", region_name=REGION)
    workgroup = f"{PROJECT}-wg"
    out: dict[str, Any] = {"workgroup": workgroup, "queries": []}
    try:
        ids = athena.list_query_executions(WorkGroup=workgroup, MaxResults=20).get(
            "QueryExecutionIds", []
        )
        if ids:
            batch = athena.batch_get_query_execution(QueryExecutionIds=ids)
            for execution in batch.get("QueryExecutions", []):
                stats = execution.get("Statistics", {})
                out["queries"].append(
                    {
                        "query_id": execution["QueryExecutionId"],
                        "sql": " ".join(execution.get("Query", "").split())[:600],
                        "state": execution.get("Status", {}).get("State"),
                        "submitted": str(execution.get("Status", {}).get("SubmissionDateTime", "")),
                        "bytes_scanned": stats.get("DataScannedInBytes"),
                        "megabytes_scanned": round(
                            stats.get("DataScannedInBytes", 0) / 1e6, 3
                        ),
                        "runtime_ms": stats.get("TotalExecutionTimeInMillis"),
                        "estimated_usd": round(
                            stats.get("DataScannedInBytes", 0) / 1e12 * 5.0, 6
                        ),
                    }
                )
    except AWS_ERRORS as exc:
        out["error"] = str(exc)[:200]
    return out






def collect_catalog(session: boto3.Session) -> dict[str, Any]:
    """What the Glue Data Catalog believes exists.

    Added in Phase 1, when hand-written DDL first put tables in the catalog.
    Phase 2 will extend this with crawler run history; there is nothing to
    record about crawlers until crawlers exist.

    The column types are the part worth keeping: Phase 2 deliberately breaks
    schema inference with a `price = UNKNOWN` row, and the interesting evidence
    is the before-and-after of a column's declared type.
    """
    glue = session.client("glue", region_name=REGION)
    out: dict[str, Any] = {"databases": []}

    try:
        for database in glue.get_databases().get("DatabaseList", []):
            tables = []
            for table in glue.get_tables(DatabaseName=database["Name"]).get("TableList", []):
                storage = table.get("StorageDescriptor", {})
                tables.append(
                    {
                        "name": table["Name"],
                        "location": storage.get("Location"),
                        "input_format": (storage.get("InputFormat") or "").rsplit(".", 1)[-1],
                        "serde": (
                            storage.get("SerdeInfo", {}).get("SerializationLibrary") or ""
                        ).rsplit(".", 1)[-1],
                        "columns": {c["Name"]: c["Type"] for c in storage.get("Columns", [])},
                        "partition_keys": [c["Name"] for c in table.get("PartitionKeys", [])],
                    }
                )
            out["databases"].append(
                {
                    "name": database["Name"],
                    "location": database.get("LocationUri"),
                    "table_count": len(tables),
                    "tables": tables,
                }
            )
    except AWS_ERRORS as exc:
        return {"error": str(exc)[:200]}

    # Crawler configuration and run history, added in Phase 2. The run record
    # is the evidence that schema discovery actually happened, and
    # TablesUpdated is what shows a re-crawl changing an existing schema rather
    # than creating something new.
    try:
        for crawler in glue.get_crawlers().get("Crawlers", []):
            last = crawler.get("LastCrawl", {})
            out.setdefault("crawlers", []).append(
                {
                    "name": crawler["Name"],
                    "state": crawler.get("State"),
                    "targets": [
                        target.get("Path")
                        for target in crawler.get("Targets", {}).get("S3Targets", [])
                    ],
                    "update_behavior": crawler.get("SchemaChangePolicy", {}).get("UpdateBehavior"),
                    "delete_behavior": crawler.get("SchemaChangePolicy", {}).get("DeleteBehavior"),
                    "recrawl_behavior": crawler.get("RecrawlPolicy", {}).get("RecrawlBehavior"),
                    "last_status": last.get("Status"),
                    "last_started": str(last.get("StartTime", "")),
                    "tables_created": last.get("TablesCreated"),
                    "tables_updated": last.get("TablesUpdated"),
                    "tables_deleted": last.get("TablesDeleted"),
                    "error": last.get("ErrorMessage"),
                }
            )
    except AWS_ERRORS as exc:
        out["crawlers"] = {"error": str(exc)[:200]}

    return out


def collect_etl(session: boto3.Session) -> dict[str, Any]:
    """Glue job definitions and run history, added in Phase 3.

    The reconciliation is the point. A curated dataset is only trustworthy if
    every source row is accounted for, and the run that produced it is the only
    place that proof exists - `curated/` shows what survived, never what did
    not. Once the training layer is destroyed the job and its logs go with it,
    so the numbers are lifted out of CloudWatch and into the evidence pack.

    Run history is capped at the five most recent per job: enough to show a
    failure followed by a fix, which is the interesting shape, without
    embedding an unbounded log.
    """
    glue = session.client("glue", region_name=REGION)
    logs = session.client("logs", region_name=REGION)
    out: dict[str, Any] = {"jobs": []}

    try:
        job_names = glue.list_jobs().get("JobNames", [])
    except AWS_ERRORS as exc:
        return {"error": str(exc)[:200]}

    for name in job_names:
        try:
            job = glue.get_job(JobName=name)["Job"]
            arguments = job.get("DefaultArguments", {})
            record: dict[str, Any] = {
                "name": name,
                "glue_version": job.get("GlueVersion"),
                "worker_type": job.get("WorkerType"),
                "number_of_workers": job.get("NumberOfWorkers"),
                "max_retries": job.get("MaxRetries"),
                "timeout_minutes": job.get("Timeout"),
                "script_location": job.get("Command", {}).get("ScriptLocation"),
                # The two arguments that carry a decision rather than a path.
                "bookmarks": arguments.get("--job-bookmark-option"),
                "glue_data_catalog_enabled": arguments.get("--enable-glue-datacatalog"),
                "runs": [],
            }

            runs = glue.get_job_runs(JobName=name, MaxResults=5).get("JobRuns", [])
            for run in runs:
                entry = {
                    "id": run.get("Id"),
                    "state": run.get("JobRunState"),
                    "started": str(run.get("StartedOn", "")),
                    "execution_seconds": run.get("ExecutionTime"),
                    "dpu_seconds": run.get("DPUSeconds"),
                    "error": (run.get("ErrorMessage") or "")[:300] or None,
                }
                reconciliation = _reconciliation_from_logs(logs, run.get("Id", ""))
                if reconciliation is not None:
                    entry["reconciliation"] = reconciliation
                record["runs"].append(entry)

            out["jobs"].append(record)
        except AWS_ERRORS as exc:
            out["jobs"].append({"name": name, "error": str(exc)[:200]})

    return out


def _reconciliation_from_logs(logs: Any, run_id: str) -> dict[str, Any] | None:
    """Lift the reconciliation dict the job logged for one run.

    It lands in /aws-glue/jobs/error rather than .../output: GlueLogger writes
    through log4j to the driver's stderr, which is a genuinely surprising place
    to find an INFO line and cost a while to locate the first time.
    """
    if not run_id:
        return None
    try:
        events = logs.filter_log_events(
            logGroupName="/aws-glue/jobs/error",
            logStreamNames=[run_id],
            filterPattern="reconciliation",
        ).get("events", [])
    except AWS_ERRORS:
        return None

    for event in events:
        message = event.get("message", "")
        start, end = message.find("{"), message.rfind("}")
        if start == -1 or end == -1:
            continue
        try:
            # ast.literal_eval, not json.loads: Python dict repr, single quotes.
            return ast.literal_eval(message[start : end + 1])
        except (ValueError, SyntaxError):
            continue
    return None


COLLECTORS: dict[str, Callable[[boto3.Session], dict[str, Any]]] = {
    "context": collect_context,
    "lake": collect_lake,
    "athena": collect_athena,
    "catalog": collect_catalog,
    "etl": collect_etl,
}

# Deliberately not built yet: data_quality, orchestration, warehouse,
# governance, monitoring, terraform. Each is roughly thirty lines of boto3
# following the same shape as the five above - a paginated list call, a few
# fields kept, errors caught and recorded rather than raised. They get written
# in the phase that first produces something for them to collect, rather than
# speculatively now, when most would return empty.
#
# `etl` was added in Phase 3, on the day there was a job run to record.


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------


def write_summary(target: Path, phase: str, snapshot: dict[str, Any], note: str) -> Path:
    """A human-readable index so the evidence folder is browsable, not just parseable."""
    context = snapshot.get("context", {})
    lines = [
        f"# Phase {phase} - evidence",
        "",
        f"Captured {context.get('captured_at_utc', 'unknown')} "
        f"from account `{context.get('account', '?')}` in `{context.get('region', '?')}`.",
        f"Commit `{context.get('git_commit', '')[:12]}` on `{context.get('git_branch', '')}`"
        + ("  **(working tree dirty)**" if context.get("git_dirty") else ""),
        "",
    ]
    if note:
        lines += ["> " + note, ""]

    lake = snapshot.get("lake", {}).get("layers", {})
    if lake:
        lines += ["## Lake layers", "", "| prefix | objects | MB |", "| --- | ---: | ---: |"]
        for prefix, stats in lake.items():
            if "error" in stats:
                lines.append(f"| `{prefix}` | - | {stats['error']} |")
            else:
                lines.append(f"| `{prefix}` | {stats['objects']} | {stats['megabytes']} |")
        lines.append("")

    queries = snapshot.get("athena", {}).get("queries", [])
    if queries:
        lines += [
            "## Athena queries",
            "",
            "| state | MB scanned | ms | sql |",
            "| --- | ---: | ---: | --- |",
        ]
        for query in queries[:10]:
            sql = query["sql"][:90].replace("|", "\\|")
            lines.append(
                f"| {query['state']} | {query.get('megabytes_scanned')} | "
                f"{query.get('runtime_ms')} | `{sql}` |"
            )
        lines.append("")

    jobs = snapshot.get("etl", {}).get("jobs", [])
    if jobs:
        lines += ["## Glue ETL runs", ""]
        for job in jobs:
            if "error" in job:
                lines += [f"`{job['name']}` &mdash; {job['error']}", ""]
                continue
            lines += [
                f"`{job['name']}` &mdash; Glue {job.get('glue_version')}, "
                f"{job.get('number_of_workers')} x {job.get('worker_type')}, "
                f"bookmarks `{job.get('bookmarks')}`",
                "",
                "| run | state | sec | DPU-sec | source | dupes | valid | rejected | curated | balanced |",
                "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
            ]
            for run in job.get("runs", []):
                rec = run.get("reconciliation") or {}
                lines.append(
                    f"| `{str(run.get('id'))[:12]}...` | {run.get('state')} | "
                    f"{run.get('execution_seconds')} | {run.get('dpu_seconds')} | "
                    f"{rec.get('source_rows', '-')} | {rec.get('duplicates_removed', '-')} | "
                    f"{rec.get('valid_rows', '-')} | {rec.get('rejected_rows', '-')} | "
                    f"{rec.get('curated_rows', '-')} | {rec.get('balanced', '-')} |"
                )
            lines.append("")

    # Collector status, stated rather than left to be inferred. An "error" here
    # is often the expected result -- a workgroup that is "not found" after a
    # teardown is exactly what a teardown is supposed to produce. Without this
    # section a reader sees a stack of error strings and assumes the capture
    # broke, when it is in fact the evidence.
    lines += ["## Collectors", "", "| collector | result |", "| --- | --- |"]
    for name, payload in snapshot.items():
        if isinstance(payload, dict) and "error" in payload:
            detail = str(payload["error"]).split(":")[-1].strip()
            lines.append(f"| `{name}` | not reachable &mdash; {detail} |")
        else:
            lines.append(f"| `{name}` | captured |")
    lines.append("")

    # Every artefact, not just the JSON. The written reports are the part a
    # human actually reads, and listing only the machine-readable files made
    # the index look emptier than the folder.
    lines += ["## Files", ""]
    for path in sorted(target.iterdir()):
        if path.name == "README.md" or path.is_dir():
            continue
        lines.append(f"- `{path.name}`")
    lines.append("")

    summary = target / "README.md"
    write_text_lf(summary, "\n".join(lines) + "\n")
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture evidence for a phase.")
    parser.add_argument("phase", help="phase number, e.g. 03")
    parser.add_argument("--note", default="", help="one-line note recorded in the summary")
    parser.add_argument(
        "--only", default="", help="comma-separated subset of collectors to run"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    phase = args.phase.zfill(2)
    target = REPO_ROOT / "docs" / "evidence" / f"phase-{phase}"
    target.mkdir(parents=True, exist_ok=True)

    selected = (
        [c.strip() for c in args.only.split(",") if c.strip()] if args.only else list(COLLECTORS)
    )
    session = boto3.Session(region_name=REGION)

    snapshot: dict[str, Any] = {}
    for name in selected:
        collector = COLLECTORS.get(name)
        if collector is None:
            print(f"  {name:<14} unknown collector, skipped")
            continue
        try:
            data = collector(session)
        except Exception as exc:  # collectors must never abort the capture
            data = {"error": f"{type(exc).__name__}: {exc}"[:300]}
        snapshot[name] = data
        write_text_lf(
            target / f"{name}.json",
            json.dumps(data, indent=2, default=str) + "\n",
        )
        status = "error" if isinstance(data, dict) and "error" in data else "ok"
        print(f"  {name:<14} {status}")

    summary = write_summary(target, phase, snapshot, args.note)
    print(f"\nevidence -> {target}")
    print(f"summary  -> {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
