# Central log group for Glue job output. Short retention: these logs are
# captured into docs/evidence/ during the session and are worthless afterwards.
resource "aws_cloudwatch_log_group" "glue" {
  name              = "/aws-glue/jobs/${var.project}"
  retention_in_days = 3
}

# The alarm that closes the one real gap in "Monitoring & Failure Handling".
#
# WHY NOTIFICATION FROM INSIDE THE WORKFLOW IS NOT ENOUGH
# -------------------------------------------------------
# The state machine publishes to SNS on both outcomes, so a failed stage already
# notifies. But that only fires from INSIDE a running execution. A failure that
# stops the workflow starting at all is completely silent:
#
#   - the EventBridge rule is disabled or its pattern stops matching
#   - the Step Functions role loses a permission
#   - the Lake Formation grants are not re-applied after a teardown
#
# The last one is not hypothetical. Grants live in the training layer by design
# (D24), so they are destroyed nightly, and this project has hit exactly that
# failure three times. Each time it was noticed because someone was watching a
# terminal - which is not a monitoring strategy.
#
# This alarm watches the service metric rather than the workflow's own opinion of
# itself, so it fires whether or not an execution reached the notify state.
#
# One alarm, not a dashboard. The interesting question is "did a run fail" and
# ExecutionsFailed answers it; a dashboard nobody opens during a session that
# lasts an afternoon would be decoration.
resource "aws_cloudwatch_metric_alarm" "pipeline_failed" {
  alarm_name        = "${var.project}-pipeline-execution-failed"
  alarm_description = "A retail pipeline execution failed. Fires on the Step Functions service metric, so it catches failures that never reach the workflow's own notification."

  namespace   = "AWS/States"
  metric_name = "ExecutionsFailed"
  dimensions = {
    StateMachineArn = aws_sfn_state_machine.pipeline.arn
  }

  # Sum over one period, not an average: a single failed execution matters, and
  # averaging would let one failure disappear into a window of successes.
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"

  # Absent data is the normal state - the pipeline is event-driven and may go
  # hours without an execution. Treating missing data as breaching would alarm
  # on quiet, which is the fastest way to teach people to ignore it.
  treat_missing_data = "notBreaching"

  alarm_actions = [aws_sns_topic.pipeline.arn]
  ok_actions    = [aws_sns_topic.pipeline.arn]

  tags = {
    Name    = "${var.project}-pipeline-execution-failed"
    Purpose = "Alerts on pipeline failure independently of the workflow itself"
  }
}

