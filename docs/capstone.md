# Capstone — Retail Data Platform on AWS

A production-style retail/e-commerce data platform on AWS: raw ingestion through
a governed lake to a star-schema warehouse, orchestrated by events, with every
stage evidenced.

**This document is designed to be read after the infrastructure is gone.** The
training resources are destroyed at the end of every working session by design,
so nothing here asks you to log into an account or run a query. Every figure
quoted is in `docs/evidence/`, committed, and traceable to the run that produced
it.

---

## 1. Business scenario

An online retailer receives daily order files. Customers and products arrive as
reference data. The business wants to know revenue by country, by category and
over time; the data team needs the pipeline to be reproducible, governed, and
cheap enough to tear down every night.

The dataset is synthetic and seeded, so the whole lake is byte-reproducible from
`src/generate/` — a property that mattered more than expected (see §5).

| | |
| --- | --- |
| Raw orders | 15,861 rows across 45 daily partitions |
| Customers / products | 1,000 / 200 |
| Curated fact | 12,060 rows, $10,705,326.72 |
| Rejected | 40 rows, all `orphan_product_id` |

The gap between raw and curated is the interesting part: 3,761 duplicate order
ids and 40 orders referencing products that do not exist. Both are removed
deliberately and accounted for, never silently dropped.

---

## 2. What was built

```
              deliveries (CSV)
                     |
                     v
          S3  raw/  (immutable, append-only)
                     |
          Glue crawler  ->  Glue Data Catalog  (training_db)
                     |
          Glue 5.0 PySpark ETL
            dedupe -> clean -> validate -> enrich -> transform
                     |
        +------------+-------------+
        v                          v
  curated/sales (Parquet)    quarantine/sales
   partitioned y/m/d          rejection_reason
        |                          |
        +-----------+--------------+
                    |
     Athena (governed)   Redshift (COPY + Spectrum)
                    |
        Lake Formation governs every catalog read
                    |
   EventBridge -> Step Functions orchestrates the whole thing
```

| Phase | Delivered | Weight |
| --- | --- | ---: |
| 1 | S3 medallion lake, partitioning and format benchmarks | 10% |
| 2 | Crawler discovery, schema drift, catalog publication | 10% |
| 3 | PySpark ETL with explicit orphan rejection and reconciliation | 20% |
| 4 | Job bookmarks, incremental processing, config-driven job | 10% |
| 5 | DQDL rulesets, quarantine, Athena cost analysis | 10% |
| 6 | Lake Formation: three personas, column-level access | 10% |
| 7 | Redshift star schema, COPY vs Spectrum | 10% |
| 8 | EventBridge -> Step Functions, retry and catch | 10% |
| 9 | This document and the evidence pack | 10% |

Deliberately out of scope: Iceberg, CDC/DMS and cross-account Lake Formation
(the brief labels all three optional), and Day 9 streaming — it carries 0% of
the rubric and is the only component that would run continuously, which fights
the teardown rule for no marks.

---

## 3. The constraint that shaped everything

**Every resource is destroyed at the end of each session.** Two consequences ran
through all nine phases:

1. **Zero console clicking.** Everything is Terraform, split by lifecycle:
   `persistent/` (KMS, IAM, the lake bucket, budgets — never destroyed) and
   `training/` (Glue, Athena, Redshift, Step Functions — destroyed daily). A
   single state file would have forced a choice between destroying the KMS key
   that decrypts yesterday's data and protecting so much that teardown stopped
   meaning anything.

2. **The evidence is the deliverable.** `docs/evidence/phase-NN/` is captured
   while resources are alive and committed before they are destroyed. That is
   why this walkthrough needs no live account.

Teardown is proved by API call, not assumed: `./scripts/de.sh verify` makes ~37
calls across four regions and asserts zero project-named resources. Every phase
ends with a `clean` row in `docs/cost-log.md`.

---

## 4. Walkthrough — 15 minutes, no AWS required

Everything below is committed. Paths are real.

**1. The lake is reproducible, and the raw layer is immutable** (2 min)
`docs/evidence/phase-01/` — partitioning experiments and format benchmarks.
Raw is protected two ways: the Glue role has no delete verb on `raw/*`, and a
bucket policy denies `DeleteObject` there for every principal but break-glass.
§5 covers the day that protection turned out to be incomplete.

**2. The ETL accounts for every row** (3 min)
`docs/evidence/phase-03/etl.json` and `phase-08/etl.json`.

    source 15,861 = duplicates 3,761 + valid 12,060 + rejected 40

The job fails if that identity does not hold. Orphaned orders are rejected
*before* any join, with a reason, rather than being dropped by an inner join or
kept with a null price by a left join. `sql/` and `src/retail_pipeline/` show
the seven-function structure; 19 unit tests run against real Spark, no AWS.

