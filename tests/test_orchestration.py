"""Phase 8 orchestration checks.

These read the state machine definition and the EventBridge rule as data and
assert the properties the brief asks for: validation before work, the crawler
finishing before the ETL starts, retry, catch, and both outcomes reaching a
notification.

WHAT THESE TESTS CANNOT DO
--------------------------
There is no Amazon States Language validator in the installed AWS CLI, so
"the definition is valid" is not asserted here - only that it parses, that
every `Next` names a state that exists, and that the required structure is
present. A definition can satisfy all of that and still be rejected by Step
Functions at create time. Terraform's apply is the real syntax check, and the
execution history is the real behavioural check; these tests exist to catch the
structural mistakes that are cheap to make and expensive to find at the gate.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ASL_PATH = REPO_ROOT / "infrastructure" / "training" / "orchestration.asl.json"
TF_PATH = REPO_ROOT / "infrastructure" / "training" / "orchestration.tf"


@pytest.fixture(scope="module")
def asl() -> dict:
    """The definition with Terraform's template placeholders filled in.

    templatefile() substitutes ${...} at apply time; here they are replaced
    with recognisable stand-ins so the JSON parses and the structure can be
    inspected.
    """
    raw = ASL_PATH.read_text(encoding="utf-8")
    filled = (
        raw.replace("${crawler_name}", "TEST-crawler")
        .replace("${job_name}", "TEST-job")
        .replace("${topic_arn}", "arn:aws:sns:us-east-2:000000000000:TEST-topic")
    )
    return json.loads(filled)


@pytest.fixture(scope="module")
def terraform() -> str:
    return TF_PATH.read_text(encoding="utf-8")


def test_definition_parses_and_every_transition_targets_a_real_state(asl: dict) -> None:
    """A typo in a `Next` is the classic way to ship a broken workflow."""
    states = asl["States"]
    assert asl["StartAt"] in states

    referenced: set[str] = set()
    for body in states.values():
        if "Next" in body:
            referenced.add(body["Next"])
        for choice in body.get("Choices", []):
            referenced.add(choice["Next"])
        if "Default" in body:
            referenced.add(body["Default"])
        for catch in body.get("Catch", []):
            referenced.add(catch["Next"])

    dangling = sorted(referenced - set(states))
    assert not dangling, f"transitions point at states that do not exist: {dangling}"


def test_validate_file_is_a_choice_and_checks_all_three_conditions(asl: dict) -> None:
    """The brief's file validation, and the reason there is no Lambda.

    Prefix, suffix and non-zero size all have to be asserted; checking only the
    prefix would let a zero-byte file or a JSON drop start a crawl.
    """
    validate = asl["States"]["ValidateFile"]
    assert validate["Type"] == "Choice"

    conditions = validate["Choices"][0]["And"]
    rendered = json.dumps(conditions)

    assert '"StringMatches": "raw/orders/*"' in rendered, "prefix condition missing"
    assert '"StringMatches": "*.csv"' in rendered, "suffix condition missing"
    assert '"NumericGreaterThan": 0' in rendered, "size condition missing"


def test_a_non_matching_object_is_ignored_rather_than_alerted(asl: dict) -> None:
    """An unrelated upload is not an incident.

    Routing the default branch to the failure notification would page someone
    every time anything landed in the bucket, which trains people to ignore the
    alert that matters.
    """
    default = asl["States"]["ValidateFile"]["Default"]
    assert asl["States"][default]["Type"] == "Succeed"


def test_etl_cannot_start_until_the_crawler_has_actually_succeeded(asl: dict) -> None:
    """The dependency the brief asks for, asserted rather than assumed.

    READY only means the crawler stopped - it is also the state after a failed
    crawl. Both the state AND the last crawl's status have to be checked, or the
    ETL runs against a catalog the crawler failed to update.
    """
    finished = asl["States"]["CrawlerFinished"]
    assert finished["Type"] == "Choice"

    to_etl = [c for c in finished["Choices"] if c.get("Next") == "RunGlueETL"]
    assert len(to_etl) == 1, "exactly one branch should lead to the ETL"

    rendered = json.dumps(to_etl[0])
    assert "Crawler.State" in rendered and "READY" in rendered
    assert "LastCrawl.Status" in rendered and "SUCCEEDED" in rendered

    # And the polling loop must return to the wait, or a running crawl would
    # fall through.
    assert finished["Default"] == "WaitForCrawler"


def test_the_etl_task_is_synchronous(asl: dict) -> None:
    """.sync is what makes the workflow wait for the job to finish."""
    etl = asl["States"]["RunGlueETL"]
    assert etl["Resource"].endswith("glue:startJobRun.sync")


def test_retry_is_configured_on_both_aws_tasks_and_only_for_transient_errors(asl: dict) -> None:
    """Retrying a deterministic data failure bills three times to fail three times."""
    for name in ("StartCrawler", "RunGlueETL"):
        retries = asl["States"][name].get("Retry")
        assert retries, f"{name} has no Retry"

        rule = retries[0]
        assert rule["MaxAttempts"] == 2
        assert rule["BackoffRate"] > 1
        assert rule["IntervalSeconds"] > 0

        # States.ALL would sweep up genuine data failures along with transient
        # service errors, which is the mistake this asserts against.
        assert "States.ALL" not in rule["ErrorEquals"], f"{name} retries everything"
        assert all(e.startswith("Glue.") for e in rule["ErrorEquals"])


def test_every_failure_path_reaches_the_failure_notification(asl: dict) -> None:
    for name in ("StartCrawler", "GetCrawlerStatus", "RunGlueETL"):
        catches = asl["States"][name].get("Catch")
        assert catches, f"{name} has no Catch"
        assert catches[0]["ErrorEquals"] == ["States.ALL"]
        assert catches[0]["Next"] == "NotifyFailure"


def test_both_outcomes_notify_and_the_failure_path_still_fails(asl: dict) -> None:
    """A caught error must not report success.

    Catching an error and then ending the execution normally is the subtle way
    to make a broken pipeline look healthy in the console.
    """
    success = asl["States"]["NotifySuccess"]
    assert success["Resource"].endswith("sns:publish")
    assert success.get("End") is True

    failure = asl["States"]["NotifyFailure"]
    assert failure["Resource"].endswith("sns:publish")
    assert asl["States"][failure["Next"]]["Type"] == "Fail"


def test_no_lambda_anywhere_in_the_workflow(asl: dict) -> None:
    """The phase is orchestration, not new compute.

    Checks the Resource ARNs rather than the whole document. The definition's
    own comments explain WHY there is no Lambda, and a substring search over the
    rendered JSON matches that prose instead of the configuration. Asserting on
    the field that decides what actually runs is both stricter and immune to how
    the file is documented.
    """
    invoked = [
        body["Resource"]
        for body in asl["States"].values()
        if isinstance(body.get("Resource"), str)
    ]
    assert invoked, "no task states found"
    offenders = [r for r in invoked if "lambda" in r.lower()]
    assert not offenders, f"lambda integrations present: {offenders}"


def test_event_pattern_ands_the_prefix_and_suffix(terraform: str) -> None:
    """The bug this test exists for was real and was shipped in a first draft.

    Written as `key = [{prefix = "raw/orders/"}, {suffix = ".csv"}]`, the two
    matchers are an OR, not an AND. `aws events test-event-pattern` showed that
    form matching `athena-results/x.csv` and `raw/customers/customers.csv` -
    every CSV in the bucket, including the pipeline's own Athena output, which
    is exactly the self-triggering loop the filter was supposed to prevent.

    A single `wildcard` matcher ANDs them. This test pins that, because the
    array form looks more explicit and is the natural thing to write.
    """
    pattern = re.search(r"event_pattern\s*=\s*jsonencode\((.*?)\n  \}\)", terraform, re.S)
    assert pattern, "event_pattern not found"

    # Comments are stripped first. The block deliberately explains the
    # prefix/suffix trap in prose, and a naive substring search would match the
    # explanation rather than the configuration it warns about.
    body = "\n".join(
        line for line in pattern.group(1).splitlines() if not line.strip().startswith("#")
    )

    assert 'wildcard = "raw/orders/*.csv"' in body, "expected a single wildcard matcher"
    assert "prefix" not in body and "suffix" not in body, (
        "prefix/suffix matchers in an array are ORed - use one wildcard instead"
    )


def test_the_step_functions_role_is_scoped_to_named_resources(terraform: str) -> None:
    """Least privilege, asserted so a later 'quick fix' has to argue with a test."""
    for forbidden in ('"glue:*"', '"sns:*"', '"s3:*"', '"iam:*"', "AdministratorAccess"):
        assert forbidden not in terraform, f"over-broad permission: {forbidden}"

    # Resources are named, not wildcarded.
    assert "aws_glue_crawler.raw.name" in terraform
    assert "aws_glue_job.curated_sales.name" in terraform
    assert "aws_sns_topic.pipeline.arn" in terraform


def test_standard_workflow_so_the_execution_history_survives(terraform: str) -> None:
    """Express keeps no console history, and the history is the deliverable."""
    assert 'type = "STANDARD"' in terraform
