#!/usr/bin/env bash
# Shared helpers. Sourced by de.sh; not executed directly.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export REPO_ROOT

# shellcheck source=../config/project.env
source "${REPO_ROOT}/config/project.env"

# There is deliberately no second env file. The only value that must stay out
# of version control is the budget alert address, and that lives in
# infrastructure/persistent/terraform.tfvars - gitignored by the *.tfvars rule
# and auto-loaded by Terraform without any sourcing logic here.

PERSISTENT_DIR="${REPO_ROOT}/infrastructure/persistent"
TRAINING_DIR="${REPO_ROOT}/infrastructure/training"

# Terraform refuses to start if the plugin cache directory does not exist.
mkdir -p "$TF_PLUGIN_CACHE_DIR"

# --- output ---------------------------------------------------------------

if [ -t 1 ]; then
  C_RED=$'\033[31m'; C_GRN=$'\033[32m'; C_YEL=$'\033[33m'
  C_BLU=$'\033[34m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'
else
  C_RED=""; C_GRN=""; C_YEL=""; C_BLU=""; C_DIM=""; C_OFF=""
fi

info()  { printf '%s==>%s %s\n' "$C_BLU" "$C_OFF" "$*"; }
ok()    { printf '%s  ok%s %s\n' "$C_GRN" "$C_OFF" "$*"; }
warn()  { printf '%swarn%s %s\n' "$C_YEL" "$C_OFF" "$*" >&2; }
die()   { printf '%sfail%s %s\n' "$C_RED" "$C_OFF" "$*" >&2; exit 1; }
dim()   { printf '%s%s%s\n' "$C_DIM" "$*" "$C_OFF"; }

# --- python ---------------------------------------------------------------
# Windows installs the launcher as `py` and often has no bare `python`.
python_bin() {
  if command -v py       >/dev/null 2>&1; then echo "py -3"; return; fi
  if command -v python3  >/dev/null 2>&1; then echo "python3"; return; fi
  if command -v python   >/dev/null 2>&1; then echo "python"; return; fi
  die "No Python interpreter found (tried py, python3, python)."
}

# --- preflight ------------------------------------------------------------

require_tools() {
  for t in aws terraform git; do
    command -v "$t" >/dev/null 2>&1 || die "Required tool not on PATH: $t"
  done
}

account_id() {
  aws sts get-caller-identity --query Account --output text
}

state_bucket_name() { echo "${PROJECT}-tfstate-$(account_id)"; }
lake_bucket_name()  { echo "${PROJECT}-$(account_id)"; }

# --- approval gate -------------------------------------------------------
#
# Every operation that creates, changes or destroys AWS resources goes through
# plan -> show -> confirm -> apply. Nothing is applied that was not displayed
# first, and the saved plan file is what gets applied - so what is approved is
# exactly what runs, with no window for drift between the two.

# confirm <prompt>  - returns 0 to proceed, 1 to abort.
confirm() {
  local prompt="${1:-Proceed?}"

  if [ "${ASSUME_YES:-0}" = "1" ]; then
    dim "  --yes supplied, proceeding without prompting"
    return 0
  fi

  # No terminal (CI, or a tool-driven shell): refuse rather than assume.
  # The plan has already been printed, so the operator can review it and
  # re-run with --yes.
  if [ ! -t 0 ]; then
    warn "No interactive terminal; not applying."
    warn "Review the plan above, then re-run the same command with --yes."
    return 1
  fi

  printf '%s%s%s ' "$C_YEL" "$prompt" "$C_OFF"
  read -r reply
  [ "$reply" = "yes" ]
}

# tf_change <dir> <label> [destroy]
#
# Plans, prints a summary, asks, then applies the saved plan. Uses
# -detailed-exitcode so "nothing to do" is distinguishable from "changes
# pending" rather than being inferred from output text.
tf_change() {
  local dir="$1" label="$2" mode="${3:-apply}"
  local plan_file="${dir}/terraform.tfplan"
  local rc=0

  local -a plan_args=(-input=false -out="$plan_file" -detailed-exitcode)
  if [ "$mode" = "destroy" ]; then
    plan_args+=(-destroy)
  fi

  info "Planning ${label}"
  set +e
  terraform -chdir="$dir" plan "${plan_args[@]}"
  rc=$?
  set -e

  case "$rc" in
    0)
      ok "${label}: no changes required"
      rm -f "$plan_file"
      return 0
      ;;
    2) ;;  # changes pending - continue to review
    *)
      rm -f "$plan_file"
      die "${label}: plan failed (exit ${rc})"
      ;;
  esac

  echo
  if [ "$mode" = "destroy" ]; then
    warn "The following resources will be DESTROYED (${label}):"
  else
    info "The following changes will be applied (${label}):"
  fi
  terraform -chdir="$dir" show -no-color "$plan_file"     | grep -E "^  # |^Plan:|^Changes to Outputs:" || true
  echo

  # --plan-only stops here, before the approval prompt. This is what makes a
  # separate review round possible: --yes can approve a prerequisite earlier in
  # the same command without also blanket-approving this apply.
  if [ "${PLAN_ONLY:-0}" = "1" ]; then
    dim "  --plan-only: plan displayed above, nothing applied."
    rm -f "$plan_file"
    exit 0
  fi

  local verb="Apply"
  if [ "$mode" = "destroy" ]; then verb="DESTROY"; fi
  if ! confirm "${verb} these changes to ${label}? Type 'yes' to continue:"; then
    rm -f "$plan_file"
    warn "${label}: not applied."
    return 1
  fi

  terraform -chdir="$dir" apply -input=false "$plan_file"
  rc=$?
  rm -f "$plan_file"
  return $rc
}

# Terraform init against the shared backend.
tf_init() {
  local dir="$1" bucket="$2"
  terraform -chdir="$dir" init -reconfigure -input=false \
    -backend-config="bucket=${bucket}" \
    -backend-config="region=${AWS_REGION}" >/dev/null
}
