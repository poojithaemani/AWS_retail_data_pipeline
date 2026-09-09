# ---------------------------------------------------------------------------
# Orchestration - Phase 8.
#
#   S3 arrival -> EventBridge -> Step Functions
#     -> ValidateFile -> Crawler -> Glue ETL -> Notify
#
# Five resources, and none of them is a pipeline. The crawler and the ETL job
# already exist and are proven; this file only decides when they run and what
# happens when they do not. Nothing here modifies transforms.py, the job, the
# crawler, or any Phase 5/6 behaviour.
#
# NO LAMBDA, DELIBERATELY
# -----------------------
# Every step the brief asks for has a native integration or a Choice state:
# file validation is three conditions on the event, the crawler and job have
# AWS SDK and .sync integrations, and SNS publishes directly. A Lambda would
# add a runtime, a package and an IAM role to do what the state machine already
# does - and would be a second thing to keep working.
#
# WHAT IS NOT ORCHESTRATED, STATED PLAINLY
# ----------------------------------------
# The brief's diagram ends "... -> Data Quality -> PASS/FAIL -> Publish ->
# Notify". Two of those are not separate states here, and pretending otherwise
# would misrepresent what runs:
#
#   Data Quality  The ETL already validates every row, routes rejects to
#                 quarantine/ with a reason, and fails on an unbalanced
#                 reconciliation. Its success or failure IS the quality
#                 outcome. Adding a separate Glue DQ evaluation would be a
#                 second, slower answer to a question already answered - and
#                 calling a generic JobRun SUCCEEDED a "DQ evaluation" would be
#                 a lie about what was measured.
#
#   Publish       scripts/publish_catalog.py is a local Python script that
#                 renames crawler tables. Step Functions cannot invoke a local
#                 script, and the only way to include it would be to wrap it in
#                 a Lambda built solely to satisfy a box on a diagram. It stays
#                 an operational step outside the state machine, and this
#                 comment exists so nobody later believes it was automated.
# ---------------------------------------------------------------------------

resource "aws_sns_topic" "pipeline" {
  name = "${var.project}-pipeline-events"

  tags = {
    Name    = "${var.project}-pipeline-events"
    Purpose = "Retail pipeline success and failure notifications"
  }
}

# No email subscription, deliberately.
#
# An SNS email subscription requires the recipient to click a confirmation link
# before anything is delivered. Created inside a session that tears down the
# same day, it would sit in PendingConfirmation and deliver nothing - a
# resource that looks like a notification channel and demonstrably is not.
#
# The topic itself is what the state machine needs, and sns:Publish either
# succeeds or fails visibly in the execution history, which is the evidence
# that matters. Adding a subscriber is one resource whenever a real inbox
# should receive these.

# --- the state machine's identity ------------------------------------------

data "aws_iam_policy_document" "sfn_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sfn" {
  name               = "${var.project}-sfn-role"
  assume_role_policy = data.aws_iam_policy_document.sfn_assume.json

  tags = {
    Name = "${var.project}-sfn-role"
    # Slashes rather than commas - IAM tag values reject a comma, which cost an
    # apply in Phase 0.
    Purpose = "Runs the retail orchestration: crawler / ETL / notify"
  }
}

# Least privilege, named resource by resource. No glue:*, no sns:*, no
# wildcards on ARNs. If AWS refuses something at run time the error names the
# action, and that is a better basis for a grant than guessing now.
data "aws_iam_policy_document" "sfn" {
  statement {
    sid       = "RunTheCrawler"
    effect    = "Allow"
    actions   = ["glue:StartCrawler", "glue:GetCrawler"]
    resources = ["arn:aws:glue:${var.region}:${local.account_id}:crawler/${aws_glue_crawler.raw.name}"]
  }

  statement {
    sid    = "RunTheEtlJob"
    effect = "Allow"
    actions = [
      "glue:StartJobRun",
      "glue:GetJobRun",
      "glue:GetJobRuns",
      # .sync stops the job if the execution is aborted; without this the run
      # would outlive the workflow that started it.
      "glue:BatchStopJobRun",
    ]
    resources = ["arn:aws:glue:${var.region}:${local.account_id}:job/${aws_glue_job.curated_sales.name}"]
  }

  statement {
    sid       = "Notify"
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.pipeline.arn]
  }
}

