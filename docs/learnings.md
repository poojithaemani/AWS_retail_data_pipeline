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
