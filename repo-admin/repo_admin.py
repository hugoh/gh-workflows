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
  pages-domain        set each repo's GitHub Pages custom domain, from the
                       mapping in pages-domains.yaml
  pages-status        list repos with GitHub Pages enabled and their custom
                       domain, flagging ones missing from pages-domains.yaml
  pages-domain-config print pages-domains.yaml entries to stdout for a base
                       domain (--domain), for --only repos or, if omitted,
                       repos pages-status would flag as unmapped

Forks are excluded by default -- except those listed in include-forks.txt;
edit that file to add more, or override per-run with GH_INCLUDE_FORKS
(comma-separated). GH_OWNER overrides the default owner (hugoh); GH_JOBS
controls parallelism (default 6). Run a mutating command with --dry-run
first and review the output.
"""

from __future__ import annotations

import argparse
import asyncio
import enum
import sys

import lib
from lib import (
    DEFAULT_JOBS,
    DEFAULT_OWNER,
    GhError,
    Repo,
    RepoResult,
    Status,
    api_json,
    api_request,
    as_set,
    classify_status,
    default_branch_protection_exclude,
    error_message,
    list_repos,
    result_line,
    run_parallel,
)
from rich.table import Table


class Tag(enum.StrEnum):
    """Per-command bookkeeping for end-of-run summaries -- distinct from
    Status, which is the shared display concept in lib.py.
    """

    APPLIED = "applied"
    APPLIED_NO_CHECKS = "applied_no_checks"
    SKIPPED_NO_PLAN = "skipped_no_plan"
    UNAVAILABLE = "unavailable"


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


async def cmd_list(args: argparse.Namespace) -> int:
    with lib.progress_bar(transient=True) as progress:
        progress.add_task("Fetching repos...", total=None)
        repos = await list_repos(
            DEFAULT_OWNER, only=as_set(args.only), skip=as_set(args.skip)
        )

    table = Table()
    table.add_column("NAME")
    table.add_column("DEFAULT BRANCH")
    table.add_column("PRIVATE")
    table.add_column("FORK")
    for r in repos:
        table.add_row(
            r.name, r.default_branch, str(r.is_private).lower(), str(r.is_fork).lower()
        )
    lib.console.print(table)
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


async def _merge_settings(owner: str, name: str) -> dict:
    data = await api_json("GET", f"/repos/{owner}/{name}")
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
    async def worker(repo: Repo) -> RepoResult:
        if dry_run:
            current = await _merge_settings(owner, repo.name)
            status = (
                Status.UNCHANGED if merge_settings_at_target(current) else Status.OK
            )
            return RepoResult(
                repo, merge_settings_dry_run_line(repo.name, current, status), status
            )

        before = await _merge_settings(owner, repo.name)
        await api_json(
            "PATCH",
            f"/repos/{owner}/{repo.name}",
            json={field: True for field in MERGE_SETTINGS_FIELDS},
        )
        after = await _merge_settings(owner, repo.name)
        status = classify_status(
            at_target=merge_settings_at_target(after), changed=before != after
        )
        return RepoResult(
            repo, merge_settings_apply_line(repo.name, before, after, status), status
        )

    return worker


async def cmd_merge_settings(args: argparse.Namespace) -> int:
    repos = await list_repos(
        DEFAULT_OWNER, only=as_set(args.only), skip=as_set(args.skip)
    )
    await run_parallel(repos, make_merge_settings_worker(DEFAULT_OWNER, args.dry_run))
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


async def _fetch_security_state(
    owner: str, name: str
) -> tuple[dict, bool, dict | None]:
    repo_json = await api_json("GET", f"/repos/{owner}/{name}")

    # 204 = enabled, 404 = disabled -- GitHub's documented shape for this
    # endpoint, not an error either way.
    vuln_response = await api_request(
        "GET", f"/repos/{owner}/{name}/vulnerability-alerts"
    )
    if vuln_response.status_code not in (204, 404):
        raise GhError(
            error_message(vuln_response), status_code=vuln_response.status_code
        )
    vuln_alerts_enabled = vuln_response.status_code == 204

    # 200 = available (body has "enabled"), 404 = not available on this plan.
    pvr_response = await api_request(
        "GET", f"/repos/{owner}/{name}/private-vulnerability-reporting"
    )
    if pvr_response.status_code not in (200, 404):
        raise GhError(error_message(pvr_response), status_code=pvr_response.status_code)
    pvr_json = pvr_response.json() if pvr_response.status_code == 200 else None

    return repo_json, vuln_alerts_enabled, pvr_json


def make_security_features_worker(owner: str, dry_run: bool):
    async def worker(repo: Repo) -> RepoResult:
        repo_json, vuln_alerts_enabled, pvr_json = await _fetch_security_state(
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

        await api_json("PUT", f"/repos/{owner}/{repo.name}/vulnerability-alerts")

        unavailable = []

        # 422 = one or more of these fields isn't available on this plan
        # (GitHub Advanced Security is required for private repos).
        security_response = await api_request(
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
        elif not security_response.is_success:
            raise GhError(
                error_message(security_response),
                status_code=security_response.status_code,
            )

        # 404 = not available on this plan.
        pvr_response = await api_request(
            "PUT", f"/repos/{owner}/{repo.name}/private-vulnerability-reporting"
        )
        if pvr_response.status_code == 404:
            unavailable.append("private vulnerability reporting")
        elif not pvr_response.is_success:
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


async def cmd_security_features(args: argparse.Namespace) -> int:
    repos = await list_repos(
        DEFAULT_OWNER, only=as_set(args.only), skip=as_set(args.skip)
    )
    results = await run_parallel(
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
# Only check runs reported by the "github-actions" app are considered:
# third-party apps (DeepSource, Codecov, etc.) aren't defined by the repo
# itself, can be reconfigured or removed outside of this tool's control, and
# shouldn't be able to block merges as a side effect of having run once on a
# PR.
#
# Private repos on a plan that doesn't expose branch protection return a 403
# ("Upgrade to GitHub Pro..."); those are collected and reported at the end
# rather than treated as a hard failure.
# ---------------------------------------------------------------------------


def branch_protection_payload(contexts: list[str]) -> dict:
    return {
        "required_status_checks": (
            {"strict": True, "contexts": contexts} if contexts else None
        ),
        "enforce_admins": True,
        "required_pull_request_reviews": {"required_approving_review_count": 0},
        "restrictions": None,
        "allow_force_pushes": False,
        "allow_deletions": False,
    }


def branch_protection_up_to_date(current: dict | None, contexts: list[str]) -> bool:
    current = current or {}
    required_status_checks = current.get("required_status_checks") or {}
    current_contexts = required_status_checks.get("contexts") or []
    if contexts:
        checks_ok = (
            sorted(current_contexts) == sorted(contexts)
            and required_status_checks.get("strict") is True
        )
    else:
        # No known checks to require yet (no PRs, or no check runs on the
        # latest one) -- the baseline (no force-push/deletion, PR required,
        # admins enforced) still applies without gating on specific contexts.
        checks_ok = not current_contexts
    return (
        checks_ok
        and (current.get("enforce_admins") or {}).get("enabled") is True
        and (current.get("allow_force_pushes") or {}).get("enabled") is False
        and (current.get("allow_deletions") or {}).get("enabled") is False
        and (current.get("required_pull_request_reviews") or {}).get(
            "required_approving_review_count"
        )
        == 0
    )


_PR_SAMPLE_SIZE = "5"


async def _recent_pr_head_shas(owner: str, name: str) -> list[str]:
    pulls = await api_json(
        "GET",
        f"/repos/{owner}/{name}/pulls",
        params={
            "state": "all",
            "per_page": _PR_SAMPLE_SIZE,
            "sort": "updated",
            "direction": "desc",
        },
    )
    return [pull["head"]["sha"] for pull in pulls]


async def _github_actions_check_runs(owner: str, name: str, sha: str) -> list[dict]:
    data = await api_json("GET", f"/repos/{owner}/{name}/commits/{sha}/check-runs")
    return [
        run
        for run in data["check_runs"]
        if (run.get("app") or {}).get("slug") == "github-actions"
    ]


async def _check_run_contexts(owner: str, name: str, shas: list[str]) -> list[str]:
    """Contexts to require, sampled from the most recent PR's head commit.

    A job that calls a reusable workflow via `uses:` alongside a `needs:`
    dependency reports under two different names depending on outcome: its
    own job id (e.g. "release") when a failed dependency causes it to skip
    before ever invoking the reusable workflow, or a composite name (e.g.
    "release / release") once it actually runs. Landing on a PR where that
    dependency failed would require the skip-only name, which then never
    posts again once the dependency passes -- leaving future PRs stuck
    pending forever. So a bare, skipped name is replaced with a composite
    alias ("<name> / ...") if one shows up, actually run, anywhere in a
    short recent-PR window.
    """
    latest_runs = await _github_actions_check_runs(owner, name, shas[0])
    contexts = {run["name"] for run in latest_runs}
    suspect = {
        run["name"]
        for run in latest_runs
        if run["conclusion"] == "skipped" and " / " not in run["name"]
    }
    if not suspect:
        return sorted(contexts)

    for sha in shas[1:]:
        if not suspect:
            break
        for run in await _github_actions_check_runs(owner, name, sha):
            if run["conclusion"] == "skipped":
                continue
            base = run["name"].split(" / ", 1)[0]
            if base in suspect:
                contexts.discard(base)
                contexts.add(run["name"])
                suspect.discard(base)

    return sorted(contexts)


def _plan_gated_result(repo: Repo, *, tag: Tag | None = None) -> RepoResult:
    status = Status.LIMITED_UNCHANGED
    detail = "private repo, plan does not allow branch protection"
    return RepoResult(repo, result_line(repo.name, detail, status), status, tag=tag)


def make_branch_protection_worker(owner: str, dry_run: bool):
    async def worker(repo: Repo) -> RepoResult:
        # Contexts to require are derived from a recent PR's check runs when
        # available. Without any (a brand-new repo, or one that's only ever
        # been pushed to directly), the baseline protection -- PR required,
        # no force-push/deletion, admins enforced -- still applies; it just
        # can't gate on specific status checks yet. A later run picks up
        # contexts once a PR exists to sample them from.
        pr_head_shas = await _recent_pr_head_shas(owner, repo.name)
        contexts: list[str] = []
        pending_note = None
        if not pr_head_shas:
            pending_note = "no pull requests found yet, requiring none for now"
        else:
            contexts = await _check_run_contexts(owner, repo.name, pr_head_shas)
            if not contexts:
                pending_note = (
                    f"no check runs found on latest PR commit {pr_head_shas[0]}, "
                    "requiring none for now"
                )

        protection_response = await api_request(
            "GET",
            f"/repos/{owner}/{repo.name}/branches/{repo.default_branch}/protection",
        )
        if protection_response.status_code == 403:
            return _plan_gated_result(
                repo, tag=Tag.SKIPPED_NO_PLAN if not dry_run else None
            )
        if protection_response.status_code == 404:
            current = None
        elif protection_response.is_success:
            current = protection_response.json()
        else:
            raise GhError(
                error_message(protection_response),
                status_code=protection_response.status_code,
            )
        up_to_date = branch_protection_up_to_date(current, contexts)

        require_desc = ", ".join(contexts) if contexts else "(none yet)"
        suffix = f"; {pending_note}" if pending_note else ""
        tag = Tag.APPLIED if contexts else Tag.APPLIED_NO_CHECKS

        if dry_run:
            if up_to_date:
                status = Status.UNCHANGED
                detail = f"{require_desc}{suffix}"
                return RepoResult(repo, result_line(repo.name, detail, status), status)
            status = Status.OK
            detail = f"would update -> require: {require_desc}{suffix}"
            return RepoResult(repo, result_line(repo.name, detail, status), status)

        if up_to_date:
            status = Status.UNCHANGED
            detail = f"{require_desc}{suffix}"
            return RepoResult(
                repo, result_line(repo.name, detail, status), status, tag=tag
            )

        payload = branch_protection_payload(contexts)
        put_response = await api_request(
            "PUT",
            f"/repos/{owner}/{repo.name}/branches/{repo.default_branch}/protection",
            json=payload,
        )
        if put_response.status_code == 403:
            return _plan_gated_result(repo, tag=Tag.SKIPPED_NO_PLAN)
        if not put_response.is_success:
            raise GhError(
                error_message(put_response), status_code=put_response.status_code
            )

        status = Status.OK
        detail = f"protected ({require_desc}){suffix}"
        return RepoResult(repo, result_line(repo.name, detail, status), status, tag=tag)

    return worker


async def cmd_branch_protection(args: argparse.Namespace) -> int:
    skip = (as_set(args.skip) or set()) | default_branch_protection_exclude()
    repos = await list_repos(DEFAULT_OWNER, only=as_set(args.only), skip=skip)
    results = await run_parallel(
        repos, make_branch_protection_worker(DEFAULT_OWNER, args.dry_run)
    )

    if args.dry_run:
        return 0

    applied = [r for r in results if r.tag == Tag.APPLIED]
    applied_no_checks = sorted(
        r.repo.name for r in results if r.tag == Tag.APPLIED_NO_CHECKS
    )
    skipped_no_plan = sorted(
        r.repo.name for r in results if r.tag == Tag.SKIPPED_NO_PLAN
    )
    print()
    print("Summary:")
    print(f"  Protected (with required status checks): {len(applied)}")
    print(
        "  Protected (no required status checks yet -- no PRs / no check runs "
        f"seen): {' '.join(applied_no_checks) or 'none'}"
    )
    print(
        "  Skipped (plan doesn't allow branch protection on private repos): "
        f"{' '.join(skipped_no_plan) or 'none'}"
    )
    return 0


# ---------------------------------------------------------------------------
# pages-domain
#
# Sets each repo's GitHub Pages custom domain from pages-domains.yaml -- the
# single source of truth also read by iac/cloudflare's OpenTofu config to
# generate the matching CNAME/verification DNS records. Unlike the other
# commands, this doesn't apply the same setting account-wide: only repos
# listed in the mapping are touched.
#
# https_enforced is only ever turned on, never off, and only once GitHub
# reports the domain's certificate as "approved" -- that requires the CNAME
# DNS record to already resolve, so a freshly-set cname always needs a later
# rerun to pick up https_enforced once the cert catches up.
# ---------------------------------------------------------------------------


async def _pages_config(owner: str, name: str) -> dict:
    return await api_json("GET", f"/repos/{owner}/{name}/pages")


def pages_domain_https_ready(pages_json: dict) -> bool:
    return (pages_json.get("https_certificate") or {}).get("state") == "approved"


def pages_domain_up_to_date(pages_json: dict, domain: str) -> bool:
    return (
        pages_json.get("cname") == domain and pages_json.get("https_enforced") is True
    )


def pages_domain_dry_run_line(
    name: str, pages_json: dict, domain: str, status: Status
) -> str:
    cname_ok = pages_json.get("cname") == domain
    https_ok = pages_json.get("https_enforced") is True
    if cname_ok and https_ok:
        detail = f"cname={domain}, https enforced"
    elif not cname_ok:
        detail = f"would set cname -> {domain}"
    elif pages_domain_https_ready(pages_json):
        detail = f"cname={domain}; would enable https_enforced"
    else:
        detail = f"cname={domain}; https cert pending"
    return result_line(name, detail, status)


def pages_domain_apply_line(
    name: str, before: dict, after: dict, domain: str, status: Status
) -> str:
    cname_changed = before.get("cname") != after.get("cname")
    https_changed = before.get("https_enforced") != after.get("https_enforced")
    if not cname_changed and not https_changed:
        detail = (
            f"cname={domain}, https enforced"
            if after.get("https_enforced")
            else f"cname={domain}; https cert pending"
        )
        return result_line(name, detail, status)
    parts = []
    if cname_changed:
        parts.append(f"cname -> {domain}")
    if https_changed:
        parts.append("https_enforced -> true")
    return result_line(name, ", ".join(parts), status)


def make_pages_domain_worker(owner: str, dry_run: bool, domains: dict[str, str]):
    async def worker(repo: Repo) -> RepoResult:
        domain = domains[repo.name]
        current = await _pages_config(owner, repo.name)

        if dry_run:
            cname_ok = current.get("cname") == domain
            would_change = not cname_ok or (
                pages_domain_https_ready(current)
                and current.get("https_enforced") is not True
            )
            status = classify_status(
                at_target=pages_domain_up_to_date(current, domain), changed=would_change
            )
            return RepoResult(
                repo,
                pages_domain_dry_run_line(repo.name, current, domain, status),
                status,
            )

        before = current
        payload: dict = {}
        if before.get("cname") != domain:
            payload["cname"] = domain
        elif (
            pages_domain_https_ready(before)
            and before.get("https_enforced") is not True
        ):
            payload["https_enforced"] = True

        if payload:
            await api_json("PUT", f"/repos/{owner}/{repo.name}/pages", json=payload)
            after = await _pages_config(owner, repo.name)
        else:
            after = before

        status = classify_status(
            at_target=pages_domain_up_to_date(after, domain), changed=before != after
        )
        return RepoResult(
            repo,
            pages_domain_apply_line(repo.name, before, after, domain, status),
            status,
        )

    return worker


async def cmd_pages_domain(args: argparse.Namespace) -> int:
    domains = lib.default_pages_domains()
    only = as_set(args.only)
    if only:
        unknown = only - set(domains)
        if unknown:
            print(
                f"error: not in pages-domains.yaml: {', '.join(sorted(unknown))}",
                file=sys.stderr,
            )
            return 1
    else:
        only = set(domains)

    repos = await list_repos(DEFAULT_OWNER, only=only, skip=as_set(args.skip))
    await run_parallel(
        repos, make_pages_domain_worker(DEFAULT_OWNER, args.dry_run, domains)
    )
    return 0


# ---------------------------------------------------------------------------
# pages-status
#
# Read-only survey of which repos have GitHub Pages enabled and what custom
# domain (if any) they're currently serving -- lets pages-domains.yaml be
# checked for repos that have Pages on but aren't mapped yet.
# ---------------------------------------------------------------------------


async def _fetch_pages_config(owner: str, name: str) -> dict | None:
    response = await api_request("GET", f"/repos/{owner}/{name}/pages")
    if response.status_code == 404:
        return None
    if not response.is_success:
        raise GhError(error_message(response), status_code=response.status_code)
    return response.json()


async def _pages_enabled_repos(repos: list[Repo]) -> list[tuple[Repo, dict]]:
    """Fetches every repo's Pages config concurrently and returns the ones
    with Pages enabled, sorted by name.
    """
    sem = asyncio.Semaphore(DEFAULT_JOBS)

    async def fetch_one(repo: Repo) -> tuple[Repo, dict | None]:
        async with sem:
            return repo, await _fetch_pages_config(DEFAULT_OWNER, repo.name)

    with lib.progress_bar() as progress:
        task = progress.add_task("Fetching Pages config...", total=len(repos))

        async def fetch_and_advance(repo: Repo) -> tuple[Repo, dict | None]:
            result = await fetch_one(repo)
            progress.advance(task)
            return result

        results = await asyncio.gather(*(fetch_and_advance(repo) for repo in repos))

    enabled: list[tuple[Repo, dict]] = [
        (repo, config) for repo, config in results if config is not None
    ]
    enabled.sort(key=lambda item: item[0].name)
    return enabled


async def cmd_pages_status(args: argparse.Namespace) -> int:
    with lib.progress_bar(transient=True) as progress:
        progress.add_task("Fetching repos...", total=None)
        repos = await list_repos(
            DEFAULT_OWNER, only=as_set(args.only), skip=as_set(args.skip)
        )

    enabled = await _pages_enabled_repos(repos)
    domains = lib.default_pages_domains()

    table = Table()
    table.add_column("NAME")
    table.add_column("URL", overflow="fold")
    table.add_column("HTTPS")
    table.add_column("MAPPED")
    for repo, config in enabled:
        https = (
            "enforced"
            if config.get("https_enforced")
            else (config.get("https_certificate") or {}).get("state", "n/a")
        )
        table.add_row(
            repo.name,
            config.get("html_url") or "(unknown)",
            https,
            "yes" if repo.name in domains else "no",
        )
    lib.console.print(table)

    missing = sorted(repo.name for repo, _config in enabled if repo.name not in domains)
    if missing:
        print()
        print(f"Pages enabled but not in pages-domains.yaml: {' '.join(missing)}")

    return 0


# ---------------------------------------------------------------------------
# pages-domain-config
#
# Prints pages-domains.yaml-formatted entries to stdout for a base domain --
# `<repo> -> <repo>.<domain>`, dots in the repo name replaced with dashes
# since a raw dot would split a hostname across two DNS labels instead of
# one. With --only, generates for exactly those repos (no API calls). With
# neither, auto-discovers repos with Pages enabled but missing from
# pages-domains.yaml -- the same set `pages-status` flags -- and suggests
# entries for those.
# ---------------------------------------------------------------------------


def pages_domain_suggest(repo_name: str, domain: str) -> str:
    return f"{repo_name.replace('.', '-')}.{domain}"


async def cmd_pages_domain_config(args: argparse.Namespace) -> int:
    only = as_set(args.only)
    if only:
        names = sorted(only)
    else:
        repos = await list_repos(DEFAULT_OWNER, only=None, skip=as_set(args.skip))
        enabled = await _pages_enabled_repos(repos)
        domains = lib.default_pages_domains()
        names = sorted(
            repo.name for repo, _config in enabled if repo.name not in domains
        )

    for name in names:
        print(f"{name}: {pages_domain_suggest(name, args.domain)}")

    return 0


# ---------------------------------------------------------------------------
# all
# ---------------------------------------------------------------------------


async def cmd_all(args: argparse.Namespace) -> int:
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
            if await cmd(args) != 0:
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
    subparsers.add_parser("pages-domain", parents=[mutating_parent]).set_defaults(
        func=cmd_pages_domain
    )
    subparsers.add_parser("pages-status", parents=[filter_parent]).set_defaults(
        func=cmd_pages_status
    )
    pages_domain_config_parser = subparsers.add_parser(
        "pages-domain-config", parents=[filter_parent]
    )
    pages_domain_config_parser.add_argument(
        "--domain", required=True, help="base domain, e.g. larve.net"
    )
    pages_domain_config_parser.set_defaults(func=cmd_pages_domain_config)

    return parser


async def _run(args: argparse.Namespace) -> int:
    try:
        return await args.func(args)
    finally:
        await lib.aclose_client()


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except GhError as exc:
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
