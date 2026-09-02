"""The pipeline data contract, read at runtime rather than compiled in.

`config/pipeline.json` has existed since Phase 0 and, until now, nothing read
it. Every value in it was *also* written down somewhere else - table names as
an f-string in `extract()`, the partition columns as a default argument, the
compression codec as a literal, the curated and quarantine prefixes as Glue job
arguments in Terraform. Four copies of the same contract, none of them
authoritative, and no test that they agreed.

That is the gap the brief means by "configuration-driven development" (Day 4).
The point is not that a JSON file exists; it is that changing where curated
data lands should be one edit, applied without repackaging code.

WHAT BELONGS HERE, AND WHAT DOES NOT
------------------------------------
This file loads the *data contract*: which catalog tables feed the job, where
its output goes, how it is partitioned and compressed, what counts as a valid
order. Those are properties of the pipeline, and the job is the thing that
needs them.

`config/dev.json` is deliberately NOT loaded. Worker type, worker count,
retries, timeout and the bookmark setting are properties of the *job resource*,
owned by Terraform, and read by AWS before any of this code runs. A job cannot
configure its own worker count from inside itself, and pretending otherwise
would produce a file that looks authoritative and changes nothing. `dev.json`
documents what Terraform applies; it is not an input to the job.

Local paths and `s3://` URIs both work, so the same loader serves the unit
tests and the deployed job.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# The convention the brief fixes (p.7): raw catalog tables are <name>_raw.
# Used only when the contract does not name a table explicitly, so that a
# missing key degrades to the documented default rather than to a crash.
RAW_TABLE_SUFFIX = "_raw"

SOURCES = ("customers", "products", "orders")


def load_contract(source: str | Path) -> dict[str, Any]:
    """Read the contract from a local path or an s3:// URI.

    boto3 is imported lazily: the unit tests read a local file and must not
    need AWS credentials, or even the SDK, to run.
    """
    text = _read_s3(str(source)) if str(source).startswith("s3://") else Path(source).read_text(
        encoding="utf-8"
    )
    return json.loads(text)


def _read_s3(uri: str) -> str:
    import boto3

    bucket, _, key = uri[len("s3://") :].partition("/")
    body = boto3.client("s3").get_object(Bucket=bucket, Key=key)["Body"]
    return body.read().decode("utf-8")


def catalog_tables(contract: dict[str, Any]) -> dict[str, str]:
    """Logical dataset name -> Glue Catalog table name.

    Phase 2 published `customers_raw`, `products_raw`, `orders_raw` and the
    contract records them per dataset. Falling back to the suffix convention
    keeps a contract that omits `catalog_table` working.
    """
    datasets = contract.get("datasets", {})
    return {
        name: datasets.get(name, {}).get("catalog_table", f"{name}{RAW_TABLE_SUFFIX}")
        for name in SOURCES
    }


def curated_output(contract: dict[str, Any]) -> dict[str, Any]:
    """Where curated sales land, and in what shape."""
    sales = contract.get("curated", {}).get("sales", {})
    return {
        "prefix": sales.get("prefix", "curated/sales"),
        "partition_by": tuple(sales.get("partition_by", ("year", "month", "day"))),
        "compression": sales.get("compression", "snappy"),
        "format": sales.get("format", "parquet"),
    }


def quarantine_prefix(contract: dict[str, Any]) -> str:
    return contract.get("quality", {}).get("quarantine_prefix", "quarantine/sales")


def valid_statuses(contract: dict[str, Any]) -> tuple[str, ...]:
    """The order lifecycle the business recognises.

    Optional in the contract. When absent the module default in
    `transforms.VALID_STATUSES` applies, so an older contract keeps working.
    """
    statuses = contract.get("quality", {}).get("valid_order_statuses")
    return tuple(statuses) if statuses else ()
