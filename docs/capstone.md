# Capstone — Retail Data Platform on AWS

> Written first, deliberately. This document is the target every phase builds
> toward and the script for the walkthrough at the end. If a phase produces
> something that does not serve one of the acceptance criteria below, it was
> the wrong work.

---

## 1. Business scenario

An online retailer receives **customer**, **product** and **order** data every
day. Files arrive on object storage in whatever shape the source system felt
like emitting: CSV, inconsistent types, duplicated order lines, occasional
nulls where a foreign key should be.

The platform must provide:

| Requirement | Where it is satisfied |
| --- | --- |
| Raw data retention | Phase 1 — immutable, date-partitioned `raw/` layer |
| Data catalog | Phase 2 — Glue Crawlers into a Glue database |
| Data transformation | Phase 3 — Glue PySpark ETL |
| Data cleansing | Phase 3 — nulls, types, date standardisation, invalid-row filter |
| Deduplication | Phase 3 — deterministic winner selection, not blind `distinct` |
| Incremental processing | Phase 4 — Glue job bookmarks |
| Data quality | Phase 5 — Glue Data Quality (DQDL) with a quarantine branch |
| Data governance | Phase 6 — Lake Formation, column-level control |
| Analytical queries | Phase 5 / 7 — Athena, partition pruning, CTAS |
| Warehouse integration | Phase 7 — Redshift Serverless star schema |
| Orchestration | Phase 8 — EventBridge → Step Functions |
| Monitoring | Phase 8 — CloudWatch metrics, alarms, SNS |
| Failure handling | Phase 8 — Retry / Catch / quarantine / notify |

### Expected architecture

Reproduced from the brief (p.21–22) so the target is unambiguous:

```
                       SOURCE
                          │
                          ▼
                      S3 RAW
                          │
                          ▼
                    Glue Crawler
                          │
                          ▼
                   Glue Catalog                    ← training_db
                          │
                          ▼
                    Glue PySpark
                   /     |      \
             Validate   Join    Dedup
                   \     |      /
                          ▼
                   Data Quality
                          │
                   ┌──────┴──────┐
                 PASS           FAIL
                   │             │
                   ▼             ▼
              S3 Curated     Quarantine
                   │
             ┌─────┴──────┐
             ▼            ▼
          Athena       Redshift
             │
             ▼
         Analytics

Lake Formation  ──── Governance
EventBridge → Step Functions ──── Orchestration
CloudWatch      ──── Monitoring
```

Out of scope by instruction, and labelled "Optional Advanced Exercise" in the
brief itself: Apache Iceberg (p.19), CDC/DMS (p.20), cross-account Lake
Formation (p.14). Be ready to say what each would add — see `decisions.md`.

---

## 2. The constraint that makes this a real test

Anyone can leave a working pipeline running and demo it. This platform is
**destroyed at the end of every working session**, so the capstone is not
"show me your environment" — it is:

> Rebuild the entire platform from an empty AWS account with two commands, no
> console clicking, and no copy-pasting from earlier phases.

```bash
./scripts/de.sh bootstrap          # persistent layer: state, KMS, IAM, budget
./scripts/de.sh up 09              # everything else
```

If it only works because an earlier phase left something behind, it fails.
That is the point: teardown is not a cleanup chore bolted onto the end, it is
the forcing function that keeps every resource in version control.

---

## 3. Acceptance criteria

Numeric and checkable. "It looks right" is not a criterion.

| # | Criterion | How it is proven |
| --- | --- | --- |
| A1 | Row counts reconcile exactly across `raw → curated → Redshift` | `docs/evidence/phase-09/reconciliation.json` |
| A2 | All nine injected defect classes are detected; **zero** invalid rows reach `curated/` | Compare Glue DQ results against `data/test_fixtures/defects.json` ground truth |
| A3 | Quarantined row count equals the injected defect count | Same file — expected vs actual |
| A4 | `MarketingAnalystRole` cannot read `customers.email` | Athena query run under the assumed role fails with an authorisation error, captured |
| A5 | A second identical pipeline run reprocesses **0** rows | Glue job bookmark; two job-run records with row-count metrics |
| A6 | An injected stage failure is retried, quarantined and notified | Step Functions execution history showing Retry → Catch → quarantine → SNS |
| A7 | Partitioned Parquet scans < 5% of the bytes the equivalent raw CSV query scans | Athena `bytes_scanned` for the same logical query, both formats |
| A8 | Teardown leaves zero billable resources | `./scripts/de.sh verify` exits 0 |
| A9 | Two consecutive `apply` runs produce no drift | `terraform plan` reports `No changes` |

---

## 4. Failure drill

One pipeline stage is broken at random immediately before the walkthrough.
The demonstration is **detection → diagnosis → recovery**, live:

1. Which signal fired first, and how long did it take?
2. Where is the evidence — CloudWatch logs, Step Functions history, DQ result?
3. What is the blast radius — did bad data reach `curated/`, or stop at quarantine?
4. Recover, and show the pipeline re-run without reprocessing good data.

