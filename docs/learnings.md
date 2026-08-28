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
