#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# run_glue_job.sh - start the Glue ETL job and wait for it to finish.
#
#   ./scripts/de.sh runjob
#
# Glue job runs are asynchronous. Without a wait loop the next command reads
# curated/ before the job has written anything and concludes it produced no
# output - the same trap the crawler wait loop exists to avoid.
#
# Billing is per DPU-second with a one-minute minimum. G.1X x 2 workers plus a
# driver is roughly 3 DPU, so a four-minute run costs about $0.09.
# ---------------------------------------------------------------------------

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

JOB="${1:-${PROJECT}-curated-sales}"

aws glue get-job --job-name "$JOB" --region "$AWS_REGION" >/dev/null 2>&1 \
  || die "Job ${JOB} not found. Is the training layer up?"

info "starting ${JOB}"
RUN_ID="$(aws glue start-job-run --job-name "$JOB" --region "$AWS_REGION" \
  --query JobRunId --output text)"
info "run ${RUN_ID}"

started="$(date +%s)"
while true; do
  state="$(aws glue get-job-run --job-name "$JOB" --run-id "$RUN_ID" \
    --region "$AWS_REGION" --query 'JobRun.JobRunState' --output text)"
  elapsed=$(( $(date +%s) - started ))
  printf '\r  %-12s %3ds' "$state" "$elapsed"
  case "$state" in
    SUCCEEDED|FAILED|STOPPED|TIMEOUT|ERROR) break ;;
  esac
  if [ "$elapsed" -gt 1800 ]; then printf '\n'; die "still ${state} after 30 minutes"; fi
  sleep 10
done
printf '\n'

aws glue get-job-run --job-name "$JOB" --run-id "$RUN_ID" --region "$AWS_REGION" \
  --query 'JobRun.[JobRunState,ExecutionTime,DPUSeconds,ErrorMessage]' --output text \
  | while read -r st secs dpu err; do
      printf '  state=%s  execution=%ss  dpu-seconds=%s\n' "$st" "$secs" "$dpu"
      if [ "$st" != "SUCCEEDED" ]; then warn "${err}"; fi
    done

# The reconciliation figures are logged by the job, not returned by the API.
dim "  reconciliation and rejection counts are in the job log:"
dim "  aws logs tail /aws-glue/jobs/output --log-stream-names ${RUN_ID}"

[ "$state" = "SUCCEEDED" ] || exit 1
