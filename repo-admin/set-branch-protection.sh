#!/usr/bin/env bash
# Applies a baseline branch-protection policy (required status checks,
# PR required with 0 approvals, enforce-for-admins, no force-push/deletion)
# to each repo's default branch. Matches the convention already established
# by go-tools' `mise run gh-repo-setup`: required_pull_request_reviews must
# be a non-null object (required_approving_review_count: 0 works) to force
# GitHub's "require a pull request before merging" — a null value doesn't
# reliably block direct pushes to the branch, only null-vs-object controls
# that, independent of required_status_checks.
#
# Required status check contexts are read from the check runs on the most
# recent pull request's head commit, not the default branch tip: a workflow
# skipped entirely by a path/branch filter never posts a check at all, and
# requiring that context as a merge gate would leave it stuck pending
# forever. (A job skipped via an `if:` condition inside a triggered workflow
# is fine to require — GitHub reports that as a passing "skipped" check.)
# Sampling an actual PR's check runs avoids picking up the former case.
#
# Private repos on a plan that doesn't expose branch protection return a 403
# ("Upgrade to GitHub Pro..."); those are collected and reported at the end
# rather than treated as a hard failure.
#
# Usage: set-branch-protection.sh [--dry-run] [--only name1,name2] [--skip name1,name2]
#
# --dry-run   Compare each repo's current branch protection against the
#             baseline policy and print "up to date" or "would update",
#             without changing anything.
#
# GH_OWNER env var overrides the default owner (hugoh).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./repo-admin/lib.sh
source "$SCRIPT_DIR/lib.sh"

DRY_RUN=false
args=()
for arg in "$@"; do
  if [[ "$arg" == "--dry-run" ]]; then
    DRY_RUN=true
  else
    args+=("$arg")
  fi
done

if [[ ${#args[@]} -gt 0 ]]; then
  parse_filter_args "${args[@]}"
else
  parse_filter_args
fi

skipped_no_plan=()
skipped_no_checks=()
applied=()

while IFS=$'\t' read -r name default_branch _is_private _is_fork; do
  [[ -z "$name" ]] && continue

  pr_head_sha=$(gh api -X GET "repos/$OWNER/$name/pulls" \
    -f state=all -f per_page=1 -f sort=updated -f direction=desc \
    -q '.[0].head.sha // empty' 2>/dev/null || true)

  if [[ -z "$pr_head_sha" ]]; then
    printf '%-30s no pull requests found, skipping (nothing to detect PR-gating checks from)\n' "$name"
    skipped_no_checks+=("$name")
    continue
  fi

  contexts_json=$(gh api "repos/$OWNER/$name/commits/$pr_head_sha/check-runs" \
    -q '[.check_runs[].name] | unique' 2>/dev/null || echo '[]')

  if [[ "$contexts_json" == "[]" ]]; then
    printf '%-30s no check runs found on latest PR commit %s, skipping\n' "$name" "$pr_head_sha"
    skipped_no_checks+=("$name")
    continue
  fi

  if "$DRY_RUN"; then
    if current_output=$(gh api "repos/$OWNER/$name/branches/$default_branch/protection" 2>&1); then
      current_json="$current_output"
    elif [[ "$current_output" == *"Upgrade to GitHub Pro"* ]]; then
      printf '%-30s cannot check: private repo, plan does not allow branch protection\n' "$name"
      continue
    elif [[ "$current_output" == *"Branch not protected"* ]]; then
      current_json='null'
    else
      echo "$current_output" >&2
      exit 1
    fi

    up_to_date=$(jq -n --argjson current "$current_json" --argjson contexts "$contexts_json" '
      ($contexts | sort) as $want |
      ($current // {}) as $c |
      (($c.required_status_checks.contexts // []) | sort) == $want
        and (($c.required_status_checks.strict) == true)
        and (($c.enforce_admins.enabled) == true)
        and (($c.allow_force_pushes.enabled) == false)
        and (($c.allow_deletions.enabled) == false)
        and (($c.required_pull_request_reviews.required_approving_review_count) == 0)
    ')

    if [[ "$up_to_date" == "true" ]]; then
      printf '%-30s up to date (%s)\n' "$name" "$(jq -r '. | join(", ")' <<<"$contexts_json")"
    else
      printf '%-30s would update -> require: %s\n' "$name" "$(jq -r '. | join(", ")' <<<"$contexts_json")"
    fi
    continue
  fi

  payload=$(jq -n --argjson contexts "$contexts_json" '{
    required_status_checks: { strict: true, contexts: $contexts },
    enforce_admins: true,
    required_pull_request_reviews: { required_approving_review_count: 0 },
    restrictions: null,
    allow_force_pushes: false,
    allow_deletions: false
  }')

  if error=$(gh api -X PUT "repos/$OWNER/$name/branches/$default_branch/protection" \
    --input - <<<"$payload" 2>&1 >/dev/null); then
    printf '%-30s protected (%s)\n' "$name" "$(jq -r '. | join(", ")' <<<"$contexts_json")"
    applied+=("$name")
  elif [[ "$error" == *"Upgrade to GitHub Pro"* ]]; then
    printf '%-30s skipped: private repo, plan does not allow branch protection\n' "$name"
    skipped_no_plan+=("$name")
  else
    echo "$error" >&2
    exit 1
  fi
done < <(list_repos)

if "$DRY_RUN"; then
  exit 0
fi

echo
echo "Summary:"
echo "  Protected: ${#applied[@]}"
echo "  Skipped (no PRs / no check runs yet): ${skipped_no_checks[*]:-none}"
echo "  Skipped (plan doesn't allow branch protection on private repos): ${skipped_no_plan[*]:-none}"
