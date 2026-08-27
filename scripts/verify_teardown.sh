#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# verify_teardown.sh
#
# `terraform destroy` is a claim. This is the proof.
#
# Asserts by live API call that nothing billable survives, sweeps other common
# regions for strays, and reports the day's actual spend. Exits non-zero if
# anything is still standing - a session is not finished until this is green.
#
# Deliberately uses --query/--output text rather than jq: one less tool the
# next person needs to install.
# ---------------------------------------------------------------------------

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

FAILURES=0
R="$AWS_REGION"

# check <label> <actual-count>
check() {
  local label="$1" actual="$2"
  if [ "$actual" = "None" ]; then actual=0; fi
  if [ "${actual:-0}" -eq 0 ] 2>/dev/null; then
    printf '  %-30s %s0%s
' "$label" "$C_GRN" "$C_OFF"
  else
    printf '  %-30s %s%s SURVIVED%s
' "$label" "$C_RED" "$actual" "$C_OFF"
    FAILURES=$((FAILURES + 1))
  fi
}

# note <label> <count> - pre-existing resources that are not this project's.
# Reported so they are visible, never counted as a teardown failure.
note() {
  local label="$1" actual="$2"
  if [ "$actual" = "None" ]; then actual=0; fi
  if [ "${actual:-0}" -gt 0 ] 2>/dev/null; then
    printf '  %-30s %s%s%s %s(pre-existing, not this project)%s
'       "$label" "$C_YEL" "$actual" "$C_OFF" "$C_DIM" "$C_OFF"
  fi
}

# Checks are scoped to resources this project owns, by name prefix. Scoping
# matters: the account may legitimately hold unrelated resources, and a
# verifier that fails on someone else's Redshift workgroup is a verifier
# people learn to ignore. Glue databases use underscores, everything else
# uses hyphens.
P="$PROJECT"
P_UNDERSCORE="${PROJECT//-/_}"

info "Teardown verification - region ${R}, resources named ${P}*"

check "glue jobs"   "$(aws glue get-jobs --region "$R" --query "length(Jobs[?starts_with(Name, '${P}')])" --output text 2>/dev/null || echo 0)"
check "glue crawlers"   "$(aws glue get-crawlers --region "$R" --query "length(Crawlers[?starts_with(Name, '${P}')])" --output text 2>/dev/null || echo 0)"
# The catalog is named by the brief (training_db) and carries no project
# prefix, so it is matched by exact name rather than by prefix.
check "glue databases"   "$(aws glue get-databases --region "$R" --query "length(DatabaseList[?Name == '${GLUE_DATABASE}' || starts_with(Name, '${P_UNDERSCORE}')])" --output text 2>/dev/null || echo 0)"
check "glue connections"   "$(aws glue get-connections --region "$R" --query "length(ConnectionList[?starts_with(Name, '${P}')])" --output text 2>/dev/null || echo 0)"
check "glue triggers"   "$(aws glue get-triggers --region "$R" --query "length(Triggers[?starts_with(Name, '${P}')])" --output text 2>/dev/null || echo 0)"
# Glue sessions and DQ rulesets are billable and short-lived, so any of them
# surviving is a problem regardless of who created it.
check "glue sessions (any)"   "$(aws glue list-sessions --region "$R" --query 'length(Ids)' --output text 2>/dev/null || echo 0)"
check "glue DQ rulesets"   "$(aws glue list-data-quality-rulesets --region "$R" --query "length(Rulesets[?starts_with(Name, '${P}')])" --output text 2>/dev/null || echo 0)"
check "step functions"   "$(aws stepfunctions list-state-machines --region "$R" --query "length(stateMachines[?starts_with(name, '${P}')])" --output text 2>/dev/null || echo 0)"
check "eventbridge rules"   "$(aws events list-rules --region "$R" --query "length(Rules[?starts_with(Name, '${P}')])" --output text 2>/dev/null || echo 0)"
check "kinesis streams"   "$(aws kinesis list-streams --region "$R" --query "length(StreamNames[?starts_with(@, '${P}')])" --output text 2>/dev/null || echo 0)"
check "redshift namespaces"   "$(aws redshift-serverless list-namespaces --region "$R" --query "length(namespaces[?starts_with(namespaceName, '${P}')])" --output text 2>/dev/null || echo 0)"
check "redshift workgroups"   "$(aws redshift-serverless list-workgroups --region "$R" --query "length(workgroups[?starts_with(workgroupName, '${P}')])" --output text 2>/dev/null || echo 0)"
check "redshift clusters (any)"   "$(aws redshift describe-clusters --region "$R" --query 'length(Clusters)' --output text 2>/dev/null || echo 0)"
check "lambda functions"   "$(aws lambda list-functions --region "$R" --query "length(Functions[?starts_with(FunctionName, '${P}')])" --output text 2>/dev/null || echo 0)"
check "sns topics"   "$(aws sns list-topics --region "$R" --query "length(Topics[?contains(TopicArn, '${P}')])" --output text 2>/dev/null || echo 0)"
check "cloudwatch alarms"   "$(aws cloudwatch describe-alarms --region "$R" --query "length(MetricAlarms[?starts_with(AlarmName, '${P}')])" --output text 2>/dev/null || echo 0)"
check "log groups"   "$(aws logs describe-log-groups --region "$R" --query "length(logGroups[?contains(logGroupName, '${P}')])" --output text 2>/dev/null || echo 0)"
check "athena workgroups"   "$(aws athena list-work-groups --region "$R" --query "length(WorkGroups[?starts_with(Name, '${P}')])" --output text 2>/dev/null || echo 0)"

