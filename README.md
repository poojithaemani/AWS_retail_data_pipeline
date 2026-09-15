# AWS Retail Data Pipeline

A production-style retail/e-commerce data platform on AWS. Raw CSV deliveries
land in an immutable S3 lake, are catalogued, transformed by Glue PySpark into a
curated Parquet fact, governed by Lake Formation, served to Athena and a
Redshift star schema, and orchestrated end to end by EventBridge and Step
Functions.

Built as a nine-phase exercise with one rule that shaped everything: **all
compute is destroyed at the end of every session, so the evidence has to outlive
the infrastructure.**

Start with **[`docs/capstone.md`](docs/capstone.md)** — it walks the whole
project in fifteen minutes and needs no AWS access.

---

## What exists

```
              daily CSV deliveries
                       |
                       v
            S3  raw/   immutable, append-only
                       |
            Glue crawler -> Data Catalog (training_db)
                       |
            Glue 5.0 PySpark ETL
              dedupe -> clean -> validate -> enrich -> transform
                       |
          +------------+------------+
          v                         v
    curated/sales             quarantine/sales
    Parquet, y/m/d            with rejection_reason
          |                         |
          +------------+------------+
                       |
        Athena                Redshift
        (governed)            COPY + Spectrum
                       |
          Lake Formation governs every catalog read
                       |
        EventBridge -> Step Functions ties it together
```

| | |
| --- | --- |
| Raw | 15,861 orders, 45 daily partitions, 1,000 customers, 200 products |
| Curated | 12,060 rows, $10,705,326.72 |
| Quarantined | 40 rows, all `orphan_product_id` |
| Warehouse | `fact_orders` + `dim_customer` / `dim_product` / `dim_date` |
| Tests | 67, none requiring AWS |
| Total spend | under $2 across all nine phases |

Every figure above is in `docs/evidence/`, captured while the resources were
alive and committed before they were destroyed.

---

## Layout

```
config/project.env        single source of truth: region, project, TF_VAR_*
infrastructure/
  persistent/             never destroyed: KMS, IAM, lake bucket, budgets,
                          Lake Formation registration
  training/               destroyed every session: Glue, Athena, Redshift,
                          Step Functions, EventBridge, LF grants
scripts/de.sh             the only entry point; everything else is called by it
scripts/                  runners: crawler, ETL, DQ, Athena, Redshift, personas
sql/athena/               Phase 1-5 queries
sql/redshift/             Phase 7 star schema: ddl, load, analytics, spectrum
src/generate/             seeded dataset generator and defect injectors
src/retail_pipeline/      the ETL transformations (pure PySpark, unit tested)
tests/                    68 tests, no AWS required
docs/capstone.md          START HERE - the 15-minute walkthrough
docs/learnings.md         per-phase write-up, including every failure
docs/decisions.md         D1-D27, the reasoning behind each choice
docs/troubleshooting.md   real incidents with diagnosis
docs/evidence/phase-NN/   captured proof, per phase
docs/cost-log.md          a teardown row per session
architecture/             diagram (.drawio, .png) and notes
```

---

## The two-layer split

Terraform is split by **lifecycle**, not by service.

| | `persistent/` | `training/` |
| --- | --- | --- |
| Lifetime | months | hours |
| Holds | KMS key, IAM roles, lake bucket, budgets, LF registration | Glue, Athena, Redshift, Step Functions, EventBridge, LF grants |
| Destroyed by | `nuke` only | `down`, every session |

A single state file would have forced a choice between destroying the KMS key
that decrypts yesterday's data and protecting so much that teardown stopped
meaning anything.

Consequence worth knowing: **Lake Formation grants live in the training layer**,
so they are destroyed nightly and must be re-applied before the crawler or ETL
will run. Since `IAM_ALLOWED_PRINCIPALS` was removed in Phase 6, that is not
optional — the pipeline cannot read its own catalog without them.

---

## The session loop

```bash
./scripts/de.sh up NN        # create the training layer, tagged Phase=NN
#   ... phase work ...
./scripts/de.sh evidence NN  # capture proof while it is alive
git commit                   # the evidence outlives the infrastructure
./scripts/de.sh down         # destroy everything ephemeral
./scripts/de.sh verify NN    # prove it, by API call, across four regions
```

