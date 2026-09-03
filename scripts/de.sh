#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# de.sh - single entry point for the AWS Data Engineering training platform.
#
#   ./scripts/de.sh bootstrap        one-time: state bucket + persistent layer
#   ./scripts/de.sh up [PHASE]       create the ephemeral training layer
#   ./scripts/de.sh gen              generate the synthetic retail dataset
#   ./scripts/de.sh load             upload the dataset into the raw layer
#   ./scripts/de.sh evidence PHASE   capture proof of execution
#   ./scripts/de.sh benchmark        Phase 1 format comparison
#   ./scripts/de.sh partexp          Phase 1 over-partitioning / small files
#   ./scripts/de.sh crawl            run the Glue crawler and wait
#   ./scripts/de.sh publish          crawler names -> required table names
#   ./scripts/de.sh runjob           run the curated-sales Glue ETL job
#   ./scripts/de.sh dq               evaluate the Glue Data Quality rulesets
#   ./scripts/de.sh analytics        run the Phase 5 Athena queries
#   ./scripts/de.sh orphans          Phase 3 orphan-FK delivery
#   ./scripts/de.sh breakschema      Phase 2 schema-break delivery
#   ./scripts/de.sh down             destroy the ephemeral training layer
#   ./scripts/de.sh verify [PHASE]   assert zero resources + report spend
#   ./scripts/de.sh nuke             destroy EVERYTHING, persistent included
#   ./scripts/de.sh status           what exists right now
#   ./scripts/de.sh plan [PHASE]     terraform plan for the training layer
#   ./scripts/de.sh fmt              terraform fmt + python format
#
# Anything that creates, changes or destroys AWS resources runs
# plan -> show -> confirm -> apply. That includes the Terraform state bucket,
# which is created by the AWS CLI rather than by Terraform but is still an AWS
# mutation creating a persistent resource.
#
#   --yes         skip the confirmation prompt; never the default
#   --plan-only   show the plan and stop, applying nothing
#
# The session loop is:  up -> work -> evidence -> commit -> down -> verify
# ---------------------------------------------------------------------------

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

cmd_bootstrap() {
  require_tools
  info "Preflight"
  local acct; acct="$(account_id)"
  ok "account ${acct}, region ${AWS_REGION}"

  # The budget subscriber lives in the persistent layer's tfvars, which
  # Terraform loads by itself. It is checked here rather than left to
  # Terraform so the failure arrives before anything is created.
  local tfvars="${PERSISTENT_DIR}/terraform.tfvars"
  [ -f "$tfvars" ] || die \
    "Missing ${tfvars}
     cp infrastructure/persistent/terraform.tfvars.example infrastructure/persistent/terraform.tfvars
     then set a real budget alert address."
  if grep -q "you@example.com" "$tfvars"; then
    die "budget_notification_email in ${tfvars} is still the placeholder.
     A budget nobody is subscribed to is not a guardrail, it is decoration."
  fi

  local bucket; bucket="$(state_bucket_name)"

  # The state bucket is created here, not by Terraform: Terraform cannot own
  # the bucket its own state lives in without a local-state migration dance.
  #
  # It is still an AWS mutation creating a persistent resource, so it gets the
  # same explicit approval as anything Terraform does. It also has to happen
  # before the persistent layer can be planned at all, because the backend
  # lives in it - which is why this gate is separate from the plan below.
  if aws s3api head-bucket --bucket "$bucket" >/dev/null 2>&1; then
    ok "state bucket ${bucket} already exists"
  else
    echo
    info "This will CREATE one persistent AWS resource, outside Terraform:"
    echo
    echo "  # aws_s3_bucket  (created by the AWS CLI, not managed by Terraform)"
    echo "      bucket               = ${bucket}"
    echo "      region               = ${AWS_REGION}"
    echo "      versioning           = Enabled"
    echo "      encryption           = AES256 (SSE-S3)"
    echo "      public_access_block  = all four settings true"
    echo "      tags                 = Project=${PROJECT}, Layer=persistent, ManagedBy=bootstrap"
    echo
    echo "  Plan: 1 to add, 0 to change, 0 to destroy."
    echo
    warn "PERSISTENT. Not removed by 'down'. Holds Terraform state for both layers."
    echo

    # Approval is checked first, so --yes can approve this prerequisite while
    # --plan-only still stops at the Terraform plan further down. Checking
    # --plan-only first would make the two flags mutually exclusive and leave
    # no way to reach the persistent-layer plan in a single reviewed step.
    if ! confirm "Create this state bucket? Type 'yes' to continue:"; then
      if [ "${PLAN_ONLY:-0}" = "1" ]; then
        dim "  --plan-only: nothing created."
        dim "  The persistent layer cannot be planned until this bucket exists,"
        dim "  because Terraform's backend lives in it."
        exit 0
      fi
      die "State bucket not created. Nothing in AWS was changed."
    fi

    info "Creating state bucket ${bucket}"
    aws s3api create-bucket --bucket "$bucket" --region "$AWS_REGION" \
      --create-bucket-configuration "LocationConstraint=${AWS_REGION}" >/dev/null
    aws s3api put-bucket-versioning --bucket "$bucket" \
      --versioning-configuration Status=Enabled
    aws s3api put-bucket-encryption --bucket "$bucket" \
      --server-side-encryption-configuration \
      '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
    aws s3api put-public-access-block --bucket "$bucket" \
      --public-access-block-configuration \
      'BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true'
    aws s3api put-bucket-tagging --bucket "$bucket" \
      --tagging "TagSet=[{Key=Project,Value=${PROJECT}},{Key=Layer,Value=persistent},{Key=ManagedBy,Value=bootstrap}]"
    ok "state bucket created"
  fi

  # The persistent layer only. bootstrap never touches the training layer -
  # the two have separate lifecycles and separate state files.
  info "Persistent layer (never destroyed by 'down')"
  tf_init "$PERSISTENT_DIR" "$bucket"
  if ! tf_change "$PERSISTENT_DIR" "persistent layer"; then
    die "Persistent layer not applied. The state bucket above already exists;
     re-running bootstrap is safe and idempotent."
  fi
  ok "persistent layer ready"

  dim "Next: ./scripts/de.sh up 00"
}

