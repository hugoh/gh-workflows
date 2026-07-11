#!/usr/bin/env bash
# Enables auto-merge, delete-branch-on-merge, and PR-branch auto-update
# across repos. The latter matters because branch protection requires PR
# branches to be up to date with the base branch (`strict: true`) — without
# auto-update, auto-merge PRs get stuck needing a manual "Update branch"
# click whenever another PR merges first.
#
# Usage: set-merge-settings.sh [--dry-run] [--only name1,name2] [--skip name1,name2]
#
# --dry-run   Diff each repo's current allow_auto_merge/delete_branch_on_merge/
#             allow_update_branch against "all enabled" and print "up to
#             date" or "would enable: ...", without changing anything.
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

while IFS=$'\t' read -r name _default_branch _is_private _is_fork; do
  [[ -z "$name" ]] && continue

  if "$DRY_RUN"; then
    would_enable=$(gh api "repos/$OWNER/$name" -q '
      {allow_auto_merge, delete_branch_on_merge, allow_update_branch}
      | to_entries | map(select(.value | not) | .key) | join(", ")
    ')
    if [[ -z "$would_enable" ]]; then
      printf '%-30s up to date\n' "$name"
    else
      printf '%-30s would enable: %s\n' "$name" "$would_enable"
    fi
    continue
  fi

  before=$(gh api "repos/$OWNER/$name" -q '{allow_auto_merge, delete_branch_on_merge, allow_update_branch}')
  gh repo edit "$OWNER/$name" --enable-auto-merge --delete-branch-on-merge --allow-update-branch >/dev/null
  after=$(gh api "repos/$OWNER/$name" -q '{allow_auto_merge, delete_branch_on_merge, allow_update_branch}')

  if [[ "$before" == "$after" ]]; then
    printf '%-30s unchanged %s\n' "$name" "$after"
  else
    printf '%-30s %s -> %s\n' "$name" "$before" "$after"
  fi
done < <(list_repos)
