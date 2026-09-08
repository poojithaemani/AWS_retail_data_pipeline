# Design decisions

Section 7 of the brief requires being able to explain what the architecture
does, why each service is used, what alternatives exist, and what happens when
a component fails. This file is where the *why* lives — one entry per decision
that a reviewer could reasonably challenge.

Format: the decision, the alternatives actually considered, and the trade-off
accepted. A decision with no cost listed is a decision that has not been
thought about properly.

---

## D1 — Terraform, not CloudFormation or CDK

**Decision.** All infrastructure in Terraform.

**The brief** suggests `infrastructure/{cloudformation,cdk}/` (§8), but does not
mandate a tool.

**Alternatives.** CloudFormation is native and needs no state file, but its
delete behaviour on failed stacks is awkward and drift detection is weak. CDK
gives real language constructs, but adds a synth step and a bootstrap stack that
itself has to be managed.

**Why Terraform.** This project destroys and recreates its infrastructure
constantly, so the `plan` → `apply` → `destroy` loop is the primary interface,
not an occasional operation. Terraform's plan output is the clearest of the
three, and `destroy` is a first-class verb rather than a stack deletion.

**Cost accepted.** A state file to manage, which brought its own decision (D3).
Also a deviation from the brief's suggested tree, which must be explained rather
than glossed over.

---

## D2 — Split by lifecycle, not by service

**Decision.** Two Terraform layers: `persistent/` (KMS, IAM, lake bucket,
budgets) and `training/` (everything else). The training layer creates no
buckets, keys or roles — it reads them from the persistent layer's outputs.

**Alternative.** One state file with `prevent_destroy` on the resources that
must survive.

**Why.** A single state forces a bad choice: either `terraform destroy` takes
the KMS key that decrypts yesterday's data, or so much is protected that the
teardown stops being meaningful. Splitting by *lifecycle* makes destroying the
training layer a routine operation rather than a risky one — which is the whole
point, since it happens every session.

**Cost accepted.** Two `terraform apply` targets, and a `terraform_remote_state`
dependency between them.

---

## D3 — The state bucket is not Terraform-managed

**Decision.** `de.sh bootstrap` creates the state bucket with the AWS CLI.

**Alternative.** The usual pattern: apply with local state, then
`terraform init -migrate-state`.

**Why.** Terraform cannot cleanly own the bucket its own state lives in. The
migration dance works but leaves a local state file on every machine that ever
ran it, which is exactly the kind of untracked artefact this project is trying
to eliminate. One idempotent CLI call has no such failure mode.

**Cost accepted.** One resource that is not in any Terraform state, documented
in `backend.tf`.

---

## D4 — S3 native state locking, no DynamoDB table

**Decision.** `use_lockfile = true` on the S3 backend.

**Alternative.** The traditional DynamoDB lock table.

**Why.** One fewer resource, one fewer cost line, and — most relevant here — one
fewer thing that can survive a teardown unnoticed.

**Cost accepted.** Requires Terraform ≥ 1.10. Pinned in `versions.tf`.

---

## D5 — No VPC-attached Glue, therefore no NAT Gateway

**Decision.** Glue jobs run outside a VPC. `verify` treats the existence of a
NAT Gateway as a hard failure.

**Why.** Nothing in scope needs it. Glue reaches S3, the Data Catalog and
Redshift Serverless over AWS-managed networking. A NAT Gateway would add roughly
$33/month for zero capability — and it is the classic silent cost sink in
training accounts precisely because it is easy to create and easy to forget.

**When this would be wrong.** The moment a source lives in a private subnet — an
RDS instance, an on-prem system over Direct Connect, or a third-party API behind
a static egress IP. Then Glue needs a VPC connection, private subnets, and a NAT
Gateway or VPC endpoints. **VPC endpoints for S3 and the Glue API are the
cheaper answer** where the only private need is AWS services.

---

## D6 — us-east-2, not us-east-1

**Decision.** Single pinned region, `us-east-2`, set once in
`config/project.env`.

**Why.** It matches the machine's AWS CLI default region. A mismatch between
Terraform's region and the CLI's would mean every ad-hoc `aws` command silently
targets a different region from the infrastructure — a class of bug that costs
an hour every time it happens. Glue Data Quality and Lake Formation are both
fully available there.

**Cost accepted.** us-east-1 gets new AWS features first. Not a factor for
services this mature.

---

## D7 — The lake bucket lives in the persistent layer

