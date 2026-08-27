#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# load_raw.sh - upload the generated dataset into the raw layer.
#
# Raw is immutable by convention: this script only ever adds objects under
# date-partitioned prefixes, and never rewrites what is already there.
#
#   ./scripts/de.sh load             upload csv (default source format)
#   ./scripts/de.sh load json        upload json as well
#   ./scripts/de.sh load all         upload every generated format
# ---------------------------------------------------------------------------

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

FORMAT="${1:-csv}"
LAKE="$(lake_bucket_name)"
SRC="${REPO_ROOT}/data/raw"

[ -d "$SRC" ] || die "No generated data found. Run ./scripts/de.sh gen first."
aws s3api head-bucket --bucket "$LAKE" >/dev/null 2>&1 || die "Lake bucket ${LAKE} missing. Run bootstrap."

upload() {
  local local_dir="$1" prefix="$2"
  [ -d "$local_dir" ] || { dim "skip ${local_dir} (not generated)"; return; }
  info "s3://${LAKE}/${prefix}"
  aws s3 sync "$local_dir" "s3://${LAKE}/${prefix}" --region "$AWS_REGION" \
    --only-show-errors --exact-timestamps
}

case "$FORMAT" in
  csv|json|parquet)
    upload "${SRC}/${FORMAT}/customers" "raw/customers"
    upload "${SRC}/${FORMAT}/products"  "raw/products"
    upload "${SRC}/${FORMAT}/orders"    "raw/orders"
    ;;
  all)
    for f in csv json parquet; do "$0" "$f"; done
    ;;
  *) die "Unknown format: ${FORMAT} (csv|json|parquet|all)" ;;
esac

ok "raw layer loaded (${FORMAT})"
aws s3 ls "s3://${LAKE}/raw/" --recursive --summarize --region "$AWS_REGION" \
  | tail -2
