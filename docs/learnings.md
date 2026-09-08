# Learnings

One section per phase. Written at the end of each session, while the details
are still fresh — these notes are the raw material for the eight capstone
questions in [capstone.md](capstone.md).

Each entry answers four things:

- **Built** — what actually got created
- **Concepts** — what the phase was really teaching, in my own words
- **Broke** — the failure introduced on purpose, and what it looked like
- **Fixed** — the diagnosis path, not just the fix

---

## Phase 0 — Foundation & guardrails

**Built**

- Two-layer Terraform split: `persistent/` (state, KMS, IAM, lake bucket,
  budget) and `training/` (everything destroyable).
- `scripts/de.sh` as the single entry point; `verify_teardown.sh` as the proof.
- Seeded synthetic retail generator, defaulting to development scale
  (1k customers / 200 products / 10k orders) with the brief's 100k/5k/1M
  figures passed explicitly at the Spark scaling exercise. Nine defect
  classes injected from a disjoint row pool, with a ground-truth manifest.
- Evidence harness writing structured snapshots to `docs/evidence/`.

**Concepts**

- *Why two layers.* A single Terraform state would mean `destroy` either takes
  the state bucket and KMS key with it, or protects so much that the teardown
  stops being meaningful. Splitting by lifecycle — not by service — is what
  makes a daily destroy safe.
- *Why the state bucket is not Terraform-managed.* Terraform cannot own the
  bucket holding its own state without a local-state-then-migrate dance that
  leaves a stray state file on every machine. One idempotent CLI call in
  `bootstrap` is simpler and has no failure mode.
- *Why no NAT Gateway.* Nothing in the mandatory scope needs Glue inside a
  VPC — Glue reaches S3, the Catalog and Redshift over AWS-managed networking.
  A NAT Gateway would add roughly $33/month for zero capability. It becomes
  necessary only when reaching a private source such as RDS or on-prem.
- *Why S3 native state locking.* `use_lockfile = true` replaces the DynamoDB
  lock table: one less resource, one less cost line, one less thing that can
  survive a teardown.
- *Why determinism in the generator.* If the raw layer is regenerated after
  every teardown, the incremental-processing and data-quality baselines are
  only meaningful when the data is identical byte for byte.

**Broke**

Two real failures, neither of them planned. Both are written up in full in
[troubleshooting.md](troubleshooting.md); the short version and the lesson:

1. **A comma in an IAM tag value stopped `terraform apply` 22 resources into
   25.** `Purpose = "Retail order ETL: crawl, clean, deduplicate, join, curate"`
   — IAM tag values permit only `[\p{L}\p{Z}\p{N}_.:/=+\-@]`, and a comma is
   not in that set.

2. **Git Bash rewrote a `/`-prefixed CLI argument into a Windows path.**
   `--log-group-name-prefix '/aws-glue/jobs'` reached AWS as
   `D:/Git/aws-glue/jobs` and failed validation. The tell was that the error's
   own regex permits `/`, so the value being rejected could not be the value
   typed.

**Fixed**

1. Slashes instead of commas. The diagnosis mattered more than the fix: the
   error names the constraint but not the offending value, so I extracted every
   tag value in the Terraform and tested each against the pattern. Exactly one
   failed. Then I checked *why it surfaced so late* — the Redshift role has a
   `Purpose` tag too, but with no commas, and the S3 and KMS tags contain commas
   quite happily. **S3 and KMS accept commas in tag values; IAM does not.**
   Terraform builds in dependency order, so the strictest validator in the graph
   was among the last things reached.

2. `MSYS_NO_PATHCONV=1`. Then checked whether `verify_teardown.sh` carried the
   same latent bug — it does not, because it filters client-side with JMESPath
   `contains()` rather than passing a prefix to AWS.

Both are now guarded by tests, and the IAM one was verified by reintroducing the
comma and watching the test fail.

**The concepts these actually taught**

- ***`terraform validate` does not validate against AWS.*** It parses
  configuration and checks types. Service-side constraints — tag character
  sets, name lengths, value patterns — are enforced only at apply, and they
  differ per service. This is the single most useful thing Phase 0 taught, and
  it is not in the brief.
- **A partial apply is not a corrupt one.** Terraform recorded exactly what
  succeeded, so recovery was a re-plan showing `3 to add, 0 to change,
  0 to destroy` — no manual cleanup, no orphans, no drift. That is the argument
  for `plan → apply` over console clicking, demonstrated rather than asserted.
- **A guard that has never fired is a guard you have not tested.** Every
  hygiene test in this repo exists because something actually broke.

**Also corrected during the phase**

A review of the persistent-layer plan caught the Redshift role being granted
`AWSGlueConsoleFullAccess` — 49 actions across 13 services, including `glue:*`
and `cloudformation:DeleteStack` — plus the same read/write/delete lake policy
as the Glue ETL role. A role whose only job is `COPY` could have deleted the
catalog it reads from. Replaced with a purpose-built policy: `GetObject` on
`curated/*`, `ListBucket` conditioned on that prefix, `kms:Decrypt` on the lake
key, and six read-only `glue:Get*` actions scoped to `training_db` (D19).

That surfaced a related problem now recorded as **open**: the Glue role holds
`s3:DeleteObject` on `raw/*`, so "immutable raw" is currently a convention
rather than a control. It is deliberately left for the Phase 1 raw-immutability
decision (D20) rather than mixed into a settled change.

**Phase 0 result**

Persistent layer: 25 resources, `plan: No changes`. Training layer created and
destroyed twice, `verify` green each time, persistent layer intact at 25
throughout. 33 tests passing. Standing cost ~$1/month, all of it the KMS CMK.

Honest caveat: the persistent layer did **not** apply cleanly first time. It
took two applies because of the tag defect above. The exit criteria are met;
the path to them was not straight.

---

## Phase 1 — Data lake & ingestion

**Built**

- The four-layer lake with immutability **enforced** rather than asserted.
- `raw/` loaded as CSV only; a separate `benchmark/` corpus in three formats.
- Four hand-written Athena tables — no crawlers, those are Phase 2.
- `scripts/benchmark_formats.py`: one logical query across four encodings,
  recording bytes scanned.
- `scripts/partition_experiments.py`: 500k orders arranged four ways to measure
  over-partitioning and the small-file problem.

