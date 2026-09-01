#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# run_crawler.sh - start the Glue crawler and wait for it to finish.
#
#   ./scripts/de.sh crawl
#
# Crawlers are asynchronous. start-crawler returns immediately, so without a
# wait loop the next command reads the catalog before the crawler has written
# to it and sees the previous run's schema - which looks exactly like "the
# crawler did nothing".
#
# Billing is per DPU-second with a ten-minute minimum per run, so each
# invocation costs roughly $0.07 regardless of how little data it reads.
# ---------------------------------------------------------------------------

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

CRAWLER="${1:-${PROJECT}-raw-crawler}"

state() {
  aws glue get-crawler --name "$CRAWLER" --region "$AWS_REGION" \
    --query 'Crawler.State' --output text 2>/dev/null
}

current="$(state)"
[ -n "$current" ] || die "Crawler ${CRAWLER} not found. Is the training layer up?"

if [ "$current" != "READY" ]; then
  warn "crawler is ${current}; waiting for it to settle"
else
  info "starting ${CRAWLER}"
  aws glue start-crawler --name "$CRAWLER" --region "$AWS_REGION"
fi

started="$(date +%s)"
while true; do
  current="$(state)"
  elapsed=$(( $(date +%s) - started ))
  printf '\r  %s  %3ds' "$current" "$elapsed"
  if [ "$current" = "READY" ] && [ "$elapsed" -gt 5 ]; then
    break
  fi
  if [ "$elapsed" -gt 900 ]; then
    printf '\n'
    die "crawler still ${current} after 15 minutes"
  fi
  sleep 5
done
printf '\n'

aws glue get-crawler --name "$CRAWLER" --region "$AWS_REGION" \
  --query 'Crawler.LastCrawl.[Status,MessagePrefix,LogGroup]' --output text \
  | while read -r status _ _; do
      if [ "$status" = "SUCCEEDED" ]; then ok "crawl ${status}"; else warn "crawl ${status}"; fi
    done

info "tables in ${GLUE_DATABASE}"
aws glue get-tables --database-name "$GLUE_DATABASE" --region "$AWS_REGION" \
  --query 'TableList[].[Name,length(StorageDescriptor.Columns)]' --output text \
  | sed 's/^/  /'
