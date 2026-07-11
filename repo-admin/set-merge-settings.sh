#!/usr/bin/env bash
# Enables auto-merge and delete-branch-on-merge across repos.
#
# Usage: set-merge-settings.sh [--dry-run] [--only name1,name2] [--skip name1,name2]
#
# --dry-run   Print current allow_auto_merge/delete_branch_on_merge per repo
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

while IFS=$'\t' read -r name _default_branch _is_private _is_fork; do
  [[ -z "$name" ]] && continue

  if "$DRY_RUN"; then
    current=$(gh api "repos/$OWNER/$name" -q '{allow_auto_merge, delete_branch_on_merge}')
    printf '%-30s %s\n' "$name" "$current"
    continue
  fi

  before=$(gh api "repos/$OWNER/$name" -q '{allow_auto_merge, delete_branch_on_merge}')
  gh repo edit "$OWNER/$name" --enable-auto-merge --delete-branch-on-merge >/dev/null
  after=$(gh api "repos/$OWNER/$name" -q '{allow_auto_merge, delete_branch_on_merge}')

  if [[ "$before" == "$after" ]]; then
    printf '%-30s unchanged %s\n' "$name" "$after"
  else
    printf '%-30s %s -> %s\n' "$name" "$before" "$after"
  fi
done < <(list_repos)
