"""Prove the Lake Formation permission matrix by assuming each persona.

The brief's Day 6 exercise ends with "verify permissions using Athena", and
this is that verification. It assumes each persona role with STS and runs
Athena queries as that role - not as dev-user with a filter, which would prove
nothing about what the role can actually reach.

WHY THE DENIALS ARE THE POINT
-----------------------------
A permission model is only demonstrated by what it refuses. Every persona has
at least one query that MUST fail, and the most important assertion in this
phase is that MarketingAnalystRole cannot read `email` from customers_raw while
reading `customer_id` and `country` from the same table in the same breath.

So an expected denial is a PASS here, and an expected denial that unexpectedly
succeeds is a FAILURE - the loudest kind, because it means data is reachable by
someone who should not reach it. A verification script that only checks the
happy path would report success on a completely open lake.

THIS IS ONLY MEANINGFUL AFTER IAM_ALLOWED_PRINCIPALS IS REMOVED
---------------------------------------------------------------
While that default is in place, Lake Formation defers to IAM, and every one of
these roles can read everything their IAM policy permits. Run this before that
removal and the denial cases will "succeed" - the script says so rather than
reporting a false pass.

Usage::

    ./scripts/de.sh personas
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import boto3

REPO_ROOT = Path(__file__).resolve().parents[1]

# (persona key, sql, should_succeed, why)
#
# Each persona gets what the matrix grants and at least one thing it does not.
CASES: list[tuple[str, str, bool, str]] = [
    # DataEngineerRole - all three raw tables.
    ("data_engineer", "SELECT COUNT(*) FROM customers_raw", True, "matrix grants customers"),
    ("data_engineer", "SELECT COUNT(*) FROM products_raw", True, "matrix grants products"),
    ("data_engineer", "SELECT COUNT(*) FROM orders_raw", True, "matrix grants orders"),
    (
        "data_engineer",
        "SELECT COUNT(*) FROM quarantine_sales",
        True,
        "granted via the sensitivity=restricted LF-Tag, not by name",
    ),
    # FinanceAnalystRole - sales and orders, but no customer data.
    ("finance_analyst", "SELECT COUNT(*) FROM curated_sales", True, "matrix grants sales"),
    ("finance_analyst", "SELECT COUNT(*) FROM orders_raw", True, "matrix grants orders"),
    (
        "finance_analyst",
        "SELECT COUNT(*) FROM customers_raw",
        False,
        "customers is not in the finance matrix row",
    ),
    # MarketingAnalystRole - two columns of one table, and nothing else.
    (
        "marketing_analyst",
        "SELECT customer_id, country FROM customers_raw LIMIT 5",
        True,
        "the two columns the matrix permits",
    ),
    (
        "marketing_analyst",
        "SELECT email FROM customers_raw LIMIT 5",
        False,
        "THE column-level test - email is explicitly excluded",
    ),
    (
        "marketing_analyst",
        "SELECT * FROM customers_raw LIMIT 5",
        True,
        "SELECT * SUCCEEDS but Lake Formation resolves it to the granted columns only "
        "- see check_star_hides_email(), which asserts what actually comes back",
    ),
    (
        "marketing_analyst",
        "SELECT COUNT(*) FROM orders_raw",
        False,
        "orders is not in the marketing matrix row",
    ),
]


def env(name: str, default: str) -> str:
    for line in (REPO_ROOT / "config" / "project.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(f"export {name}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return default


def assume(sts: Any, role_arn: str, region: str) -> boto3.Session:
    creds = sts.assume_role(RoleArn=role_arn, RoleSessionName="phase6-verify")["Credentials"]
    return boto3.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
        region_name=region,
    )


def run(athena: Any, sql: str, database: str, workgroup: str) -> tuple[str, str | None]:
    try:
        qid = athena.start_query_execution(
            QueryString=sql,
            QueryExecutionContext={"Database": database},
            WorkGroup=workgroup,
        )["QueryExecutionId"]
    except Exception as exc:  # noqa: BLE001 - a refusal can arrive at submission
        return "FAILED", str(exc)[:200]

    while True:
        detail = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
        state = detail["Status"]["State"]
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            return state, detail["Status"].get("StateChangeReason")
        time.sleep(0.5)


def check_star_hides_email(session: Any, database: str, workgroup: str, region: str) -> dict:
    """`SELECT *` must return the granted columns and nothing else.

    This is the assertion the CASES table cannot make. A column-level grant does
    not make `SELECT *` fail - Lake Formation resolves the star against what the
    principal may see and returns those columns. The query succeeding is
    therefore the CORRECT outcome, and treating it as a denial (as an earlier
    version of this script did) reports a false alarm on a permission model that
    is working.

    What matters is not the query's status but its shape: the columns that come
    back. If `email` ever appears here, the column grant has stopped being
    enforced, and that is a real finding regardless of what the status says.
    """
    athena = session.client("athena", region_name=region)
    sql = "SELECT * FROM customers_raw LIMIT 3"
    state, reason = run(athena, sql, database, workgroup)
    result: dict[str, Any] = {
        "check": "select_star_returns_only_granted_columns",
        "sql": sql,
        "state": state,
        "reason": reason,
    }
    if state != "SUCCEEDED":
        result["as_expected"] = False
        return result

    # The query id is needed to read the column metadata back.
    qid = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": database},
        WorkGroup=workgroup,
    )["QueryExecutionId"]
    while True:
        detail = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
        if detail["Status"]["State"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(0.5)

    meta = athena.get_query_results(QueryExecutionId=qid)["ResultSet"]["ResultSetMetadata"]
    columns = [c["Name"] for c in meta["ColumnInfo"]]
    granted = {"customer_id", "country"}
    leaked = [c for c in columns if c not in granted]

    result["columns_returned"] = columns
    result["leaked_columns"] = leaked
    result["as_expected"] = not leaked
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", default="06")
    args = parser.parse_args(argv)

    region = env("AWS_REGION", "us-east-2")
    database = env("GLUE_DATABASE", "training_db")
    project = env("PROJECT", "de-training")
    workgroup = f"{project}-wg"

    root = boto3.Session()
    sts = root.client("sts", region_name=region)
    account = sts.get_caller_identity()["Account"]

    roles = {
        "data_engineer": f"arn:aws:iam::{account}:role/DataEngineerRole",
        "finance_analyst": f"arn:aws:iam::{account}:role/FinanceAnalystRole",
        "marketing_analyst": f"arn:aws:iam::{account}:role/MarketingAnalystRole",
    }

    sessions = {}
    for key, arn in roles.items():
        try:
            sessions[key] = assume(sts, arn, region)
            print(f"  assumed {arn.rsplit('/', 1)[1]}")
        except Exception as exc:  # noqa: BLE001
            print(f"  COULD NOT ASSUME {arn}: {str(exc)[:150]}")

    print()
    results = []
    for persona, sql, should_succeed, why in CASES:
        session = sessions.get(persona)
        if session is None:
            continue
        athena = session.client("athena", region_name=region)
        state, reason = run(athena, sql, database, workgroup)

        succeeded = state == "SUCCEEDED"
        # The assertion is on the *expectation*, not on the query state. An
        # expected denial that is refused is a pass.
        correct = succeeded == should_succeed
        verdict = "ok  " if correct else "WRONG"
        expectation = "allow" if should_succeed else "deny "

        print(f"  {verdict} [{expectation}] {persona:18} {sql[:52]}")
        if not correct:
            if succeeded and not should_succeed:
                print("        ^^ REACHED DATA IT SHOULD NOT - permission model not enforced")
            else:
                print(f"        ^^ refused but should have been allowed: {str(reason)[:120]}")
        results.append(
            {
                "persona": persona,
                "sql": sql,
                "expected": "allow" if should_succeed else "deny",
                "state": state,
                "as_expected": correct,
                "reason": reason,
                "why": why,
            }
        )

    star = None
    if "marketing_analyst" in sessions:
        star = check_star_hides_email(sessions["marketing_analyst"], database, workgroup, region)
        mark = "ok  " if star["as_expected"] else "WRONG"
        print()
        print(f"  {mark} [shape] marketing_analyst  SELECT * returns {star.get('columns_returned')}")
        if star.get("leaked_columns"):
            print(f"        ^^ LEAKED: {star['leaked_columns']} - column grant not enforced")
        results.append({**star, "persona": "marketing_analyst", "expected": "granted columns only"})

    wrong = [r for r in results if not r["as_expected"]]
    leaks = [r for r in wrong if r["expected"] == "deny"]

    print()
    print(f"  {len(results) - len(wrong)}/{len(results)} cases behaved as specified")
    if leaks:
        print(f"  {len(leaks)} DENIAL(S) DID NOT HOLD - if IAM_ALLOWED_PRINCIPALS is still")
        print("  present this is expected and the removal step has not been done yet.")

    target = REPO_ROOT / "docs" / "evidence" / f"phase-{args.phase}"
    target.mkdir(parents=True, exist_ok=True)
    out = target / "persona-verification.json"
    out.write_bytes(
        (json.dumps({"database": database, "cases": results}, indent=2, default=str) + "\n").encode(
            "utf-8"
        )
    )
    print(f"\n  evidence -> {out}")
    return 1 if wrong else 0


if __name__ == "__main__":
    raise SystemExit(main())