**Decision.** S3 survives teardown; all compute, catalog, warehouse and
orchestration is destroyed. `de.sh nuke` wipes the bucket when a genuinely clean
slate is wanted.

**Alternative.** Destroy everything including S3, and regenerate the raw layer
each morning (~3 minutes for 1M orders).

**Why.** S3 is storage, not a running service: a few hundred MB costs about a
cent a month, and a 7-day lifecycle rule bounds it. More importantly, the
incremental-processing exercise needs multi-day raw history to be meaningful —
destroying it nightly would reduce "immutable raw retention" to a claim that can
only ever be demonstrated within a single session.

**Cost accepted.** ~$0.01/month, and one bucket that a strict reading of
"destroy everything daily" would not allow.

---

## D8 — Glue Data Quality (DQDL), not Deequ

**Decision.** Implement Phase 5 with Glue Data Quality; read the Deequ repo for
understanding.

**The brief** lists `aws-samples/amazon-deequ-glue` as Mandatory.

**Why.** Glue Data Quality *is* Deequ, productised — AWS built it on the Deequ
engine. DQDL expresses the same constraints as configuration rather than as a
Scala job with a JAR dependency, integrates with EventBridge for the
pass/fail branch in Phase 8, and destroys cleanly as a Terraform resource.

**Cost accepted.** A deviation from a Mandatory repository, justified in
`reference-repos.md`, to be revisited if the reasoning does not survive Phase 5.

---

## D9 — A purpose-built data generator

**Decision.** `src/generate/` rather than
`aws-samples/aws-glue-test-data-generator`.

**Why.** Because the raw layer is rebuilt after every teardown, byte-level
reproducibility is a hard requirement, not a nicety. Two baselines depend on it:
"the second run reprocessed 0 rows" (job bookmarks) and "exactly these rows were
quarantined" (data quality). Both are meaningless if the input drifts. The local
generator is seeded, emits a SHA-256 manifest, and is covered by tests asserting
two runs produce identical bytes.

It also emits the defect ground truth that Phase 5 asserts against — turning
"the pipeline caught some bad rows" into "the pipeline caught exactly the 231
rows that were broken."

**Cost accepted.** Code to maintain, and no exposure to the AWS generator.

---

## D10 — Two budgets with distinct jobs

**Decision.** A **daily** $5 budget and a **monthly** $50 budget, both always
created, both notifying one subscriber on actual spend only.

| | Job | Thresholds |
| --- | --- | --- |
| Daily | the immediate failed-teardown alarm | 100% of $5 |
| Monthly | the backup guardrail and total cap | 50 / 80 / 100% of $50 |

**Why not monthly alone.** Too slow for the failure that actually matters: a
session ending with the training layer still standing. At roughly $4 per active
day, a leak takes a fortnight to breach $50 — by which point the money is spent.
The daily budget catches it the next morning.

**Why not daily alone.** It only sees one day at a time, so a slow leak sitting
just under $5/day never trips it while still costing $150 a month. The monthly
budget is the backstop for exactly that shape of failure.

**Why no forecast alerts.** This workload is deliberately spiky. Forecasting
from a few active hours projects figures that mean nothing, and an alert people
learn to ignore is worse than no alert.

**Why no conditional creation.** Making the budgets depend on whether an email
was supplied would let the guardrail be skipped by omission — which is precisely
how guardrails get skipped. `de.sh bootstrap` therefore refuses to run until a
real address is set.

**Alternative rejected.** A CloudWatch alarm on `EstimatedCharges` would need an
SNS topic — another resource for teardown verification to reason about — and
depends on "Receive Billing Alerts" being enabled in account preferences, which
no API can set.

**Cost accepted, and the honest limitation.** At $5 against a ~$4 active day
there is little headroom, so an unusually heavy day may alert; a false positive
costs an email, a false negative costs a month of Redshift. More importantly,
**budgets watch spend, not existence**. Redshift Serverless scales to zero and
Glue costs nothing between runs, so an idle-but-undestroyed training layer can
sit under $5/day indefinitely without tripping either budget. `de.sh verify` is
what catches that. The two mechanisms are complements, not substitutes.

---

## D11 — Verification scoped to project-owned resources

**Decision.** `verify` filters by name prefix, with two deliberate exceptions
checked account-wide: Glue sessions and Redshift provisioned clusters.

**Why.** The account already holds an unrelated Redshift Serverless workgroup
(`nyc-taxi-mdm-wg`). Account-wide counts would flag it as a survivor on every
run, and a verifier that cries wolf is a verifier people stop reading. The two
exceptions are billable, short-lived, and should never be running regardless of
who created them.

