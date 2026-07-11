#!/usr/bin/env bash
# Lists non-archived repos for an account as an aligned table:
# NAME, DEFAULT BRANCH, PRIVATE, FORK.
#
# Usage: list-repos.sh [--only name1,name2] [--skip name1,name2]
#
# GH_OWNER env var overrides the default owner (hugoh).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./repo-admin/lib.sh
source "$SCRIPT_DIR/lib.sh"

parse_filter_args "$@"

{
  printf 'NAME\tDEFAULT BRANCH\tPRIVATE\tFORK\n'
  list_repos
} | column -t -s $'\t'