### The layer contract

Who may write what, in which format, and whether it can be changed afterwards.
This is the part that has to be decided before any data lands, because
retrofitting immutability onto a layer that has already been rewritten is not
possible.

| Layer | Contents | Written by | Mutable? | Enforced how |
| --- | --- | --- | --- | --- |
| `raw/` | CSV, exactly as the source system emitted it | the **ingestion identity** (`dev-user` via `load_raw.sh`) | **No — append-only** | IAM: the Glue role has `GetObject` only, no delete verb. Bucket policy: `DenyRawObjectDeletion` on `raw/*` for every principal except break-glass |
| `processed/` | Parquet, cleaned and typed | the **Glue role** | Yes | derived data; a re-run must be able to replace what it produced |
| `curated/` | Parquet, partitioned, business-ready | the **Glue role** | Yes | same |
| `quarantine/` | Parquet plus a reason column | the **Glue role** | Yes | same |
| `benchmark/` | the same rows in CSV, JSON and Parquet | ingestion identity | Yes | **not a production layer** — a measurement corpus for Phase 1 |
| `experiments/` | 500k orders in four partition layouts | ingestion identity | Yes | **not a production layer** — measurement only |

Three consequences worth stating plainly:

- **The Glue role cannot write `raw/` either**, not just delete it. That is
  stricter than "append-only" and it is correct for this architecture: raw is
  written by ingestion and read by ETL. The ETL role never has a reason to
  touch it.
- **`benchmark/` and `experiments/` are deliberately outside the medallion
  model.** Putting three encodings of the same rows into `raw/` would have made
  the layer meaningless and handed the Phase 2 crawler three tables where the
  business has one. They are covered by the same 7-day lifecycle expiry, so
  they cost nothing to leave behind.
- **Enforcement is two-layered on purpose.** The IAM split constrains the
  principal; the bucket policy constrains the resource. The second catches
  anything the first does not describe — a future role, a console session, an
  SDK call. Verified by IAM policy simulation against both policies together,
  not by reading the configuration: read raw `allowed`, delete raw
  `explicitDeny`, write curated `allowed`.

### Concepts — the six questions from the brief

**1. Why separate Raw, Processed and Curated?**

Because they have different *contracts*, not because three folders look tidy.
Raw is the evidence: it is what the source actually sent, so it must never be
rewritten — otherwise a bug found next month cannot be reproduced or replayed.
Processed and Curated are derived, which means they are disposable and
therefore rewritable; a re-run must be able to replace the partition it
produced. The layer boundary is a permissions boundary, and in this project it
is literally implemented as one.

**2. Why should Raw data normally remain unchanged?**

Reprocessing. Every downstream artefact can be rebuilt from raw, so raw is the
only thing whose loss is unrecoverable. The moment raw is mutable, "we can
always reprocess" stops being true and nobody notices until they need it.

It also matters that "unchanged" be a *control* rather than a convention. Until
Phase 1 this project asserted immutability in a comment while the Glue role
held `s3:DeleteObject` on `raw/*` — the claim was decorative. It is now two
enforced mechanisms (see the table above), and the difference is the difference
between a policy and a promise.

**3. Why is Parquet preferred for analytics?**

Measured, not assumed. Same rows, same query, four encodings:

| effect | comparison | result |
| --- | --- | ---: |
| columnar storage alone | flat Parquet vs CSV, both full scans | **1.33%** |
| partition pruning alone | partitioned vs flat Parquet, both filtered | **3.70%** |
| pruning on a row format | CSV filtered vs CSV full scan | **4.46%** |
| both together | partitioned Parquet vs CSV, both filtered | **2.25%** |

Two reasons. Columnar layout means a query touching 2 of 6 columns reads 2
columns of bytes; CSV and JSON must read every byte of every row to find the
fields they want. And Parquet is typed and compressed, so it is smaller before
any query runs — JSON cost **241%** of CSV on disk for identical data.

The third row is the one usually left out of this answer: **CSV prunes too.**
Pruning is a property of the partition layout, not the file format. Quoting
only the 2.25% credits Parquet with a saving the directory structure produced.

The most instructive result was counterintuitive. On the *unpartitioned*
Parquet table, adding a `WHERE year/month/day` filter made Athena scan **2×
more** — 15,906 bytes against 7,778. In that table those are ordinary columns,
so the filtered query reads five columns where the unfiltered one reads two. A
columnar engine reads only the columns a query names, so **a predicate on a
column you were not otherwise reading costs bytes**. Against the partitioned
table the same predicate is free, because the values live in the S3 prefix
rather than in the file.

**4. When should partitioning be used?**

When the partition column is one you filter by, and when the resulting
partitions are large enough to be worth having. Both halves matter.

The first half is what makes a filter free rather than a read (see above). The
second half is what the over-partitioning experiment measured.

**5. Why is over-partitioning a problem?**

500,000 orders across 30 days, partitioned daily (30 partitions) and hourly
(720). Identical rows, identical schema, identical queries:

| | daily (30) | hourly (720) | penalty |
| --- | ---: | ---: | --- |
| storage | 9.06 MB | 13.44 MB | **+48%** |
| average file size | 294.9 KB | 18.2 KB | 16× smaller |
| `MSCK REPAIR` | 4.53 s | 18.97 s | **4.2× slower** |
| planning, one-day query | 122 ms | 359 ms | 2.9× slower |
| planning, full scan | 275 ms | 922 ms | **3.4× slower** |
| bytes scanned, one-day query | 16,051 | 21,902 | **+36%** |

Every metric got worse. There is no trade-off here to balance — finer
partitioning was a straight loss on this data.

The last row is the one that breaks the intuition. Hourly partitioning is
*finer*, so a single-day query should prune *better*. It scanned 36% **more**,
because that day is now 24 files instead of one, each carrying its own Parquet
footer, schema and dictionary pages. The metadata overhead exceeded the pruning
benefit.

Three distinct costs, worth separating when explaining this:

- **Storage.** Per-file overhead is amortised over fewer rows, so the same data
  occupies 48% more space.
- **Catalog.** 720 partitions is 720 pieces of metadata to register and to list.
  Registration went from 4.5 s to 19 s, and that cost recurs on every crawl.
