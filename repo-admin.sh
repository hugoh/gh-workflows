#!/usr/bin/env bash
# Thin dispatcher so repo_admin.py can be run as `./repo-admin.sh <command>`
# from the repo root, without `cd repo-admin` first.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec uv run --project "$SCRIPT_DIR/repo-admin" "$SCRIPT_DIR/repo-admin/repo_admin.py" "$@"
