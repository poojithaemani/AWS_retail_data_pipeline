# Troubleshooting log

Real problems only. Every entry here is something that actually happened in
this project, with the diagnosis path that led to the fix — not a list of
things that could theoretically go wrong.

That constraint matters for the screening discussion: §7 of the brief asks
*"what happens when a component fails?"*, and an answer built from incidents
you actually debugged sounds different from one assembled out of documentation.

## How to add an entry

Use the template below. The **Diagnosis** section is the valuable part — the
fix is usually one line, but how you found it is the transferable skill.

```markdown
### <short title>

| | |
| --- | --- |
| Phase | NN |
| Component | Glue / Athena / Terraform / … |
| Deliberate? | yes (exercise) / no (real) |

**Symptom** — what was observed, verbatim: the error text, exit code, or wrong output.

**Diagnosis** — what was checked, in order, and what each step ruled in or out.

**Cause** — the actual mechanism, not the surface error.

**Fix** — what changed.

**Prevention** — the test, guard or convention added so it cannot recur silently.
```

---

## Phase 0 — foundation

### Persistent layer apply failed 22 resources in: comma in an IAM tag value

| | |
| --- | --- |
| Phase | 0 |
| Component | Terraform / IAM |
| Deliberate? | **no** — a real defect |

**Symptom**

`terraform apply` created 22 of 25 resources, then failed:

```
Error: creating IAM Role (de-training-glue-role): api error ValidationError:
1 validation error detected: Value at 'tags.2.member.value' failed to satisfy
constraint: Member must satisfy regular expression pattern:
[\p{L}\p{Z}\p{N}_.:/=+\-@]*
```

`aws_iam_role.glue` failed; its two attachments (`glue_service`, `glue_lake`)
were never attempted. The other 22 resources were created and recorded in state.

**Diagnosis**

1. The message names the constraint but not the offending value. Read the
   pattern first: it permits letters, whitespace, digits and `_ . : / = + - @`.
   **A comma is absent from that set.**
2. Extracted every `tags = { ... }` value across `infrastructure/**/*.tf` and
   tested each against the pattern. Exactly one failed:

   ```
   iam.tf  Purpose = 'Retail order ETL: crawl, clean, deduplicate, join, curate'
                     offending characters: [',']
   ```

3. Checked why it surfaced so late, and only here. The Redshift role carries a
   `Purpose` tag too — *"Load curated retail sales into the warehouse star
   schema"* — with no commas, so it applied cleanly. The S3 bucket and KMS key
   also carry tags: **S3 and KMS accept commas in tag values; IAM does not.**
   Terraform creates resources in dependency order, so the strictest validator
   in the graph was among the last things reached.

**Cause**

The `Purpose` tags were added when descriptive retail-domain labels were
introduced (`decisions.md` D14). Commas read naturally in prose, and nothing
locally rejected them — `terraform validate` checks syntax and types, not
service-side value constraints. The failure could only appear at apply.

**Fix**

Slashes instead of commas — `/` is in the permitted set:

```hcl
Purpose = "Retail order ETL: crawl / clean / deduplicate / join / curate"
```

**Prevention**

`tests/test_repo_hygiene.py::test_iam_tag_values_are_valid` parses every tag
block in the Terraform and asserts each value matches IAM's pattern.
Interpolations such as `${var.project}` are substituted with a safe token
before checking, since they resolve to names and ids.

Verified by reintroducing the comma: the test fails. Restored: it passes.

**What this cost, and what it did not**

The apply was partial, not corrupt. Terraform recorded exactly what succeeded,
so the recovery is a re-plan showing `3 to add, 0 to change, 0 to destroy` —
no manual cleanup, no orphaned resources, no drift. That is the argument for
`plan → apply` over console clicking, demonstrated rather than asserted.

The general lesson worth carrying: **`terraform validate` does not validate
against AWS.** It parses configuration. Service-side constraints — tag value
patterns, name length limits, character sets — are only enforced at apply, and
they differ per service.

Remaining candidates for this phase, to be recorded only if they occur:

- Cost allocation tag activation rejected because AWS has not yet observed the
  `Project` tag key on any resource — needs a second apply.
- Terraform state lock left behind by an interrupted run.

---

### AWS CLI rejects a log group name under Git Bash

| | |
| --- | --- |
| Phase | 0 |
| Component | AWS CLI / Git Bash |
| Deliberate? | no |

**Symptom**

```
aws logs describe-log-groups --log-group-name-prefix '/aws-glue/jobs'

InvalidParameterException: Value at 'logGroupNamePrefix' failed to satisfy
constraint: Member must satisfy regular expression pattern: [\.\-_/#A-Za-z0-9]+
```