- **Planning.** The query planner enumerates partitions before reading anything,
  which is why full-scan planning tripled.

The rule: partition so that each partition holds a *useful* amount of data —
conventionally at least ~128 MB — and no finer than the granularity you
actually filter at. Daily is right for a dataset with this volume; hourly would
be right at roughly 24× the volume.

**6. What is the small-file problem?**

Many small objects cost more than few large ones, for the same bytes, because
per-file overhead is paid per file: a Parquet footer and schema in storage, and
a separate request and open in query.

One day of orders (20,840 rows) written as 1 file and as 200:

| | 1 file | 200 files | verdict |
| --- | ---: | ---: | --- |
| storage | 0.375 MB | 1.353 MB | **3.6× — conclusive** |
| bytes scanned | 16,051 | 65,845 | **4.1× — conclusive** |
| total ms (median of 3) | 559 | 701 | **inconclusive** |
| each run (ms) | 1138, 530, 559 | 701, 883, 668 | ranges overlap |

**The storage and scan penalties are real and reproducible. The runtime
difference is not a result.** The single-file case ranged 530–1138 ms and the
200-file case 668–883 ms; the ranges overlap entirely, so the 142 ms median gap
sits inside the noise. Athena carries roughly a second of fixed overhead per
query, and 1.35 MB is not enough work for per-file cost to dominate it.

Recording that as a win would have been dishonest. Small-file pain becomes a
runtime problem at thousands of files across gigabytes — which is more data
than this project should be paying to store. The mechanism is demonstrated by
the storage and bytes-scanned columns; the runtime claim is left unproven
because the measurement did not support it.

**Broke**

The measurement harness produced a complete, plausible-looking report in which
**every number was wrong**.

`AGGREGATE` was written as `SELECT status, count(*), sum(quantity) FROM {t}`
with no `GROUP BY`, and `ONE_DAY` appended a `WHERE` clause *after* it — which
would have been invalid even with the `GROUP BY` present, since `WHERE`
precedes `GROUP BY`. Every experiment query failed.

**Fixed**

The failure was invisible because of how it presented. A failed Athena query
still returns a `TotalExecutionTimeInMillis` and a `DataScannedInBytes` of
zero. The report rendered `bytes scanned: 0` alongside timings of 381 ms and
656 ms — which reads exactly like a very fast query, not like a broken one.

Three changes, in order of importance:

1. **`repeat()` now nulls every metric when all runs fail**, instead of passing
   the failure's timing through as though it were a measurement.
2. **The report gained a "Queries that did not succeed" section**, stating that
   every number above it is suspect. A harness that can fail silently will.
3. The SQL was corrected, and the composition-by-string-concatenation that
   caused it replaced with two explicit statements.

The lesson generalises past this script: **an error that looks like a plausible
result is more dangerous than a crash.** A crash gets investigated. A zero gets
believed — and this one would have been committed as evidence.

Diagnosis was straightforward once suspected: run one query by hand and read
the state. `count(*)` returning `bytes=0` turned out to be a genuine Parquet
optimisation (row counts live in the file footer, so Athena answers without
reading data), which briefly made the zeroes look legitimate — worth knowing,
and worth not being fooled by twice.

---

## Phase 2 — Catalog & discovery

**Built**

- One Glue crawler, four S3 targets, narrow include paths.
- `scripts/publish_catalog.py` — republishes crawler discovery under the
  brief's required names, then audits the catalog strictly.
- `scripts/run_crawler.sh` — starts the crawler and waits, because crawls are
  asynchronous and reading the catalog too early looks exactly like "the
  crawler did nothing".
- `src/generate/break_schema.py` — the drift fixture, outside `raw/`.
- `sql/athena/05_raw_tables.sql` — the same three tables written by hand, as
  the expectation to compare against and as a fallback.

**Result:** `training_db` holding exactly `customers_raw`, `products_raw`,
`orders_raw` — 1,000 / 200 / 13,761 rows, 42 partitions, and a three-way join
returning revenue by country.

### The naming problem, and why the crawler could not solve it

The brief requires `customers_raw`. A crawler cannot produce that name.
Verified against the Developer Guide rather than assumed:

> "The name of the table is based on the Amazon S3 prefix or folder name."
> "If duplicate table names are encountered, the crawler adds a hash string
> suffix to the name."

Naming is controlled by the folder and an optional *prefix* — no suffix, no
custom naming, and no documented behaviour where a crawler adopts an existing
table because its location matches. An earlier design assumed exactly that, and
would have produced stale hand-written tables sitting beside crawler-created
ones with the required names present and therefore looking correct.

Renaming the S3 prefixes was the other option, and the Phase 1 immutability
control forbids it: a rename is a copy plus a delete. So the crawler discovers,
and `publish_catalog.py` republishes under the contract names — which is the
guide's own suggested workaround.

**The transferable lesson: your S3 layout determines your table names, so
prefixes must be chosen with the catalog in mind before any data lands.** That
decision is one an immutable raw layer stops you revisiting.

### Five failures, and what each one taught

Phase 2 was planned around one deliberate failure. It produced five real ones,
and four of them reported success.

**1. The crawler could not read the customers header.**

`customers_raw` came back as `col0..col4` with 1001 rows for a 1000-row file —
the header counted as data. `products_raw` and `orders_raw` were correct from
the same crawl.

Glue decides a file has a header by comparing the first row's *types* against
the rows below it:

| | signal | outcome |
| --- | --- | --- |
| products | `price` is double in data, text in header | detected |
| orders | `quantity` is bigint in data, text in header | detected |
| customers | every column is a string in **both** | ambiguous |

With no signal it declines to guess. The data was never malformed; **inference
had nothing to work from**. All-string tables are exactly what a CSV export of
anything looks like, so this blind spot is common rather than exotic.

**2. A custom classifier did not fix it, and corrupted a different table.**

A CSV classifier with `ContainsHeader = PRESENT` and an explicit five-name
header was created and attached. Timestamps confirm the crawl ran after it.

`customers` still came back `col0..col4`. Meanwhile the classifier **was**
applied to `fixtures/products_drift` — a four-column file — whose columns
became `customer_id, customer_name, email, country`: the customers header,
truncated. Real column names destroyed.