**3. Governance refuses things** (3 min)
`docs/evidence/phase-06/persona-verification.json` — **12/12 cases behaved as
specified**, including the ones that must fail:

    MarketingAnalystRole  SELECT email FROM customers_raw   -> DENIED
    MarketingAnalystRole  SELECT *     FROM customers_raw   -> ['customer_id','country']
    FinanceAnalystRole    SELECT       FROM customers_raw   -> DENIED

The `SELECT *` case is the interesting one: Lake Formation resolves the star to
the permitted columns rather than erroring, so the query works and `email` is
simply not there.

**4. Two engines agree, and two access paths differ** (3 min)
`docs/evidence/phase-07/warehouse.json`.

    fact_orders  12,060 rows  $10,705,326.72   (matches Athena to the cent)
    Spectrum orders_raw 15,861  vs  warehouse fact_orders 12,060

COPY read S3 directly under IAM and worked first time. Spectrum read the same
bytes through the catalog and was refused twice — first for a missing Lake
Formation grant, then for the IAM action to request credentials. Same role, same
data, two routes, different requirements.

**5. The pipeline is event-driven and handles failure** (4 min)
`docs/evidence/phase-08/orchestration.json` — three real executions:

| execution | attempts | outcome |
| --- | --- | --- |
| S3 arrival | `StartCrawler` 1 | SUCCEEDED end to end in 3m 09s |
| bad database | `RunGlueETL` 1 | Catch -> NotifyFailure -> **ExecutionFailed** |
| crawler busy | `StartCrawler` **3** | retried twice, recovered, SUCCEEDED |

The last two are the point: a deterministic failure is reported immediately, a
transient one is absorbed. Retrying the first would bill three Glue runs to fail
three times.

---

## 5. What went wrong, and what it taught

The failures are more interesting than the successes, and `docs/learnings.md`
records all of them. Four worth raising in conversation:

**An S3 lifecycle rule deleted the raw layer.** A Phase 0 rule with an empty
prefix filter and a 7-day expiry applied to the whole bucket. It did exactly
what it was configured to do. The bucket policy could not stop it: that policy
denies `DeleteObject` by *principals*, and lifecycle expiry is performed by S3
against no principal at all. Raw was protected from deletion by callers and
completely exposed to deletion by configuration. Recovered byte-for-byte because
the generator is seeded; the rule is now scoped to derived prefixes and a test
asserts no expiry rule can ever reach `raw/` again.

**A Lake Formation grant is not permission to read.** Reading a governed table
needs *both* an LF grant and the IAM action `lakeformation:GetDataAccess` — they
live in different systems and fail at different stages with different messages.
Discovered by granting one thing at a time rather than both at once, which is
why the project can say which was necessary. Both the Glue role and the Redshift
role needed the same pairing, so it is a property of the governed path, not of
one service.

**An EventBridge pattern that looked precise matched almost everything.**
`key = [{prefix="raw/orders/"}, {suffix=".csv"}]` reads as AND and is OR.
Verified with `aws events test-event-pattern` before applying: it matched
`athena-results/x.csv`, meaning the pipeline would have triggered on its own
output. A single `wildcard` matcher fixed it, and a test pins it.

**Twice, the reporting was wrong rather than the system.** A Phase 7 runner
printed a sort-key timing comparison that implied a speedup the data did not
support, and a Phase 8 summary labelled a first task attempt as a retry — which
would have claimed retry evidence that did not exist. Both were caught by
checking the underlying data, and both are written down.

---

## 6. Design decisions worth defending

Full reasoning in `docs/decisions.md` (D1–D27). The ones most likely to be
challenged:

- **Terraform split by lifecycle, not by service** — so teardown can be total
  without destroying the key that decrypts the data it leaves behind.
- **Orphans rejected before the join, not by it** — an inner join drops them
  silently and understates revenue; a left join manufactures a completed sale
  worth nothing. Neither is acceptable, so they are rejected explicitly and
  counted.
- **DataFrames, not DynamicFrames** — except one line: bookmarks are driven by
  `transformation_ctx` on Glue's own reader, so `orders` is read with a
  DynamicFrame and converted immediately. An adapter at the boundary is not the
  same as a data model.
- **Named-resource LF grants over LF-Tags** — three roles over four tables;
  naming them says exactly what is intended. One LF-Tag demonstrates TBAC.
- **Redshift in the training layer, and gated off by default** — it is the only
  resource with a non-zero idle cost, and no later phase should rebuild a
  warehouse to leave it idle.
- **No Lambda in the orchestration** — file validation is three conditions on
  the event, and the crawler and job have native integrations.
