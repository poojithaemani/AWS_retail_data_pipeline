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