**Custom classifiers attach to the crawler, not to an S3 target.** There is no
way to scope one to a single include path, so a classifier written for one
dataset is evaluated against every dataset that crawler touches. On a shared
crawler they are dangerous.

The classifier was removed and `customers_raw`'s schema is now **declared** in
`publish_catalog.py`, which affects exactly one table and cannot reach another.
That is the honest answer to "crawlers or hand-written schemas": the crawler
was right about two tables and wrong about the third, and **nothing in its
output said which**.

**3. `CombineCompatibleSchemas` silently suppressed the exercise.**

The drift fixture has the same four columns as `raw/products`, so the crawler
judged them compatible and merged them. No drift table, `TablesCreated: None`,
status `SUCCEEDED`.

The option had been set defensively while reasoning about partition schema
mismatch — which is a *different* setting (`CrawlerOutput.Partitions`). Two
options that sound related and are not.

**4. An IAM denial the crawler reported as success.**

The real reason no drift table appeared, found in the log rather than the
status:

```
Service Principal: glue.amazonaws.com is not authorized to perform:
s3:GetObject on .../fixtures/products_drift/products_20260901.csv
because no identity-based policy allows the s3:GetObject action
```

The Phase 1 prefix-split policy named raw, processed, curated, quarantine,
athena-results, temp and scripts. `fixtures/` was created afterwards, so nothing
granted access — **least privilege working exactly as designed, on a prefix
that turned out to be needed**. Fixed with one read-only statement.

I had blamed `CombineCompatibleSchemas` and changed configuration for it before
reading the log. The 403s were in every crawl log from the first run. Reasoning
from a plausible mechanism instead of reading the evidence cost two cycles.

**5. Terraform reported success while changing nothing.**

Deleting the classifier resource left the crawler still referencing it:

```
ERROR : Classifier de-training-customers-csv not found.   FAILED
```

Removing the `classifiers` argument sends nothing, and `UpdateCrawler` treats
absent as "leave alone". Setting `classifiers = []` did not work either — the
Glue API ignores an empty list. Terraform reported
`Apply complete! Resources: 0 added, 1 changed, 0 destroyed` and AWS changed
nothing, twice.

The tell was that `plan` showed the same diff again immediately afterwards. **A
resource that keeps re-planning an identical change after a successful apply is
one the provider cannot actually update.** Resolved by replacing the crawler,
which is free.

### The drift exercise

Once access worked, the crawler did something more interesting than retyping a
column. The two fixture files have incompatible schemas, so it created **one
table per file**:

| table | source | inferred |
| --- | --- | --- |
| `products_raw` | `raw/products/` | `price:double` |
| `products_csv` | the clean copy | `price:double` |
| `products_20260901_csv` | the file with `UNKNOWN` | **`col0..col3`, all string** |

The drifted file did not just lose `price`'s type — it lost **every column
name**, for the same reason customers did: with `UNKNOWN` present, every column
is a string and header detection has no signal. The same root cause twice.

Downstream, measured rather than asserted:

| query | result |
| --- | --- |
| `sum(price)` on `products_raw` | 59,038.96 |
| `sum(price)` on the drifted table | `COLUMN_NOT_FOUND: 'price'` |
| revenue join via `products_raw` | 12,124,430.60 |
| revenue join via the drifted table | `COLUMN_NOT_FOUND: 'p.product_id'` |

**Schema drift caused table proliferation.** One prefix became three tables
with names derived from whichever filenames happened to arrive. That is why
cleanup matches on *location* rather than on names — a property we control
rather than one the crawler invents.

**Restore:** fixture cleared from S3, all three fixture-derived tables removed
from the catalog, audit back to exactly three tables. `raw/` untouched
throughout — 44 objects before and after, and the clean `products.csv` still
200 rows. The exercise never required elevated permissions, because the fixture
never lived in the protected layer.

### The ten things Phase 2 should let me explain

1. **What the Glue Data Catalog is** — a metastore, not a store. Table
   definitions pointing at S3: schema, location, SerDe, partitions. Athena,
   Glue ETL and Redshift Spectrum all read the same definitions, so a schema
   change made once is seen by all of them.
2. **Why crawlers** — they discover schema and partitions from data that
   already exists, and keep partitions current as new ones arrive. Registering
   42 partitions by hand would be tedious and would drift.
3. **Crawlers vs hand-written schemas** — not either/or. The crawler is
   authoritative about what the files contain; a hand-written definition is
   authoritative about what the contract says they *should* contain. Where they
   disagree, something upstream changed. Here the crawler was right twice and
   wrong once, with nothing to indicate which.
4. **How inference works** — classifiers in order, first match wins; the CSV
   classifier detects headers by comparing first-row types to the rows below,
   and infers the narrowest type fitting every sampled value.
5. **Why drift is dangerous** — one bad token widened a column and then removed
   every column name. Nothing failed at write time; the catalog updated
   successfully and the queries broke afterwards.
6. **Catalog and Athena** — Athena has no catalog of its own; it reads Glue.
   `MSCK REPAIR` and crawlers both write partitions Athena then prunes on.
7. **S3 prefixes to tables** — the folder name becomes the table name, sibling
   folders under one include path become partitions, and incompatible schemas
   in one prefix become *separate tables*.
8. **Unexpected datatypes** — `price` went `double -> string -> gone`, and
   `sum(price * quantity)` failed with `COLUMN_NOT_FOUND` rather than a wrong
   number. A loud failure, but only after the data was already in the catalog.
9. **How I detected and recovered** — the audit in `publish_catalog.py` fails
   on any unexpected table, which is what caught the proliferation; recovery
   matched on location, not on invented names. Four of five failures reported
   success, so detection came from checking *what the catalog contained*, never
   from a status code.
10. **What I would do differently in production** — name S3 prefixes for the
    tables you want before data lands; `UpdateBehavior = LOG` so a bad delivery
    cannot silently repoint a column while a pipeline runs; one crawler per
    dataset so a classifier cannot reach a neighbour; and a schema contract
    checked in CI, because the crawler will not tell you which of its answers
    to distrust.

**Broke / Fixed**

Documented above and in `docs/troubleshooting.md`. The single most useful thing
Phase 2 taught is not about Glue: **an operation that reports success while
doing nothing is more expensive than one that fails.** Four did here. The
failing crawl in item 5 was diagnosed in under a minute because it said what
was wrong.