Candidate breakages: revoke a Lake Formation grant; corrupt the Glue job script;
point a crawler at the wrong prefix; introduce a schema change mid-partition;
delete a bookmark; make the DQ ruleset fail closed.

---

## 4a. Walkthrough script — 15 minutes

The brief (§9) asks for "a short walkthrough of the implementation". With the
infrastructure destroyed, the walkthrough runs off the repository and the
evidence pack rather than a live console.

| Min | Beat | Artefact |
| --- | --- | --- |
| 0–2 | **The problem.** Retailer, three daily feeds, dirty data, needs governed analytics | `capstone.md` §1 |
| 2–4 | **The architecture**, end to end in one pass | `architecture/retail-data-platform.drawio` (Architecture tab) |
| 4–6 | **The constraint that shaped it** — destroyed every session, so IaC-only, two layers split by lifecycle, teardown verified by API call | `decisions.md` D2, `verify_teardown.sh` |
| 6–9 | **The pipeline**, following one order row from `raw/` to `fact_orders` | `src/transform/`, `evidence/phase-09/` |
| 9–11 | **Where bad data goes.** Nine injected defect classes, ground truth, quarantine — *exactly* these rows, not "some" | `defects.json` vs `data_quality.json` |
| 11–13 | **Governance and the failure drill.** Marketing blocked on `email`; a broken stage retried, quarantined, notified | `governance.json`, Step Functions history |
| 13–15 | **Cost and trade-offs.** Parquet + pruning, no NAT Gateway, Redshift idle-to-zero, daily budget; what I would do differently | `cost-log.md`, `decisions.md` |

Openers worth having ready, because they are the questions a reviewer actually
asks:

- *"Why is there no NAT Gateway?"* — D5, including when it would be wrong.
- *"You used Glue Data Quality instead of the Deequ repo you were given."* —
  D8 and `reference-repos.md`.
- *"Show me something that failed."* — the failure drill, and the `set -e`
  and CRLF bugs the test suite caught during Phase 0.

---

## 5. The eight questions

Answered in writing, in `docs/learnings.md`, with the reasoning behind each
design choice in `docs/decisions.md` and the sample-repository study in
`docs/reference-repos.md`. Defensible out loud.

1. **What does the architecture do?** End-to-end, in under two minutes.
2. **Why each service?** For every box: what job it does that a neighbouring service could not do as well.
3. **What alternatives exist?** Glue vs EMR vs Lambda; Athena vs Redshift; Step Functions vs Airflow/MWAA; Deequ vs Glue Data Quality; Iceberg vs plain Parquet. Including when the alternative is the better call.
4. **What happens when a component fails?** Per component: blast radius, detection, recovery, whether it fails open or closed.
5. **How does it scale?** From 1M orders to 1B: what breaks first, what the fix is, where the small-file problem bites, when partitioning stops helping.
6. **How is security applied?** IAM vs Lake Formation, encryption at rest and in transit, least privilege, column-level access, where secrets live.
7. **How is it monitored?** Metrics that matter, alarm thresholds and why those numbers, what is deliberately not alarmed.
8. **How is cost optimised?** Parquet + partition pruning, bucket keys, no NAT Gateway, Redshift Serverless idle-to-zero, lifecycle expiry, workgroup scan limits — with the actual figures from `docs/cost-log.md`.

---

## 6. Evidence pack

Because the infrastructure is gone, `docs/evidence/` is the deliverable:

```
docs/evidence/phase-NN/
  README.md          human-readable summary of the session
  context.json       account, region, git commit, capture time
  lake.json          object counts and bytes per lake layer
  glue.json          catalog schemas, crawler outcomes, job runs + DPU-hours
  data_quality.json  DQDL rulesets and per-rule verdicts
  athena.json        queries with bytes scanned and estimated cost
  orchestration.json Step Functions executions, EventBridge rules
  governance.json    Lake Formation grants as the service sees them
  warehouse.json     Redshift namespace/workgroup state
  monitoring.json    alarm states and reasons
  terraform.json     what each layer believes it manages
```

---

## 7. Self-assessment against the rubric

| Category | Weight | Evidence |
| --- | ---: | --- |
| S3 / data lake design | 10% | Phase 1 — layer separation, partitioning, format benchmark |
| Glue Catalog & Crawlers | 10% | Phase 2 — catalog + the schema-inconsistency troubleshooting |
| Glue / PySpark ETL | 20% | Phase 3 — modular `extract/validate/clean/dedup/transform/enrich/write` |
| Incremental processing | 10% | Phase 4 — bookmarks, proven with a zero-row second run |
| Data quality | 10% | Phase 5 — DQDL, quarantine, ground-truth assertion |
| Athena / Redshift | 10% | Phases 5 & 7 — pruning, CTAS, star schema, dist/sort tuning |
| Lake Formation | 10% | Phase 6 — three personas, column-level deny proven |
| Orchestration | 10% | Phase 8 — EventBridge → Step Functions with failure paths |
| Monitoring & failure handling | 5% | Phase 8 — alarms, SNS, the failure drill |
| Code quality & documentation | 5% | The repository itself: IaC-only, tested, documented, reproducible |
