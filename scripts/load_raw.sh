#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# load_raw.sh - upload the generated dataset into the lake.
#
#   ./scripts/de.sh load             CSV into raw/          (the default)
#   ./scripts/de.sh load benchmark   all three formats into benchmark/
#   ./scripts/de.sh load all         both of the above
#
# Two destinations, and the distinction matters:
#
#   raw/         CSV only. This is what the source system emits, so it is the
#                only thing that belongs in the raw layer. Putting three
#                encodings of the same data here would make "raw" meaningless
#                and would give the Phase 2 crawler three tables where the
#                business has one.
#
#   benchmark/   CSV, JSON and Parquet of identical data, for the Phase 1
#                format comparison. A measurement corpus, not source data.
#
# Raw is append-only, and now enforced rather than merely asserted: the Glue
# role has no delete verb for raw/ (iam.tf) and the bucket policy denies
# DeleteObject there for every principal except break-glass (lake.tf).
# `aws s3 sync` without --delete only ever adds, so re-running is safe.
# ---------------------------------------------------------------------------

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

TARGET="${1:-raw}"
LAKE="$(lake_bucket_name)"
SRC="${REPO_ROOT}/data/raw"

[ -d "$SRC" ] || die "No generated data found. Run ./scripts/de.sh gen first."
aws s3api head-bucket --bucket "$LAKE" >/dev/null 2>&1 \
  || die "Lake bucket ${LAKE} missing. Run bootstrap."

upload() {
  local local_dir="$1" prefix="$2"
  if [ ! -d "$local_dir" ]; then
    dim "skip ${prefix} (${local_dir} not generated)"
    return
  fi
  info "s3://${LAKE}/${prefix}"
  # No --delete: raw is append-only, and the bucket policy would refuse anyway.
  aws s3 sync "$local_dir" "s3://${LAKE}/${prefix}" --region "$AWS_REGION" \
    --only-show-errors --exact-timestamps
}

load_raw() {
  info "raw layer - CSV only"
  upload "${SRC}/csv/customers" "raw/customers"
  upload "${SRC}/csv/products"  "raw/products"
  upload "${SRC}/csv/orders"    "raw/orders"
}

load_benchmark() {
  info "benchmark corpus - csv, json, parquet"
  local fmt
  for fmt in csv json parquet; do
    upload "${SRC}/${fmt}/customers" "benchmark/${fmt}/customers"
    upload "${SRC}/${fmt}/products"  "benchmark/${fmt}/products"
    upload "${SRC}/${fmt}/orders"    "benchmark/${fmt}/orders"
  done
  # The unpartitioned control for the format comparison.
  upload "${SRC}/parquet_flat/orders" "benchmark/parquet_flat/orders"
}

case "$TARGET" in
  raw)       load_raw ;;
  benchmark) load_benchmark ;;
  all)       load_raw; load_benchmark ;;
  # Accepted for the old call style, but say what changed rather than
  # silently doing something different from what was asked.
  json|parquet)
    die "raw/ holds CSV only. Use 'load benchmark' to upload ${TARGET} for the
     format comparison, or 'load all' for both." ;;
  *) die "Unknown target: ${TARGET} (raw|benchmark|all)" ;;
esac

ok "lake loaded (${TARGET})"
aws s3 ls "s3://${LAKE}/" --recursive --summarize --region "$AWS_REGION" \
  | tail -2