**Cost accepted.** A resource created outside the naming convention could evade
the check. Mitigated by mandatory `Project` tags and the cross-region sweep.

---

## D12 — Hand-written Athena DDL before crawlers

**Decision.** Phase 1 defines external tables by hand; crawlers arrive in
Phase 2.

**Why.** Schema-on-read is abstract until you have written the schema yourself.
Doing it manually first also means that when the crawler infers something wrong
in Phase 2 — `price` retyped from `double` to `string` because of `UNKNOWN` —
the difference between what you declared and what it inferred is visible rather
than theoretical.

**Cost accepted.** Some DDL that a crawler would have generated.

---

## D13 — One env file, not two

**Decision.** `config/project.env` is the only env file, and it is safe to
commit. The single value that must stay out of version control - the budget
alert address - lives in `infrastructure/persistent/terraform.tfvars`.

**Alternative rejected.** A second gitignored `config/local.env` sourced by
`lib.sh` after `project.env`. That is the conventional shape, and it works, but
for exactly one variable it costs a second file, an example file, and a
conditional source block in `lib.sh`.

**Why tfvars instead.** `*.tfvars` is already in `.gitignore`, and Terraform
loads `terraform.tfvars` automatically. So the same separation - shared config
committed, personal config not - falls out with no new machinery at all.

**Alternative rejected.** Putting the email straight into the committed
`project.env`. Simplest of all, but the address then ships with the repository
and stays in its history, which matters for something meant to be shown to
people.

**Cost accepted.** The value is one directory away from the rest of the config,
so `de.sh bootstrap` checks for it explicitly and fails with a useful message
rather than letting Terraform prompt for a missing variable.

---

## D14 — Brief-exact resource names, retail identity in tags and docs

**Decision.** Two naming systems, applied deliberately:

| Where | Convention | Example |
| --- | --- | --- |
| Graded resource names | exactly as the brief specifies | `training_db`, `customers_raw`, `de-training-glue-role` |
| S3 buckets | brief's name plus the minimum uniqueness suffix | `de-training-749185461065` |
| Tags, descriptions, docs, labels | the business domain | `Project=retail-data-pipeline` |

**Why not rename everything to `retail-*`.** The brief names `training_db` and
the raw tables explicitly (p.6-7); those are graded, and renaming them is a
deviation that buys nothing.

**Why not leave `de-training` everywhere either.** A tag reading
`Project=de-training` says which exercise produced a resource, not what it is
for. In Cost Explorer, in an incident, or in a review, the useful answer is
"the retail data pipeline". Descriptions and tags are free-form, so they carry
the domain identity at no cost to compliance.

**Cost accepted.** Two conventions in one repository, which is why this entry
exists. `PROJECT` and `PROJECT_TAG` are separate variables in
`config/project.env` so the distinction is explicit rather than accidental.

---

## D15 — Evidence collectors added per phase, not up front

**Decision.** `capture_evidence.py` ships three collectors — context, lake,
athena — not ten.

**Why.** Ten were written before any phase had produced evidence, and eight of
them returned empty on every run. Each is roughly thirty lines of boto3
following the same shape: a paginated list call, a few fields kept, errors
caught and recorded rather than raised. They are now added in the phase that
first has something for them to collect.

The general point, which applies beyond this file: speculative tooling that has
never run against real output is not tested tooling. It looks like progress and
behaves like debt.

**Cost accepted.** Each new phase adds a collector before it can capture
evidence — a few minutes of work at the point where the requirement is finally
understood, rather than guessed at.

---

## D16 — One value, one home

**Decision.** Every configuration value is defined in exactly one place, and
the boundaries are explicit:

| Owner | Holds | Example |
| --- | --- | --- |
| `config/project.env` | identity and account-wide settings | region, resource prefix, domain tag, catalog name, budget limits |
| `config/pipeline.json` | the data contract | lake layers, dataset prefixes, primary keys, partitioning, catalog table names |
| `config/dev.json` | per-environment runtime knobs | Glue worker type, worker count, timeout, retries, bookmarks |
| Terraform | anything it creates | the Athena workgroup and its scan ceiling |
| `terraform.tfvars` | personal values | budget subscriber |
| `~/.aws/credentials` | credentials | access key, secret |

**The problem this fixes.** Region, project prefix, domain tag, Glue version
and the catalog name were each defined in three places at once: `project.env`,
`dev.json`, and as a Terraform variable `default`. Nothing failed, because all
three agreed — but they only agreed by luck. A Terraform `default` sitting
alongside a `TF_VAR_` export is the worst of the three: run through `de.sh` the
export wins, run bare the default wins, and the two disagree with nothing
raising an error.