resource "aws_iam_role_policy" "sfn" {
  name   = "${var.project}-sfn-policy"
  role   = aws_iam_role.sfn.id
  policy = data.aws_iam_policy_document.sfn.json
}

# --- the workflow -----------------------------------------------------------

resource "aws_sfn_state_machine" "pipeline" {
  name     = "${var.project}-pipeline"
  role_arn = aws_iam_role.sfn.arn

  # Standard, not Express. Express workflows are cheaper per execution but cap
  # at five minutes and keep no execution history in the console - and the
  # history IS the Phase 8 deliverable, since retries and the catch transition
  # have to be visible. A crawl plus an ETL run also takes longer than the
  # Express limit.
  type = "STANDARD"

  definition = templatefile("${path.module}/orchestration.asl.json", {
    crawler_name = aws_glue_crawler.raw.name
    job_name     = aws_glue_job.curated_sales.name
    topic_arn    = aws_sns_topic.pipeline.arn
  })

  tags = {
    Name    = "${var.project}-pipeline"
    Purpose = "Event-driven retail ingestion: validate / crawl / transform / notify"
  }
}

# --- the trigger ------------------------------------------------------------

# Narrow on purpose. The lake bucket holds curated output, quarantine, Athena
# results, scripts and experiments, and every one of those is written by the
# pipeline itself. A rule that matched the whole bucket would retrigger on its
# own output - the ETL writes curated/, which fires the rule, which runs the
# ETL. The prefix and suffix filters are what stop that loop.
resource "aws_cloudwatch_event_rule" "raw_arrival" {
  name        = "${var.project}-raw-orders-arrival"
  description = "A new orders CSV under raw/orders/ starts the pipeline"

  event_pattern = jsonencode({
    source        = ["aws.s3"]
    "detail-type" = ["Object Created"]
    detail = {
      bucket = { name = [local.lake_bucket] }
      # A single wildcard, NOT [{prefix=...},{suffix=...}].
      #
      # An array of matchers on one field is an OR in EventBridge, which was
      # verified rather than assumed - `aws events test-event-pattern` showed
      # the array form firing on athena-results/x.csv and on
      # raw/customers/customers.csv, i.e. any CSV anywhere plus anything under
      # raw/orders/. That is precisely the self-triggering loop the narrowing
      # was meant to prevent.
      #
      # The wildcard matcher ANDs the prefix and suffix in one expression. Same
      # test, same four keys: only raw/orders/**.csv matches.
      object = {
        key = [{ wildcard = "raw/orders/*.csv" }]
      }
    }
  })

  tags = {
    Name    = "${var.project}-raw-orders-arrival"
    Purpose = "Event-driven trigger for the retail pipeline"
  }
}

resource "aws_cloudwatch_event_target" "raw_arrival" {
  rule     = aws_cloudwatch_event_rule.raw_arrival.name
  arn      = aws_sfn_state_machine.pipeline.arn
  role_arn = aws_iam_role.events.arn

  # Flattens the S3 event into the shape the state machine works with, and adds
  # the catalog database as an input rather than hard-coding it in the ASL.
  #
  # That last part is what makes the deliberate failure exercise possible
  # without touching data or code: a manual execution supplies a database that
  # does not exist, the ETL fails for real, and the Retry and Catch paths run
  # exactly as they would in an incident. The event-driven path always supplies
  # the real one.
  input_transformer {
    input_paths = {
      bucket = "$.detail.bucket.name"
      key    = "$.detail.object.key"
      size   = "$.detail.object.size"
    }

    input_template = <<-TPL
      {"bucket": <bucket>, "key": <key>, "size": <size>, "database": "${var.glue_database}"}
    TPL
  }
}

data "aws_iam_policy_document" "events_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "events" {
  name               = "${var.project}-events-role"
  assume_role_policy = data.aws_iam_policy_document.events_assume.json

  tags = {
    Name    = "${var.project}-events-role"
    Purpose = "Lets EventBridge start the retail pipeline"
  }
}

resource "aws_iam_role_policy" "events" {
  name = "${var.project}-events-policy"
  role = aws_iam_role.events.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "StartThePipeline"
      Effect   = "Allow"
      Action   = "states:StartExecution"
      Resource = aws_sfn_state_machine.pipeline.arn
    }]
  })
}
