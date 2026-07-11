#!/usr/bin/env bash
# Enables free, native GitHub security features across repos:
#   - Dependabot vulnerability alerts — works on every repo, no plan gate
#   - secret scanning, secret scanning push protection, and Dependabot
#     security updates — public repos only; private repos need GitHub
#     Advanced Security, a paid add-on this account's plan doesn't include
#   - private vulnerability reporting — same public-repo-only gate
#
# Repos where a feature is unavailable are reported, not treated as a
# failure — same approach as set-branch-protection.sh's private-repo
# handling.
#
# Usage: set-security-features.sh [--dry-run] [--only name1,name2] [--skip name1,name2]
#
# --dry-run   Diff each repo's current feature state against "everything
#             available should be on" and print "up to date" or "would
#             enable: ...", without changing anything.
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

# Builds a {would_enable: [...], unavailable: [...]} summary from the
# current state of all five features.
summarize() {
  local repo_json="$1" vuln_alerts_enabled="$2" pvr_json="$3"
  jq -n \
    --argjson repo "$repo_json" \
    --argjson vuln_alerts "$vuln_alerts_enabled" \
    --argjson pvr "$pvr_json" '
    ($repo.security_and_analysis // {}) as $sec |
    {
      vuln_alerts: { current: $vuln_alerts, available: true },
      secret_scanning: { current: ($sec.secret_scanning.status == "enabled"), available: ($sec.secret_scanning != null) },
      push_protection: { current: ($sec.secret_scanning_push_protection.status == "enabled"), available: ($sec.secret_scanning_push_protection != null) },
      dependabot_updates: { current: ($sec.dependabot_security_updates.status == "enabled"), available: ($sec.dependabot_security_updates != null) },
      private_vuln_reporting: { current: ($pvr.enabled == true), available: ($pvr != null) }
    } as $features |
    {
      would_enable: ($features | to_entries | map(select(.value.available and (.value.current | not)) | .key)),
      unavailable: ($features | to_entries | map(select(.value.available | not) | .key))
    }
  '
}

features_unavailable=()

while IFS=$'\t' read -r name _default_branch _is_private _is_fork; do
  [[ -z "$name" ]] && continue

  repo_json=$(gh api "repos/$OWNER/$name")

  if gh api "repos/$OWNER/$name/vulnerability-alerts" >/dev/null 2>&1; then
    vuln_alerts_enabled=true
  else
    vuln_alerts_enabled=false
  fi

  if pvr_output=$(gh api "repos/$OWNER/$name/private-vulnerability-reporting" 2>/dev/null); then
    pvr_json="$pvr_output"
  else
    pvr_json='null'
  fi

  if "$DRY_RUN"; then
    summary=$(summarize "$repo_json" "$vuln_alerts_enabled" "$pvr_json")
    would_enable=$(jq -r '.would_enable | join(", ")' <<<"$summary")
    unavailable=$(jq -r '.unavailable | join(", ")' <<<"$summary")
    if [[ -z "$would_enable" ]]; then
      printf '%-30s up to date' "$name"
    else
      printf '%-30s would enable: %s' "$name" "$would_enable"
    fi
    [[ -n "$unavailable" ]] && printf ' (unavailable: %s)' "$unavailable"
    printf '\n'
    continue
  fi

  gh api -X PUT "repos/$OWNER/$name/vulnerability-alerts" >/dev/null

  repo_unavailable=()

  if error=$(gh api -X PATCH "repos/$OWNER/$name" \
    -f 'security_and_analysis[secret_scanning][status]=enabled' \
    -f 'security_and_analysis[secret_scanning_push_protection][status]=enabled' \
    -f 'security_and_analysis[dependabot_security_updates][status]=enabled' \
    2>&1 >/dev/null); then
    :
  elif [[ "$error" == *"not available for this repository"* ]]; then
    repo_unavailable+=("secret scanning")
  else
    echo "$error" >&2
    exit 1
  fi

  if error=$(gh api -X PUT "repos/$OWNER/$name/private-vulnerability-reporting" 2>&1 >/dev/null); then
    :
  elif [[ "$error" == *"Not Found"* ]]; then
    repo_unavailable+=("private vulnerability reporting")
  else
    echo "$error" >&2
    exit 1
  fi

  if [[ ${#repo_unavailable[@]} -eq 0 ]]; then
    printf '%-30s enabled\n' "$name"
  else
    printf '%-30s enabled (unavailable: %s)\n' "$name" "$(
      IFS=,
      echo "${repo_unavailable[*]}"
    )"
    features_unavailable+=("$name")
  fi
done < <(list_repos)

if "$DRY_RUN"; then
  exit 0
fi

echo
echo "Summary:"
echo "  Repos with unavailable features (private, needs GitHub Advanced Security): ${features_unavailable[*]:-none}"
