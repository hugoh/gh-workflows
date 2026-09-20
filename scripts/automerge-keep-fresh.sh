#!/usr/bin/env bash

set -euo pipefail

: "${GH_REPO:?GH_REPO must be set}"
: "${GH_TOKEN:?GH_TOKEN must be set}"

readonly max_attempts="${MAX_ATTEMPTS:-6}"
readonly retry_seconds="${RETRY_SECONDS:-10}"

if [[ -n "${PR_NUMBER:-}" ]]; then
  prs="$(
    gh pr view "$PR_NUMBER" --repo "$GH_REPO" \
      --json number,autoMergeRequest,mergeStateStatus \
      --jq 'select(.autoMergeRequest != null and .mergeStateStatus == "BEHIND") | .number'
  )"
else
  prs=""
  for ((attempt = 1; attempt <= max_attempts; attempt++)); do
    prs="$(
      gh pr list --repo "$GH_REPO" --state open --limit 100 \
        --json number,autoMergeRequest,mergeStateStatus \
        --jq '.[] | select(.autoMergeRequest != null and .mergeStateStatus == "BEHIND") | .number'
    )"
    [[ -n "$prs" ]] && break
    [[ "$attempt" -lt "$max_attempts" ]] && sleep "$retry_seconds"
  done
fi

while read -r pr; do
  [[ -z "$pr" ]] && continue
  echo "Updating PR #$pr (behind main, auto-merge enabled)"
  gh api "repos/$GH_REPO/pulls/$pr/update-branch" -X PUT
done <<<"$prs"
