#!/usr/bin/env python3
"""Publish crawler-discovered schemas under the contract-required table names.

The brief requires `customers_raw`, `products_raw`, `orders_raw` in
`training_db`. A Glue crawler cannot produce those names. Verified against the
AWS Glue Developer Guide rather than assumed:

    "The name of the table is based on the Amazon S3 prefix or folder name."
    "If duplicate table names are encountered, the crawler adds a hash string
     suffix to the name."
        - docs.aws.amazon.com/glue/latest/dg/add-crawler.html

Naming is controlled only by the S3 folder and an optional *prefix*. There is
no suffix, no custom naming, and no documented behaviour where a crawler adopts
an existing table because its location matches. The guide's own suggested
workaround is to "use the Glue API to modify table names after creation", which
is what this does.

So the division of labour is:

    the crawler   discovers the schema, the column types and the partitions
    this script   publishes that discovery under the required names

The intermediate tables the crawler created are then removed, so the catalog
ends up holding exactly the three tables the contract asks for and nothing
else. That deletion is Glue Data Catalog metadata only - it does not touch a
single S3 object, and cannot: the raw layer forbids deletes.

The operational cost of this arrangement is real and worth stating: **every
crawl must be followed by a publish**, or the required tables go stale while
the crawler-named ones hold the current schema. In production the honest fix is
to name the S3 prefixes to match the table names you want from the start -
which is only possible before any data lands, and is precisely the decision an
immutable raw layer stops you revisiting.

Usage::

    ./scripts/de.sh publish            crawl output -> required names
    ./scripts/de.sh publish --check    verify only, change nothing
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:  # pragma: no cover
    sys.exit("boto3 is required: py -3 -m pip install -r requirements.txt")

REPO_ROOT = Path(__file__).resolve().parents[1]

# What the crawler will call each table (from the S3 folder) mapped to what the
# brief requires it to be called.
RENAMES = {
    "customers": "customers_raw",
    "products": "products_raw",
    "orders": "orders_raw",
}

# The drift fixture keeps its crawler-derived name. It is an exhibit, not part
# of the contract, and naming it `*_raw` would imply it belongs in the raw
# layer - which is the opposite of the point.
FIXTURE_TABLE = "products_drift"

REQUIRED = set(RENAMES.values())

# ---------------------------------------------------------------------------
# Declared schemas - where inference could not be trusted.
#
# The crawler got products and orders right and customers wrong, in the same
# run. Glue's CSV classifier decides a file has a header by comparing the first
# row's types against the rows below it:
#
#     products    price is double in the data, "price" is text      -> detected
#     orders      quantity is bigint, "quantity" is text            -> detected
#     customers   every column is a string in BOTH header and data  -> ambiguous
#
# With no signal to work from it declined to guess, named the columns
# col0..col4 and returned the header row as data - 1001 rows for a 1000-row
# file. A custom CSV classifier with ContainsHeader = PRESENT and an explicit
# header list was added and did NOT resolve it; see infrastructure/training/
# classifier.tf, which is deliberately kept so the attempt stays visible.
#
# So this table's schema is DECLARED rather than discovered. That is not a
# workaround, it is the answer to "crawlers or hand-written schemas?" - the
# crawler is authoritative about what the files contain, right up until it
# isn't, and nothing in its output tells you which table to distrust. Something
# has to state the contract.
#
# The declaration below matches sql/athena/05_raw_tables.sql, which is the
# hand-written definition of the same table.
# ---------------------------------------------------------------------------
DECLARED_SCHEMAS: dict[str, dict[str, Any]] = {
    "customers_raw": {
        "reason": (
            "all-string columns give the CSV classifier no way to distinguish "
            "the header from the data; a custom classifier did not fix it"
        ),
        "columns": [
            {"Name": "customer_id", "Type": "string"},
            {"Name": "customer_name", "Type": "string"},
            {"Name": "email", "Type": "string"},
            {"Name": "country", "Type": "string"},
            # Left as string on purpose. The raw layer holds what the source
            # sent; parsing dates is Phase 3's job, where a bad value can be
            # quarantined instead of silently failing a cast in the catalog.
            {"Name": "created_date", "Type": "string"},
        ],
        "parameters": {"skip.header.line.count": "1"},
    },
}


def apply_declared_schema(glue: Any, name: str) -> bool:
    """Overwrite a published table's columns with the declared contract.

    Applied after republishing, so the crawler's discovery is used for
    everything it got right and replaced only where it demonstrably failed.
    """
    declared = DECLARED_SCHEMAS.get(name)
    if not declared:
        return False

    table = table_definition(glue, name)
    if table is None:
        print(f"  skip     {name} (not present)")
        return False

    payload = {
        key: table[key]
        for key in ("StorageDescriptor", "PartitionKeys", "TableType", "Parameters")
        if key in table
    }
    payload["Name"] = name
    payload["StorageDescriptor"] = dict(payload["StorageDescriptor"])
    payload["StorageDescriptor"]["Columns"] = declared["columns"]
    payload["Parameters"] = {**payload.get("Parameters", {}), **declared["parameters"]}

    glue.update_table(DatabaseName=DATABASE, TableInput=payload)
    print(f"  declared {name}")
    print(f"           {declared['reason']}")
    print(f"           columns: {', '.join(c['Name'] for c in declared['columns'])}")
    return True


def env(name: str, default: str) -> str:
    for line in (REPO_ROOT / "config" / "project.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(f"export {name}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return default


REGION = env("AWS_REGION", "us-east-2")
DATABASE = env("GLUE_DATABASE", "training_db")


def table_definition(glue: Any, name: str) -> dict[str, Any] | None:
    try:
        return glue.get_table(DatabaseName=DATABASE, Name=name)["Table"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "EntityNotFoundException":
            return None
        raise


def republish(glue: Any, source: dict[str, Any], new_name: str) -> None:
    """Recreate a table under a different name, schema and all.

    Glue has no rename API. The table definition is copied wholesale - columns,
    partition keys, SerDe, location, parameters - so the published table is the
    crawler's discovery verbatim rather than a hand-written approximation of it.
    """
    payload = {
        key: source[key]
        for key in (
            "StorageDescriptor",
            "PartitionKeys",
            "TableType",
            "Parameters",
        )
        if key in source
    }
    payload["Name"] = new_name

    if table_definition(glue, new_name):
        glue.update_table(DatabaseName=DATABASE, TableInput=payload)
        print(f"  updated  {new_name}")
    else:
        glue.create_table(DatabaseName=DATABASE, TableInput=payload)
        print(f"  created  {new_name}")


def copy_partitions(glue: Any, source_name: str, target_name: str) -> int:
    """Partitions are per-table, so they must be copied too.

    Without this, orders_raw would have the right schema and zero partitions -
    a table that returns no rows while looking entirely healthy, which is the
    most misleading failure mode available.

    Known limitation, stated rather than discovered later: this ADDS partitions
    and never removes them. If a partition disappears upstream, the published
    table keeps pointing at it and queries fail on a missing S3 prefix. That is
    acceptable for an append-only raw layer where partitions only ever arrive -
    which is exactly what the Phase 1 immutability control guarantees - and it
    would not be acceptable for a mutable dataset.
    """
    moved = 0
    paginator = glue.get_paginator("get_partitions")
    for page in paginator.paginate(DatabaseName=DATABASE, TableName=source_name):
        batch = []
        for part in page.get("Partitions", []):
            batch.append(
                {
                    "Values": part["Values"],
                    "StorageDescriptor": part["StorageDescriptor"],
                    "Parameters": part.get("Parameters", {}),
                }
            )
        for index in range(0, len(batch), 100):  # BatchCreatePartition caps at 100
            chunk = batch[index : index + 100]
            result = glue.batch_create_partition(
                DatabaseName=DATABASE, TableName=target_name, PartitionInputList=chunk
            )
            errors = [
                e for e in result.get("Errors", [])
                if e.get("ErrorDetail", {}).get("ErrorCode") != "AlreadyExistsException"
            ]
            if errors:
                print(f"  WARN {target_name}: {len(errors)} partition error(s): {errors[:2]}")
            moved += len(chunk)
    return moved


def audit(glue: Any) -> dict[str, Any]:
    """What the catalog actually holds, and whether that is what was asked for.

    Deliberately strict. The failure this guards against is a crawler quietly
    creating `products` beside `products_raw`, or a hash-suffixed duplicate
    like `products_a1b2c3`, and everything appearing to work because the
    required names are present. Extra tables are reported as a failure, not
    ignored.
    """
    names = set()
    paginator = glue.get_paginator("get_tables")
    for page in paginator.paginate(DatabaseName=DATABASE):
        names.update(t["Name"] for t in page.get("TableList", []))

    allowed = REQUIRED | {FIXTURE_TABLE}
    missing = sorted(REQUIRED - names)
    unexpected = sorted(names - allowed)

    return {
        "database": DATABASE,
        "tables": sorted(names),
        "required": sorted(REQUIRED),
        "missing": missing,
        "unexpected": unexpected,
        "ok": not missing and not unexpected,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify only, change nothing")
    parser.add_argument("--keep-intermediates", action="store_true",
                        help="leave the crawler-named tables in place")
    args = parser.parse_args(argv)

    glue = boto3.client("glue", region_name=REGION)

    if not args.check:
        print(f"== publishing into {DATABASE}")
        for crawler_name, required_name in RENAMES.items():
            source = table_definition(glue, crawler_name)
            if source is None:
                print(f"  skip     {crawler_name} -> {required_name} (crawler table absent)")
                continue

            republish(glue, source, required_name)
            if source.get("PartitionKeys"):
                moved = copy_partitions(glue, crawler_name, required_name)
                print(f"           {moved} partitions copied")

            if not args.keep_intermediates:
                # Catalog metadata only. No S3 object is touched, and none
                # could be: raw/ denies DeleteObject.
                glue.delete_table(DatabaseName=DATABASE, Name=crawler_name)
                print(f"  removed  {crawler_name} (intermediate)")

    if not args.check:
        print("\n== declared schemas (where inference could not be trusted)")
        for name in DECLARED_SCHEMAS:
            apply_declared_schema(glue, name)

    report = audit(glue)
    print(f"\n== catalog audit: {DATABASE}")
    for name in report["tables"]:
        mark = "required" if name in REQUIRED else ("fixture" if name == FIXTURE_TABLE else "UNEXPECTED")
        print(f"  {name:<24} {mark}")

    if report["missing"]:
        print(f"\n  MISSING required tables: {report['missing']}")
    if report["unexpected"]:
        print(f"\n  UNEXPECTED tables present: {report['unexpected']}")
        print("  A crawler that produced these has not been understood. Investigate")
        print("  before treating any downstream result as valid.")

    out = REPO_ROOT / "docs" / "evidence" / "phase-02"
    out.mkdir(parents=True, exist_ok=True)
    (out / "catalog-audit.json").write_bytes(
        (json.dumps(report, indent=2) + "\n").encode("utf-8")
    )

    print(f"\n  {'PASS' if report['ok'] else 'FAIL'} - audit written to {out / 'catalog-audit.json'}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
