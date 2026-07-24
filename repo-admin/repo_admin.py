"""Bulk-applies account-wide GitHub repo settings across hugoh's non-archived
repos, via the GitHub REST API (authenticated through `gh auth token`).

Usage: repo_admin.py <command> [--dry-run] [--only name1,name2] [--skip name1,name2]

Commands:
  list                list repos as a table: name, default branch, private, fork
  merge-settings      enable auto-merge, delete-branch-on-merge, and PR-branch
                       auto-update
  branch-protection   apply a baseline branch-protection policy to each repo's
                       default branch
  security-features   enable free, native GitHub security features
  all                 run merge-settings, branch-protection, and
                       security-features in sequence

Forks are excluded by default -- except those listed in include-forks.txt;
edit that file to add more, or override per-run with GH_INCLUDE_FORKS
(comma-separated). GH_OWNER overrides the default owner (hugoh); GH_JOBS
controls parallelism (default 6). Run a mutating command with --dry-run
first and review the output.
"""

from __future__ import annotations

import argparse
import enum
import sys

from lib import (
    DEFAULT_OWNER,
    GhError,
    Repo,
    RepoResult,
    Status,
    api_json,
    api_request,
    as_set,
    classify_status,
    error_message,
    list_repos,
    result_line,
    run_parallel,
)