---

## Phase 3 — Transformation (Glue PySpark ETL)

**Built**

- `src/retail_pipeline/transforms.py` — the seven-stage pipeline the brief
  names, carrying its eleven transformations. Pure PySpark: nothing in it
  imports `awsglue`, which is what makes every rule testable locally.
- `scripts/glue_jobs/curated_sales.py` — the Glue entry point. Sequencing
  only; no transformation logic, because anything in this file cannot be unit
  tested.
- `tests/test_transforms.py` — 11 tests against real Spark on small fixed
  DataFrames.
- `infrastructure/training/glue_job.tf` — one Glue job, two S3 objects, one
  `archive_file`. No trigger, no workflow; orchestration is Phase 8.
- `src/generate/orphan_delivery.py` — the failure fixture, built but **not yet
  delivered**.
- An `etl` collector in `capture_evidence.py`, added the day there was a run
  to record.

**Result:** `curated/sales/` — 10,000 rows over 30 Hive-partitioned Parquet
files, `order_total` exact to the cent on every row, and revenue of
**$8,850,092.78** confirmed by recomputing it from the raw CSVs independently
of the job. Reconciliation balanced: `13,803 = 3,802 duplicates + 10,000 valid
+ 1 rejected`.

**Concepts**

- **Referential integrity is a decision, not a join type.** An order naming a
  product that does not exist has no price. An inner join drops it silently and
  revenue comes out low with nothing saying so; a left join keeps it with a
  null price, which is worse — a manufactured record that reads as a completed
  sale worth nothing. So orphans are rejected *before* any join, with a reason
  attached, and counted. The inner joins in `enrich()` are then safe by
  construction rather than by hope.
- **"Remove duplicates" is really "decide which row is true."**
  `dropDuplicates()` answers that with whichever row Spark read first, which is
  non-deterministic across runs and partition counts — the same input can
  produce different revenue twice. A `row_number()` window with an explicit
  ordering makes the rule stateable and reviewable. Any rule would do; having
  one is the point.
- **Cleaning must never drop a row.** `clean()` normalises and leaves failures
  as NULL; only `validate()` decides a row is unacceptable. Keeping "this value
  was unparseable" separate from "this row is unusable" is how rows stop
  disappearing without anyone deciding they should.
- **Money is not a float.** `order_total` is `decimal(12,2)`. A revenue figure
  that disagrees with itself by a fraction of a cent between runs is
  unexplainable later.
- **A reconciliation that only prints on success is not a check.** The identity
  is enforced and the job fails on imbalance. That guard caught the one real
  logic error in this phase — see below — and its failure was more valuable
  than a pass would have been.

**Broke — the deliberate defect**

A delivery of 100 orders arrived referencing three products the product feed
has never sent: `P900101`, `P900102`, `P900103`. Sixty rows join; forty do not.
Nothing about the file is malformed — every field parses, every type is right,
the CSV is valid. The rows are simply unjoinable, which is what makes it the
right failure to rehearse: it is ordinary, and it breaks quietly.

Delivered as an append to `raw/` (partition `year=2026/month=09/day=02`) and
kept there permanently. Raw holds what the source actually sent; diverting a
bad delivery to a fixture prefix would be pretending it did not happen.

**The failure mode, measured rather than asserted.** Before running the real
ETL, both naive implementations were quantified against the new partition:

| implementation | rows kept | revenue | what is wrong |
| --- | ---: | ---: | --- |
| inner join | 60 | $61,002.61 | 40 rows vanish; no error, no record |
| left join | 100 | $61,002.61 | 40 rows carry NULL price |
| **this pipeline** | **60 curated + 40 quarantined** | **$61,002.61** | nothing lost, everything named |

The revenue figure is *identical* in all three. That is the whole lesson: the
number a naive pipeline reports is not wrong here — the accounting behind it
is. An inner join understates row count silently, and a left join manufactures
forty completed sales worth nothing. Only the third can answer "where did the
missing forty go", and it answers with a queryable dataset rather than a
number.

**Result.** The job SUCCEEDED with the defect present, and every figure matched
the prediction recorded before the run:

    source 13,904 = duplicates 3,803 + valid 10,060 + rejected 41
    rejected: 40 orphan_product_id + 1 malformed_order_date
    curated 10,060 rows, revenue $8,911,095.39
    no orphan product_id in curated; zero nulls in price or order_total

Revenue moved $8,850,092.78 -> $8,911,095.39, exactly the $61,002.61 the 60
joinable rows are worth, confirmed by recomputing from the raw CSVs.

No fix was required, which is the point: the orphan decision was made in the
design rather than discovered in production. Had `enrich()` relied on its join
to enforce referential integrity, this delivery would have been the incident
that taught it.

**Broke — unplanned**

Four unplanned failures also happened, and they are the more useful material
because three share a shape.

1. **Packaging — caught locally, before spending anything.** `archive_file`
   zips the *contents* of `source_dir`, so pointing it at `src/retail_pipeline`
   produced an archive whose root was `transforms.py` with no enclosing
   package. Glue puts the zip on `sys.path`, so `from retail_pipeline import
   transforms` would have failed at startup, after the run was billed. Found by
   simulating Glue's import rather than reasoning about it.
2. **Data Catalog access — one flag, 46 seconds of billing.** I had written in
   a docstring that Glue 5.0 wires the Data Catalog in as the Hive metastore.
   It does not. Without `--enable-glue-datacatalog`, `spark.sql()` resolves
   against Spark's own in-memory catalog and every read fails with
   `TABLE_OR_VIEW_NOT_FOUND`. The docstring asserted the opposite of the truth,
   which is worse than saying nothing.
3. **IAM on a key that is not a prefix.** Writing to `s3://…/curated/sales`
   makes EMRFS materialise the parent directory as a zero-byte marker keyed
   `curated_$folder$` — underscore, not slash — at the bucket root, which does
   not match a `curated/*` grant. Same lesson as the Phase 2 `fixtures/`
   denial: least privilege working correctly on a key pattern nobody
   anticipated. Fixed by naming the five markers explicitly rather than
   widening to `bucket/*`, which would have handed the ETL role write access to
   `raw/` and undone the Phase 1 prefix split.