The pattern in the error *does* permit `/`, which is the clue that the value
reaching AWS is not the value that was typed.

**Diagnosis**

Git Bash on Windows runs under MSYS, which rewrites arguments that look like
Unix absolute paths into Windows paths before the process sees them. The CLI
received something like `D:/Git/aws-glue/jobs`, whose colon and drive letter
fail the pattern. Quoting does not help - the rewrite happens after the shell
parses quotes.

**Fix**

```bash
MSYS_NO_PATHCONV=1 aws logs describe-log-groups --log-group-name-prefix '/aws-glue/jobs'
```

A leading double slash (`//aws-glue/jobs`) also works.

**Prevention**

Checked whether the teardown verifier had the same latent bug. It does not:
it filters client-side with JMESPath `contains(logGroupName, 'de-training')`
and never passes a `/`-prefixed argument. Recorded in `CLAUDE.md` because it
applies to any ad-hoc call involving log group names, IAM paths or SSM
parameter names.

---

### UNRESOLVED: draw.io cannot open the Star Schema tab

| | |
| --- | --- |
| Phase | 0 |
| Component | draw.io |
| Deliberate? | no |
| Status | **open** - worked around, not fixed |

**Symptom**

Opening the Star Schema tab of `architecture/retail-data-platform.drawio`
raises `d.setId is not a function`. The Architecture and Terraform Layers tabs
in the same file open normally.

**Diagnosis so far**

Attribute ordering in the file draw.io itself wrote identifies which tabs it
successfully decoded: draw.io rewrites `vertex="1" parent="1"` as
`parent="1" vertex="1"` when it re-serialises a page. Architecture and
Terraform Layers came back reordered; Star Schema was passed through verbatim,
so it was never decoded.

That evidence ruled out two plausible causes: `&quot;` and `&nbsp;` both appear
in tabs that decode fine.

A rebuild removed every remaining difference at once - the nested `<font>` tag,
all HTML entities, the `endArrow=none` style used only on that tab, and the
short generic cell ids (`st`, `ss`, `fact`, `keys`, `query`, `ec`, `ep`, `ed`)
in favour of namespaced ones. **The error persisted.**

**Why it is not fixed**

Reproducing it needs draw.io, which is a browser/Electron application. It
cannot be run from this environment, so every further attempt would be a guess
at a JavaScript error that cannot be observed. Two guesses were already spent.

**Workaround**

The tab was removed rather than committed broken - a diagram that errors when a
reviewer clicks it is worse than one that does not exist. The star schema now
lives as a Mermaid `erDiagram` in `architecture/architecture.md`, which GitHub
renders inline with no tooling and which carries the same content: the four
tables, the distribution and sort keys, and the query the model exists to
answer.

**How to resolve it properly, if it matters later**

Bisect with draw.io open: create a new tab with two rectangles and one edge,
confirm it opens, then add the remaining elements back in halves until it
breaks. That identifies the trigger in about four steps. It was not done here
because the Mermaid version is sufficient for the purpose, and Phase 1 is
worth more than a diagram tab.

---

## Phase 1 — data lake and ingestion

_No incidents._ The partitioning and format work was measured rather than
debugged; the findings are in `learnings.md`.

---

## Phase 3 — transformation (Glue PySpark ETL)

### Spark ignores `skip.header.line.count`, so 42 CSV headers entered the pipeline

| | |
| --- | --- |
| Phase | 03 |
| Component | Glue / Spark SQL / Glue Data Catalog |
| Deliberate? | no (real) |
| Status | **resolved in Phase 4**, incidentally — see Resolution |

**Symptom** — the job's reconciliation reported 13,803 source rows. Every
independent count of `orders_raw` says 13,761. The surplus was exactly 42 —
the number of partitions, and therefore the number of files.

**Diagnosis** — in order:

1. Counted the raw CSVs locally: 13,761 data rows across 42 files.
2. Queried the same table through Athena, which reads the same Glue Catalog
   table with the same SerDe:

   ```sql
   SELECT COUNT(*),
          SUM(CASE WHEN order_id = 'order_id' THEN 1 ELSE 0 END)
   FROM orders_raw
   -- 13761, 0
   ```

   Athena sees 13,761 rows and zero header rows. So the *table property* is
   present and correct — this is not a catalog defect.
3. Confirmed the property is actually set: `skip.header.line.count=1` on all
   three tables, applied by `publish_catalog.py`.
4. 13,803 − 13,761 = 42 = one extra row per file. That shape can only be the
   header of each file being read as data.

The decisive step was 2: running the *same query engine-independently*. Athena
and Spark read the identical table definition and disagreed, which located the
problem in the reader rather than in the data or the catalog.

