# Phase 09 — evidence

Phase 9 is the capstone: documentation, the self-assessment, and closing the one
gap the assessment found. It produced no new pipeline, so this pack is small and
deliberately shaped differently from phases 0–8.

The other packs were captured by `de.sh evidence NN` while a training layer was
running. There is nothing equivalent here — the phase's deliverables are
documents, and re-running the collectors after teardown would only record an
empty account, which `docs/cost-log.md` already does.

## What is here

| | |
| --- | --- |
| `monitoring.json` | the CloudWatch alarm, its configuration, and its full state history |

## Why the alarm has its own evidence file

The self-assessment against the brief's rubric (`docs/capstone.md` §10) found
one genuine gap: **Monitoring & Failure Handling**. Failure *handling* was
strong — Retry and Catch demonstrated on real failures, SNS on both outcomes,
and an ETL that fails itself when its reconciliation does not balance.
Monitoring was thin in one specific way: every notification came from *inside* a
running execution, so a failure that stopped the workflow starting at all would
have been silent.

That was closed with a single `aws_cloudwatch_metric_alarm` on the Step
Functions service metric `ExecutionsFailed`, and then **demonstrated rather than
asserted**:

    17:04:28   INSUFFICIENT_DATA -> OK
               "1 missing datapoint was treated as [NonBreaching]"

    17:38:28   OK -> ALARM
               "1 datapoint [1.0] was greater than the threshold (0.0)"

## The trigger was a real failure, not a synthetic one

Lake Formation grants live in the training layer by design (D24), so teardown
destroys them. An execution started without re-applying them produced a genuine
refusal:

    Crawler:  State=READY   LastCrawl=FAILED
    error:    Insufficient Lake Formation permission(s):
              Required Describe on training_db

That is precisely the silent-failure class the alarm exists for, and one this
project had already hit three times during phases 6–8 — each time noticed only
because somebody happened to be watching a terminal.

The same execution incidentally proved a second thing. The workflow reached
`CrawlerFinished` and stopped there rather than continuing, because that Choice
state checks `LastCrawl.Status` as well as `State`: **READY means the crawl
stopped, not that it worked.** Without that check the ETL would have run against
a catalog the crawler had failed to update.

## Related evidence

- `../phase-08/orchestration.json` — the three earlier executions, including
  Retry recovering from a transient error and Catch handling a deterministic one
- `../../capstone.md` §10 — the rubric self-assessment this phase acted on
- `../../learnings.md` — the per-phase write-up, indexed
