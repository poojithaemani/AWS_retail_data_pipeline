# ---------------------------------------------------------------------------
# Cost guardrails. Created before anything billable exists.
#
# Two budgets with distinct jobs, deliberately:
#
#   DAILY   the immediate failed-teardown alarm. Fires the morning after a
#           session that ended with the training layer still standing.
#   MONTHLY the backup guardrail. Catches slow leaks the daily alarm sits
#           under, and caps total exposure for the project.
#
# Both keep the same simple shape: one subscriber, low thresholds, actual
# spend only, and no way to switch them off. There is no conditional on
# whether an email was supplied - a guardrail that can be skipped by omission
# is exactly how guardrails get skipped.
#
# What these do NOT do: alarm on a stack that is merely still standing.
# Redshift Serverless scales to zero and Glue costs nothing between runs, so
# an idle-but-undestroyed training layer can sit well under $5/day and never
# trigger. Budgets watch spend; `de.sh verify` is what watches existence.
# The two are complements, not substitutes.
# ---------------------------------------------------------------------------

# --- Daily: the primary alarm ----------------------------------------------

resource "aws_budgets_budget" "daily" {
  provider = aws.billing

  name         = "${var.project}-daily"
  budget_type  = "COST"
  limit_amount = tostring(var.daily_budget_usd)
  limit_unit   = "USD"
  time_unit    = "DAILY"

  # A single threshold on a deliberately low limit. An active session costs
  # roughly $4, so $5 leaves little headroom - an unusually heavy day may
  # alert. That is the intended trade: a false positive costs an email, a
  # false negative costs a month of Redshift.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_notification_email]
  }
}

# --- Monthly: the backup guardrail -----------------------------------------

resource "aws_budgets_budget" "monthly" {
  provider = aws.billing

  name         = "${var.project}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # Early thresholds, so the first warning arrives at half the ceiling rather
  # than at the ceiling. Forecast-based alerts are deliberately absent: this
  # workload is built up and torn down every session, and forecasting from a
  # few active hours projects figures that mean nothing. An alert people learn
  # to ignore is worse than no alert.
  dynamic "notification" {
    for_each = [50, 80, 100]

    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = [var.budget_notification_email]
    }
  }
}

# --- Cost attribution -------------------------------------------------------

# Activating the Project tag for cost allocation makes per-project spend
# visible in Cost Explorer, broken down by the domain identity
# (Project=retail-data-pipeline) rather than lumped into the account total.
#
# AWS only accepts a tag key it has already observed on a resource, so this
# stays off until the first apply has created something carrying the tag, and
# is switched on with `enable_cost_allocation_tag = true` on the second apply.
resource "aws_ce_cost_allocation_tag" "project" {
  provider = aws.billing
  count    = var.enable_cost_allocation_tag ? 1 : 0

  tag_key = "Project"
  status  = "Active"
}