**Cause** — Athena honours the `skip.header.line.count` table property. The
Spark reader behind `spark.sql()` on Glue does not apply it for these tables,
so each file's header line is returned as a data row.

**Consequence — none, and by luck rather than design.** All 42 header rows
share `order_id = 'order_id'`, so `deduplicate()` collapsed them to a single
row, which `validate()` then rejected as `malformed_order_date`. The
quarantine output contains exactly that row:

```
order_id  customer_id  product_id  quantity  order_date  status  rejection_reason
order_id  customer_id  product_id       NaN         NaT  STATUS  malformed_order_date
```

No header value reached `curated/`. But nothing in the design *intended* this:
the same defect on a file whose header happened to parse — a numeric-looking
column name, say — would have produced a plausible bad row instead of an
obviously rejected one.

**Fix** — none applied, deliberately. The rows are already detected, rejected
and quarantined with a reason, which is the behaviour the pipeline is supposed
to have for unusable input. Forcing Spark to honour the property is a
data-quality concern and belongs with the DQDL work in Phase 5, where "should
this row exist at all" is the actual subject. Fixing it here would mean
changing how the raw layer is read on the strength of an artifact that the
existing validation already handles correctly.

**Prevention** — the reconciliation is the guard. Any future divergence
between the source row count and the sum of its parts fails the job rather
than passing quietly, which is how this was noticed at all. Recorded here so
the 42-row discrepancy is not re-diagnosed from scratch next phase.

**Resolution (Phase 4) — closed by accident.** Enabling job bookmarks required
reading `orders` through `create_dynamic_frame.from_catalog` instead of
`spark.sql`, for reasons that had nothing to do with headers. That reader
honours `skip.header.line.count`. The first Phase 4 run read **13,861** rows
where Phase 3 read 13,904, and rejected **40** where Phase 3 rejected 41 - the
43 headers and the one survivor of deduplication, all gone.

Kept rather than deleted, for two reasons. The reasoning that deferred this to
Phase 5 was sound on the evidence available then, and the diagnosis - two
engines disagreeing over one table definition locates the fault in the reader -
is the transferable part. It is also a reminder that the fix was luck: nobody
chose the DynamicFrame reader for its header handling.

---

## Phase 6 — governance (Lake Formation)

### publish_catalog.py writes its audit into phase-02 whatever phase it runs in

| | |
| --- | --- |
| Phase | 06 |
| Component | scripts/publish_catalog.py / evidence |
| Deliberate? | no (real) |
| Status | worked around, not fixed |

**Symptom** — after a Phase 6 `publish`, `git status` showed
`docs/evidence/phase-02/catalog-audit.json` modified, flipping from `"ok": true`
to `"ok": false`. Committing it would have recorded Phase 2 as having failed its
own audit, months after it passed.

**Diagnosis** — the audit content was correct for *now*: `missing: []`, and
`unexpected: [curated_sales, quarantine_sales, sales_by_category_ctas]`, the
three tables the Phase 5 Athena DDL creates. The problem was the destination,
not the verdict. `scripts/publish_catalog.py:315` hardcodes:

    out = REPO_ROOT / "docs" / "evidence" / "phase-02"

**Cause** — the script was written in Phase 2, when it only ever ran in Phase 2.
It has been run in every phase since, and each run silently rewrites Phase 2's
evidence with the current catalog's verdict.

**Fix** — the Phase 2 file was restored with `git checkout`, and the Phase 6
audit written to `docs/evidence/phase-06/catalog-audit.json` with a note
explaining why it fails. The script itself was not changed: it should take the
phase as an argument the way `capture_evidence.py` does, but doing that at the
close of Phase 6 means touching a Phase 2 artifact to fix a reporting wart, and
the phase already carried more unplanned changes than intended.

**Prevention** — none automated yet. A hygiene test asserting that evidence
under `docs/evidence/phase-NN/` is only written by phase NN would catch it, and
is worth adding when the script is fixed properly. Until then, check
`git status` for modified evidence outside the current phase before committing.

---

## Phase 7 — analytical warehouse (Redshift)

### An S3 lifecycle rule deleted the raw layer

| | |
| --- | --- |
| Phase | 07 |
| Component | S3 / `infrastructure/persistent/lake.tf` |
| Deliberate? | no (real) |
| Status | **resolved** — data recovered, rule re-scoped, regression test added |

This is the project's answer to the brief's §7 question, *"what happens when a
component fails?"* — because here nothing failed. Every component did exactly
what it was configured to do.

**Symptom** — `raw/` held **2 objects**. It had held 46 in the Phase 4 and
Phase 5 evidence captures, unchanged through Phase 6. The two survivors were
the only objects uploaded inside the previous seven days.

**Diagnosis** — the shape of the loss located it before any log was read:

1. The survivors had nothing in common except their upload time. Not a prefix,
   not a partition, not a file type — only their age. Deletion by a caller
   would have followed some structure; deletion by *age* is a lifecycle rule.
2. `lake.tf` carried a single expiration rule with `filter {}` — an empty
   filter, which means every object in the bucket — and a seven-day expiry
   (`var.lake_expiration_days`, exported as `7` from `config/project.env`).
3. Forty-two order partitions plus the customer and product files had aged past
   seven days. S3 removed them.

**Cause** — the rule was correct and its scope was wrong. `filter {}` is not a
missing filter; it is a filter that matches everything, and S3 applied it as
written. The rule was added in Phase 0 carrying the comment *"everything here
is regenerable from `src/generate` with a fixed seed"*, which was true when it
was written and quietly stopped being true in Phase 3. From that point `raw/`
accumulated deliveries the generator does not produce — the orphan-product
partition, and the Phase 4 incremental tranche — and it was that append-only
history the bookmark and reconciliation exercises were built on.

**Why the bucket policy did not help, and could not.** The lake bucket carries a
`DenyRawObjectDeletion` statement refusing `s3:DeleteObject` and
`s3:DeleteObjectVersion` under `raw/`. It is scoped to *principals*. Lifecycle
expiry is performed by S3 itself against no principal at all, so the policy
never evaluates and has nothing to refuse. The raw layer was protected against
deletion by callers and completely exposed to deletion by configuration — and
the second is the one that happened.

That distinction is the transferable lesson: an identity-based control and a
service-performed action do not meet. Immutability that is enforced only in the
principal dimension is not immutability.

**Recovery — restored, not regenerated.** The local `data/raw/` tree was
untouched by the incident and was re-synced with `de.sh load`. This matters
more than it sounds: re-running the generator would have produced the seeded
30-day dataset (`seed: 20260819`, `end_date: 2026-08-31`) but not the two
later deliveries, so a regenerated lake would have been internally consistent
and silently missing the history the incremental exercise depends on. The
manifest at `data/raw/manifest.json` records a SHA-256 per generated file, so
the restored files could be compared rather than trusted.

**Validation** — `raw/` returned to exactly its pre-incident size:

    phase-04   46 objects   1,005,136 bytes    before
    phase-05   46 objects   1,005,136 bytes    before
    phase-06   46 objects   1,005,136 bytes    after recovery
    phase-07   46 objects   1,005,136 bytes    after recovery

Byte-identical, not merely the same count. The downstream reconciliation agreed
independently: the Phase 7 warehouse load reproduced 12,060 curated rows and
$10,705,326.72, matching Athena to the cent.

**Fix** — expiry now names each derived prefix explicitly. One rule per prefix,
because an S3 lifecycle rule takes a single prefix:

| Prefix | Expiry |
| --- | --- |
| `raw/` | **never** — absent from every expiration rule |
| `processed/` | `var.lake_expiration_days` (7) |
| `curated/` | `var.lake_expiration_days` (7) |
| `quarantine/` | `var.lake_expiration_days` (7) |
| `temp/` | `var.lake_expiration_days` (7) |
| `experiments/` | `var.lake_expiration_days` (7) |
| `athena-results/` | 1 day |
| incomplete multipart uploads | aborted bucket-wide after 1 day |

The derived rules also expire noncurrent versions after one day. The multipart
rule is the one remaining `filter {}`, and it is safe bucket-wide because it
removes only the fragments of uploads that never completed — it cannot touch a
whole object.

The verbosity is the point. Adding a prefix to that list is a deliberate act,
whereas `filter {}` silently covers anything anyone creates later.

**Prevention** — `tests/test_repo_hygiene.py::test_no_lifecycle_expiry_can_reach_the_raw_layer`
parses `lake.tf`, keeps only the rules that actually expire objects, and fails
if any of them omits a prefix or uses an empty filter. It also asserts that the
literal `"raw/"` never appears in a lifecycle rule. The
`abort_incomplete_multipart_upload` rule is exempt by name for the reason
above, being the one rule that expires nothing. The test reads the Terraform
source rather than any deployed state, so it guards the configuration at commit
time — before an apply can act on it.

---

## Local development

Issues in the repository tooling rather than in AWS. Two are already recorded
in `docs/decisions.md` because they changed how the project is built:

- **CRLF written into shell scripts** by Python `write_text()` on Windows,
  which would produce `\r: command not found` mid-teardown. Guarded by
  `tests/test_repo_hygiene.py::test_no_crlf_in_tracked_text_files`.
- **Double-encoded UTF-8** from `read_text()` defaulting to cp1252 on Windows,
  which mangled `README.md` irreversibly. Guarded by
  `tests/test_repo_hygiene.py::test_no_mojibake`.