4. **The reconciliation contract itself.** `source == valid + rejected` cannot
   hold when deduplication removes rows between the two. See D22: duplicates
   became their own term rather than being absorbed into the source count,
   because "3,802 of 13,803 rows were duplicates" is a fact about the upstream
   feed worth surfacing every run.

**Fixed**

The diagnosis worth keeping is for the 42-row discrepancy behind failure 4.
Spark reported 13,803 source rows; every independent count said 13,761. The
surplus was exactly the number of files.

The decisive step was running the same question through a *different engine*:
Athena reads the identical Glue Catalog table with the identical SerDe, and
returned 13,761 with zero header rows. Two engines disagreeing on one table
definition locates the fault in the reader — not in the data, not in the
catalog, and not in the ETL. Spark does not honour `skip.header.line.count`;
Athena does. Recorded in `docs/troubleshooting.md` and deliberately **not**
fixed: the header rows are already collapsed by dedup and rejected with a
reason, and forcing the property is a Phase 5 data-quality concern.

That the outcome was harmless was luck, though. All 42 headers happen to share
`order_id = 'order_id'`, so dedup collapsed them to one. A header whose values
happened to parse would have produced a plausible bad row instead of an
obviously rejected one.

**The pattern across all four:** three of them — packaging, catalog access,
reconciliation wiring — were in `scripts/glue_jobs/`, the thin layer between
tested code and AWS. Not one was in the 300 lines of transformation logic the
tests cover. The end-to-end test even passed `deduped.count()` as the source
count, asserting the identity the module *could* satisfy rather than the one
the job actually used. Tests protect the code they run; the wiring they cannot
reach is where the failures live, and it earns review attention out of all
proportion to its size.

**Cost:** 1,238 DPU-seconds across five runs (~$0.15), plus ~$0.14 of crawler.
Three of the five runs were failures — and at roughly two cents each, that is
an entirely reasonable way to learn what the documentation did not say.

---

## Phase 4 — Production hardening & incremental

**Built**

Job bookmarks and incremental processing, on the Phase 3 pipeline unchanged.
Six files, and only one of them contains transformation code - which was the
point. `src/retail_pipeline/transforms.py` has **eight added lines, all inside
a docstring**; no function signature or expression differs from Phase 3.

- `src/retail_pipeline/config.py` - reads `config/pipeline.json` at runtime
- `scripts/glue_jobs/curated_sales.py` - bookmarked read, contract, zero-run guard
- `infrastructure/training/glue_job.tf` - bookmarks on, contract deployed, logging wired
- `config/dev.json` - the bookmark setting, and why

**Concepts**

*Bookmarks are a property of the reader, not of the job.* Setting
`--job-bookmark-enable` does nothing on its own. State is tracked per
`transformation_ctx` by Glue's own readers; a plain `spark.sql()` read ignores
it entirely. Enabling the flag while reading through Spark SQL would have
produced a job that reprocessed everything on every run and still reported
SUCCEEDED - a silent no-op, which is the worst shape a failure can take. Caught
before spending anything, by reading how the mechanism works rather than
assuming the flag was self-contained.

That forced exactly one DynamicFrame into the codebase, at the read of
`orders`, converted with `.toDF()` on the following line. It lives in the entry
script - the thin layer that already cannot be unit-tested - so `transforms.py`
stays pure PySpark and all sixteen transform tests remain valid. A DynamicFrame
as an integration-boundary adapter is a different thing from a DynamicFrame as
a data model, and only the second one was ever worth refusing.

*Bookmark the fact table, never the dimensions.* A bookmark on `customers` or
`products` would mean the second run reads zero dimension rows, every order
fails its referential check, and the job quarantines the entire tranche as
orphans - while reporting a balanced reconciliation and SUCCEEDED. The
reconciliation identity cannot catch this, because nothing is lost: the rows
are all accounted for, just in the wrong bucket. Both dimensions stay on
`spark.sql()` and are re-read in full every run.

*Configuration that nothing reads is not configuration.* `config/pipeline.json`
existed from Phase 0 and was never opened. The same values lived in four other
places: an f-string in `extract()`, two default arguments, and four Terraform
job arguments. It is now deployed as its own S3 object and read at runtime, so
the curated prefix or partitioning can be corrected and the job re-run without
rebuilding the code package.

Where the contract and the code could disagree, the job now refuses rather than
guesses. `write_output` writes snappy; if the contract ever asks for something
else the job raises instead of silently writing a format the contract does not
describe. That check replaced a `compression` parameter that would have been
plumbed through `transforms.py` to change nothing - the contract already said
`snappy`.

**The incremental exercise**

Three runs against the same job, delivering one 2,000-order tranche between the
first and the second:

| run | partitions available | rows read | outcome |
| --- | ---: | ---: | --- |
| 1 | 43 | **13,861** | 10,060 curated |
| 2 | 44 | **2,000** | only the new tranche |
| 3 | 44 | **0** | clean no-op, SUCCEEDED |

Run 2 reading 2,000 rather than 15,861 is the proof. Run 3 reading zero and
still succeeding is the other half of it: a pipeline that treats "nothing new
arrived" as an error would page someone every night for working correctly.

**Broke**

*A bookmarked read with nothing new returns no schema, not just no rows.*

Run 3 failed:

    source orders: 0
    AnalysisException: [UNRESOLVED_COLUMN.WITHOUT_SUGGESTION]
      A column or function parameter with name `order_id` cannot be resolved.
      transforms.py line 114, in deduplicate

The bookmark worked perfectly - `source orders: 0` is exactly right. When the
bookmark excludes every file there is nothing to infer a schema from, so
`create_dynamic_frame.from_catalog(...).toDF()` returns a frame with zero rows
**and zero columns**. `deduplicate()` then windows over `order_id`, which no
longer exists.

*The uncomfortable part is that a test covered this case and passed.* It built
an empty DataFrame **with the orders schema** - the case I imagined rather than
the one the runtime produces. It gave confidence worth less than no test at
all, because it made the gap look closed. The corrected test builds
`spark.createDataFrame([], StructType([]))` and asserts that `deduplicate()`
**raises**, deliberately: the transformations should not be taught to tolerate
a schemaless frame. The integration boundary is what must notice.

**Fixed**