class Tag(enum.StrEnum):
    """Per-command bookkeeping for end-of-run summaries -- distinct from
    Status, which is the shared display concept in lib.py.
    """

    APPLIED = "applied"
    SKIPPED_NO_CHECKS = "skipped_no_checks"
    SKIPPED_NO_PLAN = "skipped_no_plan"
    UNAVAILABLE = "unavailable"


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def cmd_list(args: argparse.Namespace) -> int:
    repos = list_repos(DEFAULT_OWNER, only=as_set(args.only), skip=as_set(args.skip))
    header = ("NAME", "DEFAULT BRANCH", "PRIVATE", "FORK")
    rows = [header] + [
        (r.name, r.default_branch, str(r.is_private).lower(), str(r.is_fork).lower())
        for r in repos
    ]
    widths = [max(len(row[i]) for row in rows) for i in range(len(header))]
    for row in rows:
        print("  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip())
    return 0


# ---------------------------------------------------------------------------
# merge-settings
#
# Enables auto-merge, delete-branch-on-merge, and PR-branch auto-update. The
# last one matters because branch protection requires PR branches to be up
# to date with the base branch (`strict: true`) -- without auto-update,
# auto-merge PRs get stuck needing a manual "Update branch" click whenever
# another PR merges first.
# ---------------------------------------------------------------------------

MERGE_SETTINGS_FIELDS = [
    "allow_auto_merge",
    "delete_branch_on_merge",
    "allow_update_branch",
]


def _merge_settings(owner: str, name: str) -> dict:
    data = api_json("GET", f"/repos/{owner}/{name}")
    return {field: data[field] for field in MERGE_SETTINGS_FIELDS}


def merge_settings_at_target(settings: dict) -> bool:
    return all(settings[field] for field in MERGE_SETTINGS_FIELDS)


def merge_settings_dry_run_line(name: str, current: dict, status: Status) -> str:
    would_enable = [field for field in MERGE_SETTINGS_FIELDS if not current[field]]
    detail = (
        str(current) if not would_enable else f"would enable: {', '.join(would_enable)}"
    )
    return result_line(name, detail, status)


def merge_settings_apply_line(
    name: str, before: dict, after: dict, status: Status
) -> str:
    detail = str(after) if before == after else f"{before} -> {after}"
    return result_line(name, detail, status)


def make_merge_settings_worker(owner: str, dry_run: bool):
    def worker(repo: Repo) -> RepoResult:
        if dry_run:
            current = _merge_settings(owner, repo.name)
            status = (
                Status.UNCHANGED if merge_settings_at_target(current) else Status.OK
            )
            return RepoResult(
                repo, merge_settings_dry_run_line(repo.name, current, status), status
            )

        before = _merge_settings(owner, repo.name)
        api_json(
            "PATCH",
            f"/repos/{owner}/{repo.name}",
            json={field: True for field in MERGE_SETTINGS_FIELDS},
        )
        after = _merge_settings(owner, repo.name)
        status = classify_status(
            at_target=merge_settings_at_target(after), changed=before != after
        )
        return RepoResult(
            repo, merge_settings_apply_line(repo.name, before, after, status), status
        )

    return worker


def cmd_merge_settings(args: argparse.Namespace) -> int:
    repos = list_repos(DEFAULT_OWNER, only=as_set(args.only), skip=as_set(args.skip))
    run_parallel(repos, make_merge_settings_worker(DEFAULT_OWNER, args.dry_run))
    return 0


# ---------------------------------------------------------------------------
# security-features
#
# Enables free, native GitHub security features:
#   - Dependabot vulnerability alerts -- works on every repo, no plan gate
#   - secret scanning, secret scanning push protection, and Dependabot
#     security updates -- public repos only; private repos need GitHub
#     Advanced Security, a paid add-on this account's plan doesn't include
#   - private vulnerability reporting -- same public-repo-only gate
#
# Repos where a feature is unavailable are reported, not treated as a
# failure -- same approach as branch-protection's private-repo handling.
# ---------------------------------------------------------------------------


def security_summarize(
    repo_json: dict, *, vuln_alerts_enabled: bool, pvr_json: dict | None
) -> dict:
    sec = repo_json.get("security_and_analysis") or {}
    features = {
        "vuln_alerts": (vuln_alerts_enabled, True),
        "secret_scanning": (
            (sec.get("secret_scanning") or {}).get("status") == "enabled",
            sec.get("secret_scanning") is not None,
        ),
        "push_protection": (
            (sec.get("secret_scanning_push_protection") or {}).get("status")
            == "enabled",
            sec.get("secret_scanning_push_protection") is not None,
        ),
        "dependabot_updates": (
            (sec.get("dependabot_security_updates") or {}).get("status") == "enabled",
            sec.get("dependabot_security_updates") is not None,
        ),
        "private_vuln_reporting": (
            (pvr_json or {}).get("enabled") is True,
            pvr_json is not None,
        ),
    }
    return {
        "would_enable": [
            key
            for key, (current, available) in features.items()
            if available and not current
        ],
        "unavailable": [
            key for key, (_current, available) in features.items() if not available
        ],
    }


def security_dry_run_line(name: str, summary: dict, status: Status) -> str:
    would_enable, unavailable = summary["would_enable"], summary["unavailable"]
    detail = (
        "enabled" if not would_enable else f"would enable: {', '.join(would_enable)}"
    )
    if unavailable:
        detail += f" (unavailable: {', '.join(unavailable)})"
    return result_line(name, detail, status)


def _fetch_security_state(owner: str, name: str) -> tuple[dict, bool, dict | None]:
    repo_json = api_json("GET", f"/repos/{owner}/{name}")

    # 204 = enabled, 404 = disabled -- GitHub's documented shape for this
    # endpoint, not an error either way.
    vuln_response = api_request("GET", f"/repos/{owner}/{name}/vulnerability-alerts")
    if vuln_response.status_code not in (204, 404):
        raise GhError(
            error_message(vuln_response), status_code=vuln_response.status_code
        )
    vuln_alerts_enabled = vuln_response.status_code == 204

    # 200 = available (body has "enabled"), 404 = not available on this plan.
    pvr_response = api_request(
        "GET", f"/repos/{owner}/{name}/private-vulnerability-reporting"
    )
    if pvr_response.status_code not in (200, 404):
        raise GhError(error_message(pvr_response), status_code=pvr_response.status_code)
    pvr_json = pvr_response.json() if pvr_response.status_code == 200 else None

    return repo_json, vuln_alerts_enabled, pvr_json


def make_security_features_worker(owner: str, dry_run: bool):
    def worker(repo: Repo) -> RepoResult:
        repo_json, vuln_alerts_enabled, pvr_json = _fetch_security_state(
            owner, repo.name
        )
        before_summary = security_summarize(
            repo_json, vuln_alerts_enabled=vuln_alerts_enabled, pvr_json=pvr_json
        )

        if dry_run:
            status = classify_status(
                at_target=not before_summary["unavailable"],
                changed=bool(before_summary["would_enable"]),
            )
            return RepoResult(
                repo, security_dry_run_line(repo.name, before_summary, status), status
            )

        api_json("PUT", f"/repos/{owner}/{repo.name}/vulnerability-alerts")

        unavailable = []

        # 422 = one or more of these fields isn't available on this plan
        # (GitHub Advanced Security is required for private repos).
        security_response = api_request(
            "PATCH",
            f"/repos/{owner}/{repo.name}",
            json={
                "security_and_analysis": {
                    "secret_scanning": {"status": "enabled"},
                    "secret_scanning_push_protection": {"status": "enabled"},
                    "dependabot_security_updates": {"status": "enabled"},
                }
            },
        )
        if security_response.status_code == 422:
            unavailable.append("secret scanning")
        elif not security_response.ok:
            raise GhError(
                error_message(security_response),
                status_code=security_response.status_code,
            )

        # 404 = not available on this plan.
        pvr_response = api_request(
            "PUT", f"/repos/{owner}/{repo.name}/private-vulnerability-reporting"
        )
        if pvr_response.status_code == 404:
            unavailable.append("private vulnerability reporting")
        elif not pvr_response.ok:
            raise GhError(
                error_message(pvr_response), status_code=pvr_response.status_code
            )

        status = classify_status(
            at_target=not unavailable, changed=bool(before_summary["would_enable"])
        )
        detail = "enabled"
        if unavailable:
            detail += f" (unavailable: {', '.join(unavailable)})"
            return RepoResult(
                repo,
                result_line(repo.name, detail, status),
                status,
                tag=Tag.UNAVAILABLE,
            )
        return RepoResult(repo, result_line(repo.name, detail, status), status)

    return worker


def cmd_security_features(args: argparse.Namespace) -> int:
    repos = list_repos(DEFAULT_OWNER, only=as_set(args.only), skip=as_set(args.skip))
    results = run_parallel(
        repos, make_security_features_worker(DEFAULT_OWNER, args.dry_run)
    )

    if args.dry_run:
        return 0

    unavailable = sorted(r.repo.name for r in results if r.tag == Tag.UNAVAILABLE)
    print()
    print("Summary:")
    print(
        f"  Repos with unavailable features (private, needs GitHub Advanced Security): {' '.join(unavailable) or 'none'}"
    )
    return 0


# ---------------------------------------------------------------------------
# branch-protection
#
# Applies a baseline branch-protection policy (required status checks, PR
# required with 0 approvals, enforce-for-admins, no force-push/deletion) to
# each repo's default branch. Matches the convention already established by
# go-tools' `mise run gh-repo-setup`: required_pull_request_reviews must be
# a non-null object (required_approving_review_count: 0 works) to force
# GitHub's "require a pull request before merging" -- a null value doesn't
# reliably block direct pushes to the branch, only null-vs-object controls
# that, independent of required_status_checks.
#
# Required status check contexts are read from the check runs on the most
# recent pull request's head commit, not the default branch tip: a workflow
# skipped entirely by a path/branch filter never posts a check at all, and
# requiring that context as a merge gate would leave it stuck pending
# forever. (A job skipped via an `if:` condition inside a triggered workflow
# is fine to require -- GitHub reports that as a passing "skipped" check.)
# Sampling an actual PR's check runs avoids picking up the former case.
#
# Private repos on a plan that doesn't expose branch protection return a 403
# ("Upgrade to GitHub Pro..."); those are collected and reported at the end
# rather than treated as a hard failure.
# ---------------------------------------------------------------------------


def branch_protection_payload(contexts: list[str]) -> dict:
    return {
        "required_status_checks": {"strict": True, "contexts": contexts},
        "enforce_admins": True,
        "required_pull_request_reviews": {"required_approving_review_count": 0},
        "restrictions": None,
        "allow_force_pushes": False,
        "allow_deletions": False,
    }


def branch_protection_up_to_date(current: dict | None, contexts: list[str]) -> bool:
    current = current or {}
    required_status_checks = current.get("required_status_checks") or {}
    return (
        sorted(required_status_checks.get("contexts") or []) == sorted(contexts)
        and required_status_checks.get("strict") is True
        and (current.get("enforce_admins") or {}).get("enabled") is True
        and (current.get("allow_force_pushes") or {}).get("enabled") is False
        and (current.get("allow_deletions") or {}).get("enabled") is False
        and (current.get("required_pull_request_reviews") or {}).get(
            "required_approving_review_count"
        )
        == 0
    )


def _latest_pr_head_sha(owner: str, name: str) -> str | None:
    pulls = api_json(
        "GET",
        f"/repos/{owner}/{name}/pulls",
        params={
            "state": "all",
            "per_page": "1",
            "sort": "updated",
            "direction": "desc",
        },
    )
    return pulls[0]["head"]["sha"] if pulls else None


def _check_run_contexts(owner: str, name: str, sha: str) -> list[str]:
    data = api_json("GET", f"/repos/{owner}/{name}/commits/{sha}/check-runs")
    return sorted({run["name"] for run in data["check_runs"]})


def _skip_result(repo: Repo, detail: str, tag: Tag) -> RepoResult:
    status = Status.LIMITED_UNCHANGED
    return RepoResult(repo, result_line(repo.name, detail, status), status, tag=tag)


def _plan_gated_result(repo: Repo, *, tag: Tag | None = None) -> RepoResult:
    status = Status.LIMITED_UNCHANGED
    detail = "private repo, plan does not allow branch protection"
    return RepoResult(repo, result_line(repo.name, detail, status), status, tag=tag)


def make_branch_protection_worker(owner: str, dry_run: bool):
    def worker(repo: Repo) -> RepoResult:
        pr_head_sha = _latest_pr_head_sha(owner, repo.name)
        if pr_head_sha is None:
            return _skip_result(
                repo,
                "no pull requests found, skipping (nothing to detect PR-gating checks from)",
                Tag.SKIPPED_NO_CHECKS,
            )

        contexts = _check_run_contexts(owner, repo.name, pr_head_sha)
        if not contexts:
            return _skip_result(
                repo,
                f"no check runs found on latest PR commit {pr_head_sha}, skipping",
                Tag.SKIPPED_NO_CHECKS,
            )

        if dry_run:
            protection_response = api_request(
                "GET",
                f"/repos/{owner}/{repo.name}/branches/{repo.default_branch}/protection",
            )
            if protection_response.status_code == 403:
                return _plan_gated_result(repo)
            if protection_response.status_code == 404:
                current = None
            elif protection_response.ok:
                current = protection_response.json()
            else:
                raise GhError(
                    error_message(protection_response),
                    status_code=protection_response.status_code,
                )

            if branch_protection_up_to_date(current, contexts):
                status = Status.UNCHANGED
                detail = ", ".join(contexts)
                return RepoResult(repo, result_line(repo.name, detail, status), status)
            status = Status.OK
            detail = f"would update -> require: {', '.join(contexts)}"
            return RepoResult(repo, result_line(repo.name, detail, status), status)

        payload = branch_protection_payload(contexts)
        put_response = api_request(
            "PUT",
            f"/repos/{owner}/{repo.name}/branches/{repo.default_branch}/protection",
            json=payload,
        )
        if put_response.status_code == 403:
            return _plan_gated_result(repo, tag=Tag.SKIPPED_NO_PLAN)
        if not put_response.ok:
            raise GhError(
                error_message(put_response), status_code=put_response.status_code
            )

        status = Status.OK
        detail = f"protected ({', '.join(contexts)})"
        return RepoResult(
            repo, result_line(repo.name, detail, status), status, tag=Tag.APPLIED
        )

    return worker


def cmd_branch_protection(args: argparse.Namespace) -> int:
    repos = list_repos(DEFAULT_OWNER, only=as_set(args.only), skip=as_set(args.skip))
    results = run_parallel(
        repos, make_branch_protection_worker(DEFAULT_OWNER, args.dry_run)
    )

    if args.dry_run:
        return 0

    applied = [r for r in results if r.tag == Tag.APPLIED]
    skipped_no_checks = sorted(
        r.repo.name for r in results if r.tag == Tag.SKIPPED_NO_CHECKS
    )
    skipped_no_plan = sorted(
        r.repo.name for r in results if r.tag == Tag.SKIPPED_NO_PLAN
    )
    print()
    print("Summary:")
    print(f"  Protected: {len(applied)}")
    print(
        f"  Skipped (no PRs / no check runs yet): {' '.join(skipped_no_checks) or 'none'}"
    )
    print(
        "  Skipped (plan doesn't allow branch protection on private repos): "
        f"{' '.join(skipped_no_plan) or 'none'}"
    )
    return 0


# ---------------------------------------------------------------------------
# all
# ---------------------------------------------------------------------------


def cmd_all(args: argparse.Namespace) -> int:
    """Runs merge-settings, branch-protection, then security-features in
    that order (matching the README's ordering -- merge-settings' PR-branch
    auto-update makes branch-protection's auto-merge-friendly baseline
    behave as intended). One command failing doesn't stop the others; the
    exit code is nonzero if any of them failed.
    """
    failed = False
    for name, cmd in (
        ("merge-settings", cmd_merge_settings),
        ("branch-protection", cmd_branch_protection),
        ("security-features", cmd_security_features),
    ):
        print(f"== {name} ==")
        try:
            if cmd(args) != 0:
                failed = True
        except GhError as exc:
            print(exc, file=sys.stderr)
            failed = True
    return 1 if failed else 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    filter_parent = argparse.ArgumentParser(add_help=False)
    filter_parent.add_argument("--only", help="comma-separated repo names to include")
    filter_parent.add_argument("--skip", help="comma-separated repo names to exclude")

    mutating_parent = argparse.ArgumentParser(add_help=False, parents=[filter_parent])
    mutating_parent.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would change, without changing anything",
    )

    subparsers.add_parser("list", parents=[filter_parent]).set_defaults(func=cmd_list)
    subparsers.add_parser("merge-settings", parents=[mutating_parent]).set_defaults(
        func=cmd_merge_settings
    )
    subparsers.add_parser("branch-protection", parents=[mutating_parent]).set_defaults(
        func=cmd_branch_protection
    )
    subparsers.add_parser("security-features", parents=[mutating_parent]).set_defaults(
        func=cmd_security_features
    )
    subparsers.add_parser("all", parents=[mutating_parent]).set_defaults(func=cmd_all)

    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except GhError as exc:
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
