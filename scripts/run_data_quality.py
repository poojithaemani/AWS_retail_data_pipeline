"""Evaluate the Glue Data Quality rulesets and record the verdict.

Starts one evaluation run per ruleset against its catalog table, waits, and
writes every rule outcome to docs/evidence/phase-05/data-quality.json.

WHAT A FAILING RULE MEANS HERE
------------------------------
`Uniqueness "order_id" = 1.0` is expected to FAIL against the real lake, which
carries duplicate order_ids from two generator runs. That is the ruleset doing
its job, not a broken setup.

The pipeline already handles those duplicates: `deduplicate()` collapses them
before validation, so they never reach curated and never appear as rejections.
That is correct and completely silent. The DQ ruleset is what turns the silence
into a number - it reports how many duplicates the source actually contained,
which is a fact about the upstream feed that the ETL's success hides.

So this script does not treat a failing rule as an error. It exits non-zero only
if a run itself fails to complete.

Usage::

    ./scripts/de.sh dq
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import boto3

REPO_ROOT = Path(__file__).resolve().parents[1]

# ruleset name suffix -> the table it is bound to
RULESETS = {
    "orders-quality": "orders_raw",
    "products-quality": "products_raw",
}

TERMINAL = ("SUCCEEDED", "FAILED", "STOPPED", "TIMEOUT")


def env(name: str, default: str) -> str:
    for line in (REPO_ROOT / "config" / "project.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(f"export {name}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return default


def evaluate(glue: Any, ruleset: str, table: str, database: str, role: str) -> dict:
    started = glue.start_data_quality_ruleset_evaluation_run(
        DataSource={"GlueTable": {"DatabaseName": database, "TableName": table}},
        Role=role,
        RulesetNames=[ruleset],
        # The smallest allocation Glue accepts. These tables are megabytes; the
        # run is dominated by Spark startup either way, so more workers would
        # buy nothing and bill more.
        NumberOfWorkers=2,
        Timeout=20,
    )
    run_id = started["RunId"]
    print(f"  {ruleset} -> run {run_id}")

    while True:
        detail = glue.get_data_quality_ruleset_evaluation_run(RunId=run_id)
        status = detail["Status"]
        if status in TERMINAL:
            break
        time.sleep(10)

    record: dict[str, Any] = {
        "ruleset": ruleset,
        "table": table,
        "run_id": run_id,
        "status": status,
        "error": detail.get("ErrorString"),
        "rules": [],
    }

    for result_id in detail.get("ResultIds", []):
        result = glue.get_data_quality_result(ResultId=result_id)
        record["score"] = result.get("Score")
        for rule in result.get("RuleResults", []):
            record["rules"].append(
                {
                    "name": rule.get("Name"),
                    "result": rule.get("Result"),
                    "description": rule.get("Description"),
                    # Carries the observed value on failure, which is the part
                    # worth keeping: "0.73 does not meet 1.0" is the finding.
                    "message": rule.get("EvaluationMessage"),
                }
            )
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", default="05")
    args = parser.parse_args(argv)

    region = env("AWS_REGION", "us-east-2")
    database = env("GLUE_DATABASE", "training_db")
    project = env("PROJECT", "de-training")

    session = boto3.Session()
    account = session.client("sts", region_name=region).get_caller_identity()["Account"]
    role = f"arn:aws:iam::{account}:role/{project}-glue-role"
    glue = session.client("glue", region_name=region)

    print(f"== evaluating {len(RULESETS)} rulesets against {database}")
    records = [
        evaluate(glue, f"{project}-{suffix}", table, database, role)
        for suffix, table in RULESETS.items()
    ]

    print()
    incomplete = []
    for rec in records:
        if rec["status"] != "SUCCEEDED":
            incomplete.append(rec["ruleset"])
            print(f"  {rec['ruleset']}: run {rec['status']} - {str(rec.get('error'))[:120]}")
            continue
        score = rec.get("score")
        print(f"  {rec['ruleset']} on {rec['table']}  score={score}")
        for rule in rec["rules"]:
            mark = "PASS" if rule["result"] == "PASS" else "FAIL"
            print(f"    {mark}  {rule['name']}")
            if mark == "FAIL" and rule.get("message"):
                print(f"          {str(rule['message'])[:150]}")

    target = REPO_ROOT / "docs" / "evidence" / f"phase-{args.phase}"
    target.mkdir(parents=True, exist_ok=True)
    out = target / "data-quality.json"
    out.write_bytes((json.dumps({"rulesets": records}, indent=2, default=str) + "\n").encode("utf-8"))
    print(f"\n  evidence -> {out}")

    if incomplete:
        print(f"  runs that did not complete: {', '.join(incomplete)}")
        return 1
    # A failing RULE is a finding, not a script error - see the module docstring.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