cmd_up() {
  require_tools
  local phase="${1:-00}"
  export TF_VAR_phase="$phase"
  local bucket; bucket="$(state_bucket_name)"
  export TF_VAR_state_bucket="$bucket"

  info "Training layer (phase ${phase})"
  tf_init "$TRAINING_DIR" "$bucket"
  if ! tf_change "$TRAINING_DIR" "training layer (phase ${phase})"; then
    die "Training layer not applied."
  fi
  ok "training layer up"
  terraform -chdir="$TRAINING_DIR" output
}

cmd_plan() {
  require_tools
  local phase="${1:-00}"
  export TF_VAR_phase="$phase"
  local bucket; bucket="$(state_bucket_name)"
  export TF_VAR_state_bucket="$bucket"
  tf_init "$TRAINING_DIR" "$bucket"
  terraform -chdir="$TRAINING_DIR" plan -input=false
}

cmd_down() {
  require_tools
  local bucket; bucket="$(state_bucket_name)"
  export TF_VAR_state_bucket="$bucket"
  export TF_VAR_phase="${TF_VAR_phase:-00}"

  # Scoped to the training layer's own state file, which manages only the
  # ephemeral resources. The persistent layer is a separate state and cannot
  # be reached from here.
  info "Training layer teardown"
  tf_init "$TRAINING_DIR" "$bucket"
  if ! tf_change "$TRAINING_DIR" "training layer" destroy; then
    die "Training layer NOT destroyed. Resources are still running."
  fi
  ok "training layer destroyed"
  dim "Now run: ./scripts/de.sh verify"
}

cmd_gen()      { $(python_bin) "${REPO_ROOT}/src/generate/generate_retail_data.py" "$@"; }
cmd_load()     { "${REPO_ROOT}/scripts/load_raw.sh" "$@"; }
cmd_evidence()  { $(python_bin) "${REPO_ROOT}/scripts/capture_evidence.py" "$@"; }
cmd_benchmark() { $(python_bin) "${REPO_ROOT}/scripts/benchmark_formats.py" "$@"; }
cmd_partexp()   { $(python_bin) "${REPO_ROOT}/scripts/partition_experiments.py" "$@"; }
cmd_breakschema() { $(python_bin) "${REPO_ROOT}/src/generate/break_schema.py" "$@"; }
cmd_crawl()     { "${REPO_ROOT}/scripts/run_crawler.sh" "$@"; }
cmd_publish()   { $(python_bin) "${REPO_ROOT}/scripts/publish_catalog.py" "$@"; }
cmd_runjob()    { "${REPO_ROOT}/scripts/run_glue_job.sh" "$@"; }
cmd_dq()        { $(python_bin) "${REPO_ROOT}/scripts/run_data_quality.py" "$@"; }
cmd_analytics() { $(python_bin) "${REPO_ROOT}/scripts/athena_analytics.py" "$@"; }
cmd_orphans()   { $(python_bin) "${REPO_ROOT}/src/generate/orphan_delivery.py" "$@"; }
# Optional phase argument is recorded in docs/cost-log.md, so a row can be
# traced back to the session that produced it. Without it the row reads "n/a",
# which is honest but not much use later.
cmd_verify() {
  if [ -n "${1:-}" ]; then export TF_VAR_phase="$1"; shift; fi
  "${REPO_ROOT}/scripts/verify_teardown.sh" "$@"
}

