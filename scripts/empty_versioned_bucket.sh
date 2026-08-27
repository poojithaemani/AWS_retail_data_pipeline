#!/usr/bin/env bash
# Delete every object version and delete-marker in a versioned bucket.
# `aws s3 rm --recursive` does not remove versions, so a versioned bucket
# looks empty and still refuses to be deleted. Used only by `de.sh nuke`.

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

BUCKET="${1:?usage: empty_versioned_bucket.sh <bucket>}"

aws s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1 || {
  dim "bucket ${BUCKET} does not exist; nothing to empty"; exit 0; }

for kind in Versions DeleteMarkers; do
  while :; do
    keys="$(aws s3api list-object-versions --bucket "$BUCKET" --max-keys 500 \
             --query "${kind}[].[Key,VersionId]" --output text 2>/dev/null || true)"
    if [ -z "$keys" ] || [ "$keys" = "None" ]; then break; fi

    printf '%s\n' "$keys" | while IFS=$'\t' read -r key vid; do
      [ -n "$key" ] || continue
      aws s3api delete-object --bucket "$BUCKET" --key "$key" --version-id "$vid" >/dev/null
    done
    dim "  removed a batch of ${kind} from ${BUCKET}"
  done
done

ok "emptied ${BUCKET}"