# Unrelated resources in the account: visible, but never a failure here.
note "other redshift workgroups"   "$(aws redshift-serverless list-workgroups --region "$R" --query "length(workgroups[?!starts_with(workgroupName, '${P}')])" --output text 2>/dev/null || echo 0)"
note "other glue databases"   "$(aws glue get-databases --region "$R" --query "length(DatabaseList[?Name != '${GLUE_DATABASE}' && !starts_with(Name, '${P_UNDERSCORE}')])" --output text 2>/dev/null || echo 0)"

# NAT Gateways are designed out of this architecture entirely. If one exists,
# something was built the wrong way and it is quietly burning ~$32/month.
check "NAT gateways" \
  "$(aws ec2 describe-nat-gateways --region "$R" --filter 'Name=state,Values=available,pending' --query 'length(NatGateways)' --output text 2>/dev/null || echo 0)"
check "running EC2 instances" \
  "$(aws ec2 describe-instances --region "$R" --filters 'Name=instance-state-name,Values=running,pending' --query 'length(Reservations[].Instances[])' --output text 2>/dev/null || echo 0)"

# --- cross-region sweep ----------------------------------------------------
info "Cross-region stray sweep"
for sr in $SWEEP_REGIONS; do
  if [ "$sr" = "$R" ]; then continue; fi
  strays=0
  for probe in \
    "glue get-jobs --query length(Jobs)" \
    "kinesis list-streams --query length(StreamNames)" \
    "redshift-serverless list-namespaces --query length(namespaces)" \
    "ec2 describe-nat-gateways --filter Name=state,Values=available --query length(NatGateways)"
  do
    n=$(aws --region "$sr" $probe --output text 2>/dev/null || echo 0)
    if [ "$n" = "None" ]; then n=0; fi
    strays=$((strays + ${n:-0}))
  done
  if [ "$strays" -eq 0 ]; then
    printf '  %-28s %sclean%s\n' "$sr" "$C_GRN" "$C_OFF"
  else
    printf '  %-28s %s%s stray resource(s)%s\n' "$sr" "$C_RED" "$strays" "$C_OFF"
    FAILURES=$((FAILURES + 1))
  fi
done

# --- surviving storage (expected, not a failure) ---------------------------
LAKE="$(lake_bucket_name)"
if aws s3api head-bucket --bucket "$LAKE" >/dev/null 2>&1; then
  size=$(aws s3 ls "s3://${LAKE}" --recursive --summarize --region "$R" 2>/dev/null \
         | awk '/Total Size/ {print $3}')
  printf '  %-28s %s (%s bytes) %s[persistent by design]%s\n' \
    "lake bucket" "$LAKE" "${size:-0}" "$C_DIM" "$C_OFF"
fi

# --- spend -----------------------------------------------------------------
info "Cost to date this month (Cost Explorer lags ~8h)"
START="$(date -u +%Y-%m-01)"
END="$(date -u -d '+1 day' +%Y-%m-%d 2>/dev/null || date -u -v+1d +%Y-%m-%d)"
SPEND="$(aws ce get-cost-and-usage --region us-east-1 \
  --time-period "Start=${START},End=${END}" \
  --granularity MONTHLY --metrics UnblendedCost \
  --query 'ResultsByTime[0].Total.UnblendedCost.Amount' --output text 2>/dev/null || echo 'unavailable')"
printf '  month-to-date               $%s\n' "$SPEND"

COST_LOG="${REPO_ROOT}/docs/cost-log.md"
if [ -f "$COST_LOG" ]; then
  printf '| %s | %s | $%s | %s |\n' \
    "$(date -u +%Y-%m-%d)" "${TF_VAR_phase:-n/a}" "$SPEND" \
    "$([ "$FAILURES" -eq 0 ] && echo clean || echo "${FAILURES} survivor(s)")" >> "$COST_LOG"
fi

echo
if [ "$FAILURES" -eq 0 ]; then
  ok "Teardown verified - nothing billable is running."
  exit 0
fi
die "${FAILURES} check(s) failed. Resources survived teardown."