cmd_status() {
  require_tools
  local acct bucket lake
  acct="$(account_id)"; bucket="$(state_bucket_name)"; lake="$(lake_bucket_name)"

  printf 'account         %s\n' "$acct"
  printf 'region          %s\n' "$AWS_REGION"
  printf 'state bucket    %s\n' \
    "$(aws s3api head-bucket --bucket "$bucket" >/dev/null 2>&1 && echo "$bucket" || echo 'MISSING')"
  printf 'lake bucket     %s\n' \
    "$(aws s3api head-bucket --bucket "$lake" >/dev/null 2>&1 && echo "$lake" || echo 'MISSING')"
  printf 'glue databases  %s\n' \
    "$(aws glue get-databases --region "$AWS_REGION" --query 'length(DatabaseList)' --output text)"
  printf 'glue jobs       %s\n' \
    "$(aws glue get-jobs --region "$AWS_REGION" --query 'length(Jobs)' --output text)"
  printf 'glue crawlers   %s\n' \
    "$(aws glue get-crawlers --region "$AWS_REGION" --query 'length(Crawlers)' --output text)"
  printf 'state machines  %s\n' \
    "$(aws stepfunctions list-state-machines --region "$AWS_REGION" --query 'length(stateMachines)' --output text)"
  printf 'redshift ns     %s\n' \
    "$(aws redshift-serverless list-namespaces --region "$AWS_REGION" --query 'length(namespaces)' --output text 2>/dev/null || echo 0)"
}

cmd_nuke() {
  require_tools
  local lake; lake="$(lake_bucket_name)"
  warn "This destroys BOTH layers and permanently deletes ${lake}."
  read -r -p "Type the bucket name to confirm: " typed_name
  [ "$typed_name" = "$lake" ] || die "Confirmation did not match. Nothing was deleted."

  cmd_down || warn "training layer destroy reported an error; continuing"

  local bucket; bucket="$(state_bucket_name)"
  info "Emptying ${lake} (all versions)"
  aws s3 rm "s3://${lake}" --recursive --region "$AWS_REGION" >/dev/null 2>&1 || true
  "${REPO_ROOT}/scripts/empty_versioned_bucket.sh" "$lake"

  info "Destroying persistent layer"
  tf_init "$PERSISTENT_DIR" "$bucket"
  if ! tf_change "$PERSISTENT_DIR" "persistent layer" destroy; then
    die "Persistent layer NOT destroyed."
  fi

  warn "State bucket ${bucket} is intentionally left in place."
  warn "Delete it by hand if you truly want a blank account."
}

cmd_fmt() {
  terraform fmt -recursive "${REPO_ROOT}/infrastructure"
  if command -v black >/dev/null 2>&1; then
    black "${REPO_ROOT}/src" "${REPO_ROOT}/scripts" "${REPO_ROOT}/tests" 2>/dev/null || true
  else
    dim "black not installed; skipping python formatting"
  fi
}

# Print the header block, stopping at its closing rule rather than a fixed
# line number - the previous version needed editing every time it grew.
cmd_help() {
  awk 'NR>2 { if ($0 ~ /^# ----/) exit; sub(/^# ?/, ""); print }' "${BASH_SOURCE[0]}"
}

main() {
  # --yes is opt-in and never the default. It is stripped from the argument
  # list before dispatch so commands do not need to know about it.
  local -a argv=()
  local arg
  for arg in "$@"; do
    case "$arg" in
      --yes)       ASSUME_YES=1 ;;
      --plan-only) PLAN_ONLY=1 ;;
      *)           argv+=("$arg") ;;
    esac
  done
  export ASSUME_YES="${ASSUME_YES:-0}"
  export PLAN_ONLY="${PLAN_ONLY:-0}"
  set -- "${argv[@]+"${argv[@]}"}"

  local cmd="${1:-help}"; shift || true
  case "$cmd" in
    bootstrap) cmd_bootstrap "$@" ;;
    up)        cmd_up        "$@" ;;
    plan)      cmd_plan      "$@" ;;
    down)      cmd_down      "$@" ;;
    verify)    cmd_verify    "$@" ;;
    nuke)      cmd_nuke      "$@" ;;
    gen)       cmd_gen       "$@" ;;
    load)      cmd_load      "$@" ;;
    evidence)  cmd_evidence  "$@" ;;
    benchmark) cmd_benchmark "$@" ;;
    partexp)   cmd_partexp   "$@" ;;
    breakschema) cmd_breakschema "$@" ;;
    crawl)     cmd_crawl     "$@" ;;
    publish)   cmd_publish   "$@" ;;
    runjob)    cmd_runjob    "$@" ;;
    dq)        cmd_dq        "$@" ;;
    analytics) cmd_analytics "$@" ;;
    orphans)   cmd_orphans   "$@" ;;
    status)    cmd_status    "$@" ;;
    fmt)       cmd_fmt       "$@" ;;
    help|-h|--help) cmd_help ;;
    *) die "Unknown command: ${cmd}. Try: ./scripts/de.sh help" ;;
  esac
}

main "$@"