Seven lines in the entry script, after the read:

    if not orders.columns:
        log.info("no new data since the last bookmark; nothing to process")
        log.info(f"reconciliation: {T.reconcile(0, 0, 0, 0, 0)}")
        job.commit()
        return

`job.commit()` still runs, so the bookmark advances. Verified by
`get-job-bookmark`: `RunId` points at the successful run and the state holds
`{"orders_source": ...}`, the `transformation_ctx` from the entry script.

*The failed run did not corrupt bookmark state.* It logged `source orders: 0`
before crashing, meaning it read against an already-correct bookmark committed
by Run 2, and never needed to commit its own. Bookmark state was retained
across the failure - worth knowing, because the alternative would have meant a
failed run silently re-processing a tranche on the next attempt.

**Fixed by accident: the Phase 3 header defect**

Phase 3 recorded that Spark ignores `skip.header.line.count` where Athena
honours it, putting 42 CSV headers into the pipeline, and deferred it to Phase
5. Run 1 read **13,861** rows where Phase 3 read 13,904, and rejected 40 rows
where Phase 3 rejected 41. The difference is exactly the 43 header rows and the
one that survived deduplication.

The Glue DynamicFrame reader honours the table property that `spark.sql()`
ignored. The defect was closed as a side effect of a change made for an
unrelated reason - which is worth recording precisely because it was luck. The
troubleshooting entry has been updated rather than deleted: the reasoning that
led to deferring it was sound on the evidence available then.

**Skipped, deliberately**

The brief lists Docker-based local Glue development and a 100k/5k/1M dataset.
Neither was done. Local testing already runs against real Spark in seconds with
no console involvement, which is what that topic is *for*; an 8 GB image would
have added a download, not a capability. A 2,000-row tranche demonstrates
incremental processing exactly as well as a million-row one, at a fraction of
the runtime. Retries stay at `max_retries = 0` - retrying a deterministic data
failure three times bills three times and teaches nothing.

**Cost:** 646 DPU-seconds across four runs (~$0.08), plus two crawls (~$0.14).

---

## Phase 5 — Data quality & quarantine

**Built**

Two Glue Data Quality rulesets in DQDL, one application-level validation rule,
and fourteen Athena statements that measure what each query costs to answer.

**Concepts**

*Routing and measurement are different jobs.* `validate()` decides, row by row,
what may enter curated and writes the rest to quarantine with a reason - it is a
control. DQDL answers "how healthy is this dataset" against a declared standard
- it is a measurement. Building the second did not make the first redundant, and
the clearest evidence is the duplicate case below.

*A quality rule should state the standard, not describe the data.*
`Uniqueness "order_id" = 1.0` was written knowing it would fail. Tuning it down
to 0.5 until it passed would have produced a green dashboard that reports
nothing on the day duplicates double.

**The four required detections**

The brief names NULL customer id, duplicate order id, negative quantity and
negative amount. Three are expressible on `orders_raw`; the fourth is not,
because this model has no source `amount` column - money is derived,
`order_total = quantity x price`. So it is measured on `products_raw` where the
number actually lives, and enforced at the same place in `validate()`:

    orders-quality     IsComplete "customer_id"        PASS
                       Uniqueness "order_id" = 1.0     FAIL   0.5258
                       ColumnValues "quantity" > 0     PASS    score 0.67
    products-quality   ColumnValues "price" > 0        PASS    score 1.00

**The failing rule is the finding.** `deduplicate()` already collapses duplicate
order_ids before validation, so they never reach curated and never appear as
rejections - correct behaviour, and completely silent. Only the uniqueness rule
turns that silence into a number. A pipeline can handle a problem perfectly and
still owe someone a measurement of it. That is the whole argument for keeping
DQDL alongside a pipeline that already quarantines.

The reported 0.5258 is Glue's own uniqueness ratio. The exact denominator it
uses is not documented in the result, so it is recorded as observed rather than
re-derived here; what it establishes is direction and magnitude - a large
minority of order_ids are not unique, which matches the 3,761 duplicates the ETL
collapses on a full run.

**Athena: the cost of asking the same question badly**

Every statement's bytes scanned were recorded. Three pairs ask an identical
question two ways:

| comparison | cheap | costly | ratio |
| --- | ---: | ---: | ---: |
| partition pruning | 0.001 MB | 0.100 MB | **87.3x** |
| columnar projection | 0.056 MB | 0.151 MB | **2.7x** |
| CTAS summary vs full scan | 0.001 MB | 0.068 MB | **52.4x** |

The partition-pruning pair is the one worth internalising. Both queries return
the same two numbers. The only difference is that one filters on the partition
columns and the other on `date_format(order_date, ...)`, a derived value the
engine cannot prune on. Same answer, 87 times the data. At this scale that is
fractions of a cent; at a terabyte it is the difference between a query you run
hourly and one you cannot afford to run at all.

**Broke**

*CTAS refused by our own governance control.* The first analytics run failed on
the CTAS statement:

    The Create Table As Select query failed because it was submitted with an
    'external_location' property to an Athena Workgroup that enforces a
    centralized output location for all queries.

`enforce_workgroup_configuration = true` was set in Phase 0 so that no query can
choose its own output path and escape the KMS-encrypted, cost-capped result
location. CTAS's `external_location` is exactly that escape. The two are
mutually exclusive and the control wins - the property was removed and the
summary table now lands in the enforced location. A governance setting that only
ever permits things is not a control; this is what it looks like when one binds.

**Finding: the Athena DDL leaves tables the Phase 2 audit does not expect**

`curated/` and `quarantine/` are not crawled - both folders are named `sales`
and would collide on one table name - so they are declared with Athena DDL
instead. That, plus the CTAS output, leaves `training_db` holding six tables
where `publish_catalog.py` expects exactly three, and its audit fails on
unexpected tables as well as missing ones.

This self-resolves at teardown: the database is Terraform-owned and destroyed
with the training layer, so the next session's crawl and publish see only the
three raw tables. It matters only if `publish` is re-run before teardown.
Recorded rather than fixed, because the fix would mean loosening a strict audit
that has already caught real problems.

**Deployment note, not architecture**