**Consequence accepted.** Terraform no longer has defaults for anything
`project.env` owns, so `terraform apply` outside `de.sh` fails with a list of
missing variables. That is intended: it is the same class of mistake as running
a bare `terraform init` and losing the shared plugin cache, and failing loudly
beats running against the wrong region silently.

**Guarded by three tests** in `tests/test_repo_hygiene.py`:
`test_no_terraform_default_shadows_project_env` (nothing defined twice),
`test_every_required_variable_has_a_source` (nothing defined zero times), and
`test_config_json_does_not_restate_project_env` (identity stays out of the
JSON config).

---

## D17 — A `src/retail_pipeline/` package, not the brief's flat tree

**Status: agreed, not yet implemented.** Now costing something visible: three
modules reach their imports through `sys.path.insert`, and editors report every
one as unresolved because no static analyser follows a runtime path change.
`[tool.pyright] extraPaths` in `pyproject.toml` silences that, but it is a
workaround for a structural problem, not a fix. The repository currently has
`src/generate/` plus empty `src/{extract,transform,quality,common}/`. The
restructure happens in Phase 1, when there is code to move.

**Decision.** ETL code will live in one importable package:

```
src/retail_pipeline/
    __init__.py
    extract/  validate/  clean/  deduplicate/
    transform/  enrich/  write_output/
```

**The brief** (§8) shows these as top-level directories under `src/`:
`src/{extract,transform,quality,streaming}`.

**Why deviate.** Those directories are not a Python package — no `__init__.py`,
no common root — so `src/transform/` cannot import `src/common/` without a
`sys.path` hack. The generator gets away with it today only because its modules
are standalone scripts, and `tests/` already carries exactly that hack.

It also matters for Glue. A Glue job takes one script plus a zip via
`--extra-py-files`; a package gives one obvious thing to zip and one import
root that behaves the same locally, in the container, and on Glue.

The brief's stage names are preserved as subpackages, so the mapping to the
required `extract() validate() clean() deduplicate() transform() enrich()
write_output()` structure (§Day 4) stays one-to-one and visible.

**Cost accepted.** A visible deviation from the brief's suggested tree, which
is why it is written down here rather than done quietly.

---

## D18 — Approval gate on every AWS mutation

**Decision.** `bootstrap`, `up`, `down` and `nuke` all run
plan → print summary → confirm → apply. `--yes` skips the prompt and is never
the default.

**Why the saved plan file matters.** `tf_change` writes the plan to a file and
applies *that file*, rather than re-planning at apply time. What was reviewed
is exactly what runs — there is no window in which the world changes between
the plan you approved and the apply that follows it.

**Why `-detailed-exitcode`.** "Nothing to do" is distinguished from "changes
pending" by exit code (0 vs 2) rather than by scraping output text, so the
no-op case skips the prompt entirely instead of asking about an empty plan.

**Non-interactive behaviour.** With no TTY and no `--yes`, the plan is printed
and the command stops without applying. It does not assume consent, and it does
not silently succeed — the operator reviews the printed plan and re-runs.

**Cost accepted.** Two steps where there was one, and CI-style use requires an
explicit `--yes`. That is the intended friction: `-auto-approve` on a destroy is
precisely the shape of the accident this project is meant to make impossible.

---

## D19 — Least privilege for the Redshift role

**Decision.** The Redshift role gets one purpose-built policy: `s3:GetObject`
on `curated/*`, `ListBucket` conditioned on that prefix, `kms:Decrypt` and
`DescribeKey` on the lake key, and six read-only `glue:Get*` actions scoped to
this project's catalog, database and tables.

**What it replaced.**

| Removed | Why it was wrong |
| --- | --- |
| `AWSGlueConsoleFullAccess` | 49 actions across 13 services, including `glue:*` (so `DeleteDatabase`, `DeleteTable`) and `cloudformation:DeleteStack`. A console policy for a human, attached to a service principal whose entire job is `COPY`. It could have deleted the catalog it reads from. |
| the shared `lake_access` policy | `PutObject` and `DeleteObject` across the whole bucket, plus `kms:Encrypt`. Correct for the Glue ETL role, which writes processed and curated output; wrong for a warehouse that only reads one prefix. |