`verify` makes ~37 calls and asserts zero project-named resources, then appends
a row to `docs/cost-log.md`. It takes about two minutes.

---

## Getting started

```bash
# 1. Dependencies: Terraform 1.15+, AWS CLI v2, Python 3.11+, credentials
py -3 -m pip install -r requirements-dev.txt

# 2. Set the budget alert address - bootstrap refuses to run without a real one
cp infrastructure/persistent/terraform.tfvars.example \
   infrastructure/persistent/terraform.tfvars
#    *.tfvars is gitignored, so personal values never reach the repo

# 3. One-time: state bucket, KMS, IAM, lake bucket, budgets
./scripts/de.sh bootstrap

# 4. Generate the dataset and load the raw layer
./scripts/de.sh gen --end-date 2026-08-31
./scripts/de.sh load raw

# 5. Bring up a phase, work, tear down
./scripts/de.sh up 03
./scripts/de.sh crawl && ./scripts/de.sh publish && ./scripts/de.sh runjob
./scripts/de.sh down && ./scripts/de.sh verify 03
```

Run `./scripts/de.sh help` for the full command list. Tests need no AWS:

```bash
py -3 -m pytest -q
```

---

## Commands

| | |
| --- | --- |
| `bootstrap` | one-time: state bucket + persistent layer |
| `up [PHASE]` | create the training layer |
| `gen` / `load` | generate the dataset / upload to `raw/` |
| `crawl` / `publish` | discover schemas / rename to the graded table names |
| `runjob` | run the curated-sales Glue ETL |
| `dq` | evaluate the Glue Data Quality rulesets |
| `analytics` | Athena queries with bytes-scanned comparisons |
| `personas` | verify the Lake Formation permission matrix |
| `warehouse [STAGE]` | Redshift: `ddl`, `load`, `analytics`, `spectrum` |
| `orphans` / `breakschema` | deliberate defect deliveries |
| `evidence PHASE` | capture proof of execution |
| `down` / `verify` | destroy / prove nothing is running |
| `status` / `plan` / `fmt` | inspect, plan, format |
| `nuke` | destroy everything, persistent included |

---

## Dataset

Synthetic, seeded, no Faker. **Reproducibility beats realism**: the raw layer is
rebuilt after every teardown and must be byte-identical, so the same seed always
produces the same data.

That decision paid for itself when an S3 lifecycle rule deleted the raw layer —
it was regenerated exactly rather than lost. See `docs/troubleshooting.md`.

Two defect deliveries are permanent parts of `raw/`, because the raw layer holds
what the source actually sent:

- **orphan products** (`2026-09-02`) — 40 orders referencing products that do
  not exist, which the ETL rejects with a reason
- **an incremental tranche** (`2026-09-05`) — 2,000 new orders used to prove job
  bookmarks process only what is new

---

## Phases

| Phase | Focus | Weight |
| --- | --- | ---: |
| 0 | Foundation, guardrails, teardown proof | — |
| 1 | Data lake, partitioning and format benchmarks | 10% |
| 2 | Catalog, crawlers, schema drift | 10% |
| 3 | PySpark ETL, orphan rejection, reconciliation | 20% |
| 4 | Job bookmarks and incremental processing | 10% |
| 5 | Data quality, quarantine, Athena cost | 10% |
| 6 | Lake Formation: three personas, column-level access | 10% |
| 7 | Redshift star schema, COPY vs Spectrum | 10% |
| 8 | EventBridge and Step Functions, retry and catch | 10% |
| 9 | Capstone and evidence pack | 10% |

Out of scope by design: Iceberg, CDC/DMS, cross-account Lake Formation (all
optional in the brief) and streaming (0% of the rubric, and the only component
that would run continuously).

---

## Cost

Under **$2** across all nine phases. The only standing cost is the KMS key
(~$1/month); everything else exists for minutes at a time.

Guardrails: daily and monthly budget alarms, a 7-day lifecycle on derived
prefixes (never on `raw/`), no NAT gateway anywhere, and `verify` refusing to
report clean while anything billable is still running.