A DQ ruleset is bound to a catalog table and AWS validates the table exists when
the ruleset is created - but `orders_raw` and `products_raw` are produced by the
crawler and `publish_catalog.py`, which run *after* the training layer is
applied. `depends_on` cannot express a dependency on a resource Terraform does
not own, so the rulesets sit behind `data_quality_enabled`, applied in a second
pass after publish. The variable orders two applies; it adds no component and no
runtime behaviour.

**Cost:** 2 DQ evaluation runs, one no-op Glue run (121 DPU-seconds), one crawl,
and 28 Athena statements across two attempts - all but one of them under a
megabyte, so the 10 MB per-query minimum dominated the Athena bill entirely.

---

## Phase 6 — Governance (Lake Formation)

**Built**

The brief's three personas over the Lake Formation permission matrix (p.15),
verified positively and negatively through Athena, plus one LF-Tag to
demonstrate tag-based access control.

    DataEngineerRole      customers, products, orders   + quarantine via LF-Tag
    FinanceAnalystRole    sales, orders
    MarketingAnalystRole  customers: customer_id, country - NOT email

Final verification: **12/12 cases behaved as specified.**

**Concepts**

*Registration changes how data is read, not only who may read it.* Before it,
IAM alone answers "may this principal read this table" and every reader reaches
S3 directly. After it, Lake Formation vends credentials, and a principal with
flawless IAM policies is refused unless it also holds an LF grant. This is not a
subtlety - it broke both Athena and the ETL within minutes of registering, while
the IAM fallback was still fully enabled.

*IAM_ALLOWED_PRINCIPALS does not protect the vended path.* The assumption the
whole gate sequence was built on - that the fallback would carry the pipeline
until the persona work was done - was wrong, and Gate 2 disproved it. The
fallback governs authorization for principals reading S3 *directly*, which is
why the crawler and publish kept working. It does nothing for a service
requesting credentials *through* Lake Formation.

*A grant is permission to do something; an IAM action is permission to ask.*
The sharpest lesson of the phase. The Glue role held every Lake Formation grant
it needed - SELECT and DESCRIBE on the raw tables, DATA_LOCATION_ACCESS on the
bucket - and still could not read a row:

    LFCredential fetch failed with status code: 400
    simulate-principal-policy: lakeformation:GetDataAccess -> implicitDeny

Both halves are required and they live in different systems, configured by
different mechanisms. Athena never hit this because it calls GetDataAccess as
the *service*, so the caller's own policy is never consulted; only a principal
requesting credentials as itself needs the action. Phase 0 had put it on the
persona roles and nobody thought to put it on the Glue role, because before
registration nothing needed it.

*Column grants name what is permitted, not what is excluded.* The Marketing
grant lists `customer_id, country` rather than excluding `email`. A column added
to customers_raw tomorrow is inaccessible to that role by default, which is the
correct failure direction. An exclusion list would expose it silently.

**Broke — three access failures, each found only by failing**

    1. Athena     PERMISSION_DENIED ... AWSServiceRoleForLakeFormationDataAccess
                  is not authorized to perform: kms:Decrypt
    2. Glue ETL   LFCredential fetch failed with status code: 400   (no LF grant)
    3. Glue ETL   LFCredential fetch failed with status code: 400   (no IAM action)

Failures 2 and 3 produce the *identical* error message and required completely
different fixes. That is worth remembering: the message names the symptom, and
the only way to tell them apart was `simulate-principal-policy`.

The KMS one is the tidiest illustration of the vending model. The key policy
already allowed `athena.amazonaws.com`, and that bought nothing - the request
now arrives as the service-linked ROLE, a different principal entirely.

**Broke — a Terraform trap this project has hit before**

`lf_iam_allowed_principals = false` did nothing. The dynamic block emitted zero
blocks when disabled, the provider read that as "not managed" rather than "set
to empty", and the plan returned `No changes` while the fallback stayed live on
all eight resources.

This is the same trap Phase 2 hit with the crawler's classifiers, already
recorded in CLAUDE.md as *omission is not the same as empty*. Knowing about a
trap in the abstract did not prevent walking into it in a new shape. The fix is
to always describe the block and vary its content - Terraform can only remove a
value it is still describing. Empty `principal` fails provider validation, so
the working form keeps the principal and empties its permissions.

Removing the fallback also turned out to be two operations, not one. Clearing
the account defaults only affects tables created afterwards; the eight existing
resources kept their own IAM_ALLOWED_PRINCIPALS grant and had to be revoked
individually. The `default` database was left alone - it is not this project's.

**A test that reported a false alarm**

`SELECT *` as MarketingAnalystRole was asserted to be denied. It succeeds, and
that is correct: Lake Formation resolves the star against what the principal may
see and returns `['customer_id', 'country']`. The original assertion would have
reported a permission failure on a model that was working perfectly.

The test was changed, not the model - but "the query succeeded" is a weak thing
to assert, so it now checks the *shape* of the result and fails if any column
outside the grant appears. That is a stronger check than the one it replaced,
and it is the second time this phase that an assertion was measuring the wrong
thing.

**The drift, and what it was**

From the moment the Glue grants were created, `terraform plan` wanted to replace
them - eventually 10 to add and 9 to destroy, every table grant affected. The
grants were live and correct throughout; AWS simply records a `SELECT+DESCRIBE`
table grant as two entries, and the `ALL` from IAM_ALLOWED_PRINCIPALS on the
same resource folded into the provider's read.

Deliberately not chased. The hypothesis was that removing the fallback would
resolve it, and it was tested rather than assumed by capturing a plan either
side of step 11:

    before   Plan: 10 to add, 0 to change, 9 to destroy
    after    Plan:  1 to add, 0 to change, 0 to destroy

The one remaining is `table_with_columns`, which the provider cannot round-trip.
The grant exists and is correct; the plan is wrong about it. Documented and left.

**Cost:** eight Glue job runs across the phase (~1,400 DPU-seconds, ~$0.17),
three crawls, and roughly sixty small Athena queries across the control and
verification passes. Lake Formation itself is free.

**What survives teardown, and why it matters**

The data lake settings, the registered location and the service-linked role are
account-level and outlive the training layer. The removal of
IAM_ALLOWED_PRINCIPALS is therefore permanent: from now on every session's new
tables have no IAM fallback, and the Glue role's LF grants are load-bearing for
Phases 7-9. If one is ever missed, the crawler fails with a permissions error on
code that did not change - which is exactly how this phase started.