**Why not defer the role to Phase 7 instead.** D2 puts IAM in the persistent
layer so later phases only *grant*, never *create*, and Lake Formation in
Phase 6 needs stable role ARNs to grant against. Deferring would trade a
security problem for a structural one. Narrowing gets the same outcome without
that cost.

**Known risk, deliberately accepted.** The `s3:prefix` condition on
`ListBucket` is correct least privilege but is the kind of thing that surfaces
as `AccessDenied` during a Phase 7 `COPY` if Redshift lists outside `curated/`
— a manifest stored elsewhere, or a COPY issued against the bucket root. The
code comment names this as the first thing to check, with an unconditional
`ListBucket` as the fallback: listing is metadata, and `GetObject` stays scoped
either way.

**Cost accepted.** A customer-managed policy to maintain instead of an
AWS-managed one, and one more thing to get right at Phase 7.

---

## D20 — OPEN: the Glue role can delete raw objects

**Status: open. Must be settled in Phase 1, alongside raw immutability.**

**The problem.** `lake_access` grants the Glue role `s3:DeleteObject` on
`${lake}/*` — which includes `raw/`. The project claims raw is immutable and
append-only, but the principal that runs every ETL job can delete it. The
claim is currently a convention, not a control.

Surfaced while narrowing the Redshift role (D19): same class of over-grant, and
worth fixing for the same reason. It is recorded rather than fixed immediately
because the answer depends on the still-open raw-immutability decision, and
that decision belongs to Phase 1.

**Options, to decide in Phase 1.**

| | Approach | Trade-off |
| --- | --- | --- |
| A | Split `lake_access`: `GetObject` on `raw/*`, read-write on `processed/*`, `curated/*`, `quarantine/*` | Enforced in IAM. Slightly more policy to maintain, and re-running the generator into `raw/` needs a different principal — which is arguably correct. |
| B | Bucket policy denying `DeleteObject` on `raw/*` for everyone but an admin | Enforced at the resource, so it holds regardless of which principal is used. Can make `de.sh nuke` awkward. |
| C | Documented convention only | Zero enforcement. Cheapest, and honest only if described as a convention rather than a guarantee. |

A and B are complementary rather than exclusive: A limits the principal, B
limits the resource. Recommendation is A, with B considered if the exercise
wants defence in depth.

**Why not fix it now.** It would change the Glue role's permissions in the same
plan as the Redshift correction, mixing a settled decision with an open one.
The Redshift fix is unambiguous; this one has three defensible answers.

---

## D21 — One PNG export, Mermaid for the rest

**Decision.** `architecture-diagram.png` is committed, exported from the
draw.io Architecture tab. The Terraform layer split and the star schema live as
Mermaid in `architecture/architecture.md` rather than as further exports.

**Why one and not three.** The brief (§8) names `architecture-diagram.png`
specifically, and it is the diagram a reviewer actually wants: AWS service
icons, phase bands, the whole platform in one view. The other two are
supporting detail, and Mermaid renders them inline on GitHub with no export
step to forget.

**The staleness problem this accepts.** Exporting a PNG is manual - open
draw.io, select the tab, export, save over the old file. Nothing enforces it,
so the image can drift from its source unnoticed. That already happened once
here: the committed PNGs were three days behind the `.drawio` *and* had been
rendered from a Mermaid original they no longer resembled. The mitigation is
that there is now exactly one export to keep current, and the instruction for
producing it sits in `architecture.md` next to the diagram.

Mermaid cannot go stale in the same way: it is the source, so it is either
edited or it is not.

**Also relevant.** PNG export in draw.io is per-tab; there is no "all pages"
option, which is how three exports of the same tab ended up under three
filenames. PDF does have "All Pages" if a single multi-page artefact is ever
wanted.

**Cost accepted.** Two representations to keep roughly in step, and one manual
export step. Where they disagree the `.drawio` wins, and that precedence is
stated in `architecture.md`.

---

## D22 — Deduplication is a reconciliation category, not a discrepancy

The Phase 3 job reconciles `source == valid + rejected`. That identity is
false, and the first run that got far enough to write data failed on it:
13,803 source rows against 10,000 valid and 1 rejected.

Nothing was lost. `deduplicate()` had removed 3,802 rows — 3,761 genuine
duplicate orders, plus 41 of the 42 CSV header rows collapsing into one — and
the reconciliation had no term to put them in.

Two ways to make the arithmetic work:

- count `source_rows` *after* deduplication, so the identity holds trivially;
- keep the raw count and add `duplicates_removed` as an explicit term.

