#!/usr/bin/env bash
# Shared helpers for repo-admin/*.sh. Not meant to be run directly.

OWNER="${GH_OWNER:-hugoh}"

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Forked repos are excluded by default (they're usually just tracking
# upstream), except those listed in include-forks.txt — forks hugoh
# actually maintains and wants managed like any other repo. Override with
# GH_INCLUDE_FORKS (comma-separated) for one-off runs; edit the file to
# permanently add one.
if [[ -n "${GH_INCLUDE_FORKS:-}" ]]; then
  INCLUDE_FORKS="$GH_INCLUDE_FORKS"
else
  INCLUDE_FORKS=$(grep -v '^#' "$LIB_DIR/include-forks.txt" | grep -v '^[[:space:]]*$' | paste -sd, -)
fi

# Parses --only/--skip out of "$@". Sets ONLY_FILTER and SKIP_FILTER to
# comma-separated repo-name lists (empty string means "no filter"). Exits
# with an error on any unrecognized argument.
parse_filter_args() {
  ONLY_FILTER=""
  SKIP_FILTER=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --only)
        ONLY_FILTER="$2"
        shift 2
        ;;
      --skip)
        SKIP_FILTER="$2"
        shift 2
        ;;
      *)
        echo "unrecognized argument: $1" >&2
        exit 1
        ;;
    esac
  done
}

# Prints name/default-branch/is-private/is-fork TSV for every non-archived
# repo under $OWNER that is either not a fork or is listed in INCLUDE_FORKS,
# then applies ONLY_FILTER/SKIP_FILTER (set by parse_filter_args).
list_repos() {
  gh repo list "$OWNER" --limit 300 \
    --json name,isFork,isArchived,isPrivate,defaultBranchRef \
    -q '.[] | select(.isArchived==false) | [.name, .defaultBranchRef.name, .isPrivate, .isFork] | @tsv' \
    | awk -F'\t' -v OFS='\t' -v only="$ONLY_FILTER" -v skip="$SKIP_FILTER" -v include_forks="$INCLUDE_FORKS" '
      BEGIN {
        n = split(only, only_arr, ",")
        for (i = 1; i <= n; i++) only_set[only_arr[i]] = 1
        m = split(skip, skip_arr, ",")
        for (i = 1; i <= m; i++) skip_set[skip_arr[i]] = 1
        f = split(include_forks, fork_arr, ",")
        for (i = 1; i <= f; i++) fork_set[fork_arr[i]] = 1
      }
      {
        if ($4 == "true" && !($1 in fork_set)) next
        if (only != "" && !($1 in only_set)) next
        if (skip != "" && ($1 in skip_set)) next
        print $1, $2, $3, $4
      }
    '
}