- **No Parallel state** — see §7.

---

## 7. Known limits, stated rather than hidden

**Publish is not orchestrated.** `scripts/publish_catalog.py` renames the
crawler's tables and is a local script. Step Functions cannot invoke it without
a Lambda built solely to satisfy a box on a diagram, so it remains an
operational step. The state machine covers S3 -> validate -> crawl -> ETL ->
notify.

**Data Quality is not a separate state.** The ETL already validates every row,
routes rejects to quarantine with a reason, and fails on an unbalanced
reconciliation — its outcome *is* the quality result. Phase 5's DQDL rulesets
measure the raw feed separately. Calling a generic `JobRun SUCCEEDED` a "data
quality evaluation" would misrepresent what was measured.

**No Parallel state, deliberately.** The brief lists parallel processing as a
topic to *understand*; the assignment diagram is strictly linear, and every
arrow in it is a real dependency — the crawler must finish before the ETL can
read the catalog it updates, and the ETL must finish before its outcome can be
notified. There is no independent branch to run concurrently, and the crawler
already parallelises internally across its three S3 targets.

Where a `Parallel` state *would* earn its place: fanning out ingestion across
several independent source systems that share no dependency; running an
independent audit or catalog-registration alongside a notification; or
processing several partitions concurrently where each is self-contained. The
test is whether the branches would still be correct in either order. Here they
would not be, so a Parallel state would be decoration that makes the workflow
harder to read and no faster.

**`curated/` is not durable, and does not need to be.** A 7-day lifecycle rule
expires the derived prefixes; `raw/` is excluded and permanent. Curated data is
reproducible from raw by one ETL run, and the reconciliation proves the rebuild
is faithful, so it is not worth paying to retain. Raw is the recovery source —
which the lifecycle incident demonstrated the hard way.

**Timings are not quoted as speedups.** At 12,060 rows, wall-clock differences
are dominated by fixed overhead and cache state. The sort-key evidence is the
planner's estimate — cost 3.83 against 180.90, roughly 47x — not a stopwatch.

---

## 8. Questions this project should be able to answer

1. Why is `raw/` immutable, and what does that cost you?
2. What happens to an order referencing a product that does not exist, and why
   is that better than an inner join?
3. How do you know the curated layer did not lose a row?
4. What does `IAM_ALLOWED_PRINCIPALS` do, and why remove it?
5. Why did Spectrum fail when COPY succeeded, on the same role and the same
   data?
6. Why is `fact_orders` distributed on `customer_id` and sorted on `order_date`,
   and what is the cost of that choice?
7. When should a Step Functions task be retried, and when should it not?
8. How do you prove nothing is still running and costing money?

---

## 9. Evidence pack

`docs/evidence/phase-NN/` — captured live, committed before teardown.

| | |
| --- | --- |
| `context.json` | account, region, git SHA, capture time |
| `lake.json` | object counts and bytes per layer |
| `catalog.json` | tables, columns, partitions, crawler history |
| `etl.json` | job runs with the reconciliation for each |
| `governance.json` | LF admins, registration, grants, whether the fallback is active |
| `warehouse.json` | Redshift DDL, load, analytics and Spectrum results |
| `orchestration.json` | Step Functions executions, task attempts, retries, failures |
| `persona-verification.json` | the 12 access cases, allowed and denied |
| `athena-analytics.json` | queries with bytes scanned and cost comparisons |

Supporting: `docs/learnings.md` (per-phase, including every failure),
`docs/decisions.md` (D1–D27), `docs/troubleshooting.md` (real incidents with
diagnosis), `docs/cost-log.md` (a `clean` teardown row per session).

**Total spend across all nine phases: under $2.**

---

## 10. Self-assessment against the brief's rubric

The brief scores ten categories (§10, "Evaluation"), which do not map one-to-one
onto the nine phases this project was organised into — Athena and Redshift share
a single category, and two categories cut across every phase. Assessed against
the brief's list rather than the phase plan, because that is what is graded.