The second, because the first hides the number. "3,802 of 13,803 input rows
were duplicates" is a fact about the source data worth reporting every run —
it is how a duplicated upstream delivery would be noticed. Absorbing it into
the source count would make the check pass while destroying the evidence.

The contract is now:

    source  == duplicates_removed + valid + rejected
    dedup   == valid + rejected
    curated == valid

Worth noting *why* the unit tests missed it: the end-to-end test passed
`deduped.count()` as the source count, so it asserted the identity the module
could satisfy rather than the one the job actually used. The module was right;
the entry script wired it wrong. That is the third Phase 3 defect of the same
shape — packaging, Data Catalog access, and now this — all in the thin,
untestable layer between tested code and AWS. `scripts/glue_jobs/` earns
review attention disproportionate to its size.

---

## D23 — One DynamicFrame, at the read of orders only

Phase 3 decided DataFrames throughout and no DynamicFrames, and that decision
stands everywhere except a single line.

Job bookmarks are not a property of the job. `--job-bookmark-enable` tracks
state per `transformation_ctx` **on Glue's own readers**; a plain `spark.sql()`
read does not participate at all. Enabling the flag while reading through Spark
SQL produces a job that reprocesses everything on every run and reports
SUCCEEDED - it does not fail, it simply does nothing, which is why it would
have survived a casual review.

So `orders` is read with `create_dynamic_frame.from_catalog(...,
transformation_ctx="orders_source")` and converted with `.toDF()` on the next
line. It sits in `scripts/glue_jobs/curated_sales.py`, the layer that already
imports `awsglue` and already cannot be unit-tested. `transforms.py` is
untouched and every transform test remains valid.

The original objection was to DynamicFrames as a *data model* - `ResolveChoice`
and schema ambiguity leaking through the transformations. An adapter at the
integration boundary, discarded one line later, is a different thing, and only
the first was ever worth refusing.

**Dimensions are deliberately not bookmarked.** `customers` and `products` stay
on `spark.sql()` and are re-read in full every run. A bookmark on either means
the second run reads zero dimension rows, every order fails referential
integrity, and the job quarantines the whole tranche as orphans while reporting
a balanced reconciliation and SUCCEEDED. The reconciliation identity cannot
catch that: nothing is lost, it is merely all in the wrong bucket. Bookmarks
belong on the fact table that grows, never on the lookups.

---

## D24 — Lake Formation grants are split from the pipeline's own access

`lf_pipeline_grants_enabled` covers the Glue role's location, database and table
grants. `lf_grants_enabled` covers the persona matrix, the column restriction
and the LF-Tag. Two variables for what is, on paper, one feature.

They were briefly merged, on the reasoning that the gate boundary should match
the file boundary and IAM_ALLOWED_PRINCIPALS would carry the pipeline until the
governance work was ready. Gate 2 disproved the premise: with the location
registered and the fallback fully enabled, the ETL still could not read a row.
The Glue grants are not part of the demonstration - they are a prerequisite for
the pipeline running at all once the bucket is governed.

Keeping them separate also preserves the property that made this phase
debuggable: one change per gate. Applying the Glue grants alongside the first
post-registration pipeline run would have tested registration and grants
together, and a passing run would not have said which of the two carried it.

**The related decision: personas get named-resource grants, not LF-Tags.**
The matrix is three roles over four tables. Naming them says exactly what is
intended and is auditable by reading it. Tag-based access control is the model
that scales - tag the data once, grant against the tag - and it is demonstrated
on one table so the mechanism is evidenced, but building the authorisation model
out of tags at this size would add indirection without removing a decision.

## D25 — IAM_ALLOWED_PRINCIPALS is removed, permanently and deliberately

The fallback is Lake Formation's backwards-compatibility hatch: while it is
present, any principal whose IAM policy permits an action is allowed, and every
LF grant is advisory. A permission model that cannot refuse anything is not a
permission model, so the phase is only meaningful with it gone.

Two consequences worth stating plainly, because both outlive this phase:

- **The negative tests only mean something afterwards.** A control run with the
  fallback still enabled scored 7/11, with all four denials wrongly succeeding.
  That run was not wasted - it is what proves the later denials come from the
  column grant rather than from something incidental.
- **It changes the baseline for every future session.** The setting is
  account-level and survives teardown. From here on, the Glue role's LF grants
  are load-bearing: miss one and the crawler fails with a permissions error on
  unchanged code.

The alternative - leaving the fallback on and demonstrating the grants
theoretically - would have produced a governance phase that proved nothing and
left a trap for whoever removed it later.

