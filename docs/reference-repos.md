# Reference repositories

Section 7 of the brief is explicit that the AWS sample repositories are the
*method*, not optional background reading, and prescribes a process for each:

```
Clone → Understand Architecture → Deploy/Execute → Validate Output
      → Replace Dataset → Modify Transformation → Introduce Failure
      → Troubleshoot → Enhance → Document → Commit
```

It is equally explicit that deploying a sample is **not** completion, and Day 10
says the repositories "should only be used as references" — the capstone must
not reproduce one.

This file resolves that tension by recording, per repository, what was studied,
what was borrowed, and — more usefully for the walkthrough — **what was
deliberately rejected and why**. A borrowed pattern you can't argue against is
a pattern you don't understand.

Filled in as each phase reaches the relevant repository. Not homework: the
"rejected" column is the raw material for the *"what alternatives exist?"*
question in §7.

---

## Mandatory

### `aws-samples/aws-glue-samples`
Used in: Phases 3, 4 · Purpose: Glue ETL and PySpark examples

| | |
| --- | --- |
| Studied | _pending Phase 3_ |
| Patterns borrowed | |
| Rejected, and why | |
| Applies to our dataset how | |

Focus per the brief: DynamicFrames, DataFrames, joins, mapping,
relationalization, data cleaning, `ApplyMapping`, `ResolveChoice`.

Question to answer: **DynamicFrame or DataFrame?** DynamicFrames handle schema
ambiguity (`ResolveChoice`) which is exactly the `price = UNKNOWN` problem from
Phase 2; DataFrames are faster and have the richer API. The honest answer is
probably "DynamicFrame at the boundary, DataFrame in the middle" — but it needs
demonstrating, not asserting.

---

### `aws-samples/data-engineering-for-aws-immersion-day`
Used in: Phase 1 · Purpose: end-to-end AWS Data Engineering reference

| | |
| --- | --- |
| Studied | _pending Phase 1_ |
| Patterns borrowed | |
| Rejected, and why | |

---

### `aws-samples/aws-glue-local-development`
Used in: Phase 4 · Purpose: local Glue development

Docker 29.2.1 is available on this machine, so the `amazon/aws-glue-libs`
container path is viable. The point per the brief is to stop making code
changes directly in the console.

| | |
| --- | --- |
| Studied | _pending Phase 4_ |
| Container image / Glue version match | must match Glue 5.0 (Spark 3.5 / Python 3.11) |
| What can be tested locally | |
| What genuinely cannot | |

---

### `aws-samples/amazon-deequ-glue`
Used in: Phase 5 · Purpose: Data Quality

**Deviation to justify, not to hide.** The brief lists this as Mandatory, but
the plan here is to implement data quality with **Glue Data Quality (DQDL)**
rather than Deequ.

Reasoning to validate during Phase 5:

| | Deequ on Glue | Glue Data Quality |
| --- | --- | --- |
| Language | Scala library, needs a JAR on the job | Native, DQDL rules as config |
| Integration | Manual wiring, custom job | First-class: rulesets, results API, EventBridge events |
| Teardown | Extra artefacts to manage | Terraform resource, destroys cleanly |
| Lineage to this project | — | `capture_evidence.py` already collects DQ rulesets and per-rule verdicts |
| Maturity | Predates Glue DQ; still the reference implementation | The managed successor, built on Deequ |

The decisive point: Glue Data Quality *is* Deequ, productised — AWS built it on
the Deequ engine. Reading the Deequ repo is still worthwhile for understanding
what the checks do underneath, so the plan is **study Deequ, implement DQDL**,
and be able to explain the trade-off. If that reasoning does not survive Phase 5,
switch.

| | |
| --- | --- |
| Studied | _pending Phase 5_ |
| Constraint checks worth stealing | |
| Verdict on the deviation | |

---

### `aws-samples/data-lake-as-code`
Used in: Phase 6 · Purpose: Lake Formation and governance

| | |
| --- | --- |
| Studied | _pending Phase 6_ |
| Grant model borrowed | |
| Rejected, and why | |

Note: `aws-samples/aws-lakeformation-access-controls-automation` is listed as an
advanced reference. Cross-account Lake Formation is explicitly out of scope.

---

### `aws-samples/aws-step-functions-etl-pipeline-pattern`
Used in: Phase 8 · Purpose: ETL orchestration

Additional reference:
`aws-samples/sample-athena-based-glue-data-pipelines-via-step-functions`

| | |
| --- | --- |
| Studied | _pending Phase 8_ |
| State machine shape borrowed | |
| Retry/Catch policy borrowed | |
| Rejected, and why | |

---

### `aws-samples/consuming-flattening-and-joining-multiple-json-data-sources-with-aws-glue-streaming`
Used in: optional streaming phase · Purpose: streaming

Marked Mandatory in the brief's repository table, but Day 9 carries **0% of the
capstone rubric**, and Glue Streaming is the only service in scope that runs
continuously with no idle state. Deferred to a single bounded window, pending a
decision on whether to run it at all.

---

## Advanced — explicitly out of scope

Excluded on instruction. The brief itself labels the first two "Optional
Advanced Exercise".

| Repository | Topic |
| --- | --- |
| `aws-samples/aws-glue-streaming-etl-with-apache-iceberg` | Iceberg + streaming |
| `aws-samples/transactional-datalake-using-apache-iceberg-on-aws-glue` | CDC + Iceberg |

Worth being able to say *why* they were excluded and what they would add:
Iceberg brings ACID transactions, schema evolution, snapshots and time travel to
the lake, which is the answer to "how would you handle updates and deletes in
`curated/`?" — a question plain partitioned Parquet answers badly.

---

## Reference

### `aws-samples/amazon-redshift-query-patterns-and-optimizations`
Used in: Phase 7 · Purpose: Redshift optimisation

| | |
| --- | --- |
| Studied | _pending Phase 7_ |
| Distribution / sort key reasoning | |
| Query patterns borrowed | |

---

### `aws-samples/aws-glue-test-data-generator`
Purpose: synthetic data generation

**Deviation.** The brief suggests this for generating 100k customers / 5k
products / 1M orders. This project uses a purpose-built generator instead
(`src/generate/`).

Reason: every AWS resource here is destroyed at the end of each session, so the
raw layer is rebuilt from code each time. That makes **byte-level
reproducibility** a hard requirement — the incremental-processing baseline
("second run reprocessed 0 rows") and the data-quality baseline ("exactly these
rows were quarantined") are meaningless if the input drifts between runs. The
local generator is seeded, emits a SHA-256 manifest, and is covered by tests
that assert two runs produce identical bytes.

It also produces the defect ground truth (`data/dirty/defects.json`) that
Phase 5 asserts against, which a generic generator does not.