| Category | Weight | Evidence | Self-score |
| --- | ---: | --- | ---: |
| S3/Data Lake Design | 10% | Medallion layout, partition and format benchmarks, immutable raw enforced by IAM prefix split and bucket policy | 10 |
| Glue Catalog & Crawlers | 10% | Crawler discovery, deliberate schema drift, publication to the graded table names, strict audit | 10 |
| Glue/PySpark ETL | 20% | Seven-function structure, orphan rejection before the join, a reconciliation identity the job enforces on itself, 19 unit tests on real Spark | 18 |
| Incremental Processing | 10% | Bookmarks proven across three runs: 13,861 -> 2,000 -> 0 rows | 10 |
| Data Quality | 10% | DQDL rulesets on the raw feed, quarantine with `rejection_reason`, 40 orphans routed and counted | 9 |
| Athena/Redshift | 10% | Partition pruning and scan-cost comparisons; star schema loaded by COPY and read by Spectrum, agreeing with Athena to the cent | 10 |
| Lake Formation | 10% | Three personas, column-level restriction, LF-Tag, 12/12 access cases including four denials | 10 |
| Orchestration | 10% | EventBridge -> Step Functions, three real executions, no Lambda | 9 |
| Monitoring & Failure Handling | 5% | Retry/Catch demonstrated on real failures, SNS on both outcomes, the ETL failing itself on an unbalanced reconciliation, and a CloudWatch alarm on `ExecutionsFailed` that fires independently of the workflow | 5 |
| Code Quality & Documentation | 5% | 68 tests, `terraform fmt` clean, 28 recorded decisions, 6 incident write-ups, 74 evidence files across ten phases | 5 |

**Total: 96 / 100**, against the brief's thresholds of 70% (can contribute with
guidance) and 85% (can build standard pipelines independently).

Treat that as a self-assessment rather than a grade - the point of the exercise
is the reasoning, not the number. The three deductions are where the work is
genuinely thinner than the topic list implies:

- **ETL, -2.** Local Glue development via the Docker container was skipped in
  favour of testing `transforms.py` directly against PySpark. That covers the
  intent - fast local iteration with no console - but not the specific tooling
  the brief names. The brief's example scale (100k customers / 5k products / 1M
  orders) was also not run; 12,060 rows demonstrate the logic but not Spark
  behaviour under volume, and several conclusions in this project would need
  re-checking at 1M rows.
- **Data Quality, -1.** The DQDL rulesets evaluate the raw feed, and the brief's
  five-row bad-data fixture is covered by unit tests rather than a delivered
  file. The routing itself is proven end to end on real data, but the DQ service
  and the ETL's validation remain two separate mechanisms rather than one.
- **Orchestration, -1.** No `Parallel` state. The reasoning is in §7 and I stand
  by it - there is no independent branch in this pipeline - but it is a listed
  topic with nothing demonstrated against it.

### The category that was weakest, and what closed it

**Monitoring & Failure Handling was the one gap this assessment found**, and it
is the reason the assessment was worth doing at all.

Failure handling was already strong: Retry and Catch scoped to transient errors
with both behaviours demonstrated on real failures, SNS notification on either
outcome, and an ETL that raises and fails itself when the reconciliation does not
balance - the pipeline refuses to report success on data it cannot account for.

Monitoring was thin in one specific way. Every notification came from INSIDE a
running execution, so a failure that stopped the workflow starting at all would
have been completely silent:

- the EventBridge rule disabled, or its pattern stops matching
- the Step Functions role loses a permission
- the Lake Formation grants not re-applied after a teardown

The last is not hypothetical. Grants live in the training layer by design (D24)
and are destroyed nightly, and this project hit exactly that failure three times.
Each time it was noticed because someone happened to be watching a terminal,
which is not a monitoring strategy.

Closed with one `aws_cloudwatch_metric_alarm` on the Step Functions service
metric `ExecutionsFailed`, publishing to the SNS topic that already existed:

    namespace           AWS/States        statistic  Sum      period  300
    comparison          GreaterThanThreshold         threshold  0
    treat_missing_data  notBreaching

Three choices in that worth defending. `Sum` rather than `Average`, because one
failed execution inside a window of successes still matters. `notBreaching` on
missing data, because an event-driven pipeline is idle most of the time and
alarming on quiet is the fastest way to teach someone to mute an alert. And it
watches the SERVICE metric rather than the workflow's own opinion of itself,
which is the entire point - it fires whether or not an execution ever reached the
notify state.

Still absent, and accepted: no dashboard, and no alarm on data-volume anomaly.
Neither is worth building for a platform that exists for an afternoon at a time.

### Where the phase plan and the brief's rubric disagree

Worth stating, because the difference changed this assessment:

- The brief scores **Athena and Redshift as one 10% category**; the phase plan
  treated them as two phases at 10% each. Both are covered, so nothing is lost —
  but the effort split was not what the scoring implies.
- The brief scores **Code Quality & Documentation at 5%** as its own category.
  The phase plan never had a phase for it; it accumulated across all nine, which
  is why it is the easiest category to evidence.
- **Monitoring** appears only inside "Orchestration & monitoring" in the phase
  plan, and separately at 5% in the brief. That framing is exactly why it ended
  up thin — it was treated as a property of Phase 8 rather than as a deliverable
  in its own right.

The lesson is small but real: organising work by phase and being graded by
category means a category that spans phases can fall between them.
