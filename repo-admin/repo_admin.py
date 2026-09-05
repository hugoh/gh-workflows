"""Bulk-applies account-wide GitHub repo settings across hugoh's non-archived
repos, via the GitHub REST API (authenticated through `gh auth token`).

Usage: repo_admin.py <resource> <verb> [repo ...] [--dry-run] [--verbose] [--skip name1,name2]

  repos    list                 list repos as a table: name, default branch,
                                 private, fork
  repo     scaffold PATH        render a new repo's baseline files (hk.pkl,
                                 mise.toml, .renovaterc.json, workflow callers)
                                 from templates/ — see `repo scaffold --help`
  merge    sync                 enable auto-merge, delete-branch-on-merge,
                                 and PR-branch auto-update
  protection sync               apply a baseline branch-protection policy to
                                 each repo's default branch
  security sync                 enable free, native GitHub security features
  sync                          run merge/protection/security sync, in that
                                 order
  pages    status               list repos with GitHub Pages enabled and
                                 their custom domain, flagging ones missing
                                 from config/pages-domains.yaml
  pages    sync                 set each repo's GitHub Pages custom domain
                                 and homepage URL, from the mapping in
                                 config/pages-domains.yaml
  pages    config --domain D    print config/pages-domains.yaml entries to
                                 stdout for a base domain, for the given
                                 repos or, if none given, repos `pages
                                 status` would flag as unmapped
  secrets  sync                 push shared GitHub Actions secrets
                                 (config/secrets.yaml -> repos, values from
                                 sops-encrypted config/secrets.enc.yaml) to
                                 each configured repo
  secrets  edit                 open config/secrets.enc.yaml in `sops` for
                                 interactive editing, seeding it from
                                 config/secrets.yaml the first time
  activity                      rank repos by recent commit activity
                                 (private+public and public-only tables),
                                 see activity.py --help for its knobs

Every `<resource> <verb>` accepts trailing repo names to scope to a subset
(default: every repo); `--skip name1,name2` excludes instead. Forks are
excluded by default -- except those listed in config/include-forks.txt; edit
that file to add more, or override per-run with GH_INCLUDE_FORKS
(comma-separated). GH_OWNER overrides the default owner (the authenticated
account); GH_JOBS controls parallelism (default 6). Run a mutating command
with --dry-run
first and review the output; `--verbose` shows every repo, not just the
ones that changed.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import copy
import enum
import re
import sys
from dataclasses import dataclass, field
from fnmatch import fnmatch

import activity
import lib
import scaffold
import yaml
from lib import (
    DEFAULT_JOBS,
    GhError,
    Repo,
    RepoResult,
    Status,
    api_json,
    api_raw,
    as_set,
    classify_status,
    default_branch_protection_exclude,
    default_owner,
    error_message,
    list_repos,
    partition_fields,
    result_line,
    run_parallel,
    run_reconcile,
    summary_status,
    unavailable_suffix,
)
from rich.table import Table


class Tag(enum.StrEnum):
    """Per-command bookkeeping for end-of-run summaries -- distinct from
    Status, which is the shared display concept in lib.py.
    """

    APPLIED = "applied"
    APPLIED_NO_CHECKS = "applied_no_checks"
    APPLIED_RULESET = "applied_ruleset"
    APPLIED_BOTH = "applied_both"
    RULESET_EVALUATE_MODE = "ruleset_evaluate_mode"
    MULTIPLE_RULESETS = "multiple_rulesets"
    ORG_RULESET = "org_ruleset"
    SKIPPED_NO_PLAN = "skipped_no_plan"
    UNAVAILABLE = "unavailable"


async def list_repos_for_args(
    args: argparse.Namespace, *, extra_skip: set[str] | None = None
) -> list[Repo]:
    skip = as_set(args.skip) or set()
    if extra_skip:
        skip |= extra_skip
    only = set(args.repos) or None
    return await list_repos(
        await default_owner(), only=only, skip=skip, require_only_match=bool(only)
    )


# ---------------------------------------------------------------------------
# repos list
# ---------------------------------------------------------------------------


async def cmd_repos_list(args: argparse.Namespace) -> int:
    with lib.progress_bar(transient=True) as progress:
        progress.add_task("Fetching repos...", total=None)
        repos = await list_repos_for_args(args)

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
# merge sync
#
# Enables auto-merge, delete-branch-on-merge, and PR-branch auto-update. The
# last one matters because branch protection requires PR branches to be up
# to date with the base branch (`strict: true`) -- without auto-update,
# auto-merge PRs get stuck needing a manual "Update branch" click whenever
# another PR merges first.
#
# allow_auto_merge on a private repo silently no-ops on this account's plan
# (private-repo auto-merge needs GitHub Pro/Team/Enterprise) -- confirmed by
# a live PATCH against a private repo returning 200 with the field still
# false. GET always reports the real current value, so dry-run can't tell
# this apart from "would enable" without attempting the write; instead it's
# reported as unavailable, the same way security-features reports its own
# plan-gated fields.
# ---------------------------------------------------------------------------

MERGE_SETTINGS_FIELDS = [
    "allow_auto_merge",
    "delete_branch_on_merge",
    "allow_update_branch",
]


def _merge_gated_fields(*, is_private: bool) -> list[str]:
    return ["allow_auto_merge"] if is_private else []


async def _merge_settings(owner: str, name: str) -> dict:
    data = await api_json("GET", f"/repos/{owner}/{name}")
    return {field: data[field] for field in MERGE_SETTINGS_FIELDS}


def merge_settings_at_target(settings: dict) -> bool:
    return all(settings[field] for field in MERGE_SETTINGS_FIELDS)


def merge_settings_summarize(current: dict, *, is_private: bool = False) -> dict:
    gated = set(_merge_gated_fields(is_private=is_private))
    return partition_fields(
        {
            field: (bool(current[field]), field not in gated)
            for field in MERGE_SETTINGS_FIELDS
        }
    )


def merge_settings_dry_run_line(
    name: str, current: dict, status: Status, *, is_private: bool = False
) -> str:
    summary = merge_settings_summarize(current, is_private=is_private)
    would_enable, unavailable = summary["would_enable"], summary["unavailable"]
    detail = (
        str(current) if not would_enable else f"would enable: {', '.join(would_enable)}"
    )
    return result_line(name, detail + unavailable_suffix(unavailable), status)


def merge_settings_apply_line(
    name: str, before: dict, after: dict, status: Status
) -> str:
    detail = str(after) if before == after else f"{before} -> {after}"
    return result_line(name, detail, status)


def make_merge_settings_worker(owner: str, dry_run: bool):
    async def worker(repo: Repo) -> RepoResult:
        def plan_result(current: dict) -> RepoResult:
            status = summary_status(
                merge_settings_summarize(current, is_private=repo.is_private)
            )
            return RepoResult(
                repo,
                merge_settings_dry_run_line(
                    repo.name, current, status, is_private=repo.is_private
                ),
                status,
            )

        async def apply_result(before: dict) -> RepoResult:
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
                repo,
                merge_settings_apply_line(repo.name, before, after, status),
                status,
            )

        return await run_reconcile(
            dry_run=dry_run,
            fetch=lambda: _merge_settings(owner, repo.name),
            plan_result=plan_result,
            apply_result=apply_result,
        )

    return worker


async def cmd_merge_sync(args: argparse.Namespace) -> int:
    repos = await list_repos_for_args(args)
    await run_parallel(
        repos,
        make_merge_settings_worker(await default_owner(), args.dry_run),
        verbose=args.verbose,
    )
    return 0


# ---------------------------------------------------------------------------
# security sync
#
# Enables free, native GitHub security features:
#   - Dependabot vulnerability alerts -- works on every repo, no plan gate
#   - secret scanning, secret scanning push protection, and Dependabot
#     security updates -- public repos only; private repos need GitHub
#     Advanced Security, a paid add-on this account's plan doesn't include
#   - private vulnerability reporting -- same public-repo-only gate
#   - CodeQL code scanning default setup -- same public-repo-only gate, and
#     also unavailable on repos with no CodeQL-supported language
#
# Repos where a feature is unavailable are reported, not treated as a
# failure -- same approach as branch-protection's private-repo handling.
# ---------------------------------------------------------------------------


def security_summarize(
    repo_json: dict,
    *,
    vuln_alerts_enabled: bool,
    pvr_json: dict | None,
    codeql_json: dict | None,
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
        "code_scanning": (
            (codeql_json or {}).get("state") == "configured",
            codeql_json is not None,
        ),
    }
    return partition_fields(features)


def security_dry_run_line(name: str, summary: dict, status: Status) -> str:
    would_enable, unavailable = summary["would_enable"], summary["unavailable"]
    detail = (
        "enabled" if not would_enable else f"would enable: {', '.join(would_enable)}"
    )
    return result_line(name, detail + unavailable_suffix(unavailable), status)


async def _fetch_security_state(
    owner: str, name: str
) -> tuple[dict, bool, dict | None, dict | None]:
    repo_json = await api_json("GET", f"/repos/{owner}/{name}")

    # 204 = enabled, 404 = disabled -- GitHub's documented shape for this
    # endpoint, not an error either way.
    vuln_response = await api_raw("GET", f"/repos/{owner}/{name}/vulnerability-alerts")
    if vuln_response.status_code not in (204, 404):
        raise GhError(
            error_message(vuln_response), status_code=vuln_response.status_code
        )
    vuln_alerts_enabled = vuln_response.status_code == 204

    # 200 = available (body has "enabled"), 404 = not available on this plan.
    pvr_response = await api_raw(
        "GET", f"/repos/{owner}/{name}/private-vulnerability-reporting"
    )
    if pvr_response.status_code not in (200, 404):
        raise GhError(error_message(pvr_response), status_code=pvr_response.status_code)
    pvr_json = pvr_response.json() if pvr_response.status_code == 200 else None

    # 200 = available (body has "state"), 404/403 = not available on this
    # plan or repo (no Advanced Security, GHES, or no supported language).
    codeql_response = await api_raw(
        "GET", f"/repos/{owner}/{name}/code-scanning/default-setup"
    )
    if codeql_response.status_code not in (200, 403, 404):
        raise GhError(
            error_message(codeql_response), status_code=codeql_response.status_code
        )
    codeql_json = codeql_response.json() if codeql_response.status_code == 200 else None

    return repo_json, vuln_alerts_enabled, pvr_json, codeql_json


def make_security_features_worker(owner: str, dry_run: bool):
    async def worker(repo: Repo) -> RepoResult:
        async def fetch() -> dict:
            (
                repo_json,
                vuln_alerts_enabled,
                pvr_json,
                codeql_json,
            ) = await _fetch_security_state(owner, repo.name)
            return security_summarize(
                repo_json,
                vuln_alerts_enabled=vuln_alerts_enabled,
                pvr_json=pvr_json,
                codeql_json=codeql_json,
            )

        def plan_result(before_summary: dict) -> RepoResult:
            status = summary_status(before_summary)
            return RepoResult(
                repo, security_dry_run_line(repo.name, before_summary, status), status
            )

        async def apply_result(before_summary: dict) -> RepoResult:
            await api_json("PUT", f"/repos/{owner}/{repo.name}/vulnerability-alerts")

            unavailable = []

            # 422 = one or more of these fields isn't available on this plan
            # (GitHub Advanced Security is required for private repos).
            security_response = await api_raw(
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
            pvr_response = await api_raw(
                "PUT", f"/repos/{owner}/{repo.name}/private-vulnerability-reporting"
            )
            if pvr_response.status_code == 404:
                unavailable.append("private vulnerability reporting")
            elif not pvr_response.is_success:
                raise GhError(
                    error_message(pvr_response), status_code=pvr_response.status_code
                )

            # 403/404 = not available on this plan or repo; 422 = no
            # CodeQL-supported language detected in the repo.
            codeql_response = await api_raw(
                "PATCH",
                f"/repos/{owner}/{repo.name}/code-scanning/default-setup",
                json={"state": "configured"},
            )
            if codeql_response.status_code in (403, 404, 422):
                unavailable.append("code scanning")
            elif not codeql_response.is_success:
                raise GhError(
                    error_message(codeql_response),
                    status_code=codeql_response.status_code,
                )

            status = classify_status(
                at_target=not unavailable,
                changed=bool(before_summary["would_enable"]),
            )
            detail = "enabled" + unavailable_suffix(unavailable)
            tag = Tag.UNAVAILABLE if unavailable else None
            return RepoResult(
                repo, result_line(repo.name, detail, status), status, tag=tag
            )

        return await run_reconcile(
            dry_run=dry_run,
            fetch=fetch,
            plan_result=plan_result,
            apply_result=apply_result,
        )

    return worker


async def cmd_security_sync(args: argparse.Namespace) -> int:
    repos = await list_repos_for_args(args)
    results = await run_parallel(
        repos,
        make_security_features_worker(await default_owner(), args.dry_run),
        verbose=args.verbose,
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
# protection sync
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
# When a repo that already requires checks yields none to sample (the latest
# PR's workflow runs aged out of the API, a stale pre-CI PR got bumped to the
# top by a comment), its existing contexts are retained rather than the merge
# gate being cleared; --clear-stale-checks opts into dropping them for a repo
# that genuinely retired its CI.
#
# Only check runs reported by the "github-actions" app are considered:
# third-party apps (DeepSource, Codecov, etc.) aren't defined by the repo
# itself, can be reconfigured or removed outside of this tool's control, and
# shouldn't be able to block merges as a side effect of having run once on a
# PR.
#
# Even under the "github-actions" app, GitHub-managed setups post checks the
# repo can't edit: CodeQL default setup runs as a synthetic "CodeQL" workflow
# ("Analyze (...)" jobs) and Advanced Security posts a "github-advanced-security"
# check, both from `dynamic/...` workflow paths rather than a file in the repo's
# `.github/workflows/`. Each check run links to its workflow run via a shared
# check-suite id, so a context is kept when either (1) that suite belongs to a
# workflow run whose path is under `.github/workflows/`, or (2) the check name's
# first `" / "`-segment names a job in one of the repo's own workflow files that
# calls a reusable workflow via `uses:`. Signal 2 covers `hk / lint`-style
# checks: a caller job `hk` doing `uses: hugoh/gh-workflows/.../hk.yml` can have
# its check-run attributed to a child workflow run whose path points outside
# `.github/workflows/`, which signal 1 alone would drop as third-party.
#
# The required-checks gate can live in classic branch protection, in a
# repository ruleset's `required_status_checks` rule, or both -- ChecksTarget
# abstracts over that. A ruleset is adopted (its checks rule updated) when it is
# active/evaluate, targets the default branch, and already has a
# `required_status_checks` rule; the whole ruleset object is round-tripped on
# write so `bypass_actors`, `conditions`, and other rules are preserved
# verbatim. `evaluate`-mode, multiple-matching, and org-level rulesets are
# reported (Status.LIMITED) rather than guessed at. New repos with neither
# mechanism still get classic protection.
#
# Private repos on a plan that doesn't expose branch protection return a 403
# ("Upgrade to GitHub Pro..."); those are collected and reported at the end
# rather than treated as a hard failure (unless a ruleset already enforces the
# gate, in which case that ruleset is used).
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


def current_protection_contexts(current: dict | None) -> list[str]:
    required_status_checks = (current or {}).get("required_status_checks") or {}
    return sorted(required_status_checks.get("contexts") or [])


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


async def _own_workflow_check_suite_ids(owner: str, name: str, sha: str) -> set[int]:
    """check-suite ids of workflow runs defined by files in `.github/workflows/`.

    GitHub-managed setups (CodeQL default setup, the Advanced Security findings
    run) execute under synthetic `dynamic/...` paths; their checks shouldn't
    become merge gates the repo can't edit.
    """
    runs = await api_json(
        "GET",
        f"/repos/{owner}/{name}/actions/runs",
        params={"head_sha": sha, "per_page": "100"},
    )
    return {
        run["check_suite_id"]
        for run in runs["workflow_runs"]
        if run["path"].startswith(".github/workflows/")
    }


async def _own_reusable_job_prefixes(owner: str, name: str, ref: str) -> set[str]:
    """Job keys / names of jobs in the repo's own `.github/workflows/*.yml`
    that call a reusable workflow via `uses:`.

    A caller job `hk` that does `uses: hugoh/gh-workflows/.github/workflows/hk.yml`
    surfaces on a PR as a check named `hk / <reusable job>` (e.g. `hk / lint`).
    GitHub attributes that check-run's check-suite inconsistently -- sometimes to
    the caller run under `.github/workflows/`, sometimes to a child run whose
    path points at the reusable workflow's own repo -- so `_check_run_contexts`
    also accepts a check as "the repo's own" when its first `" / "`-segment is
    one of these prefixes.
    """
    listing = await api_raw(
        "GET",
        f"/repos/{owner}/{name}/contents/.github/workflows",
        params={"ref": ref},
    )
    if listing.status_code == 404:
        return set()
    if not listing.is_success:
        raise GhError(error_message(listing), status_code=listing.status_code)
    entries = listing.json()
    if not isinstance(entries, list):
        return set()

    prefixes: set[str] = set()
    for entry in entries:
        filename = entry.get("name", "")
        if entry.get("type") != "file" or not filename.endswith((".yml", ".yaml")):
            continue
        blob = await api_json(
            "GET",
            f"/repos/{owner}/{name}/contents/.github/workflows/{filename}",
            params={"ref": ref},
        )
        try:
            content = base64.b64decode(blob["content"]).decode("utf-8")
            doc = yaml.safe_load(content) or {}
        except (ValueError, yaml.YAMLError):
            continue
        for key, job in (doc.get("jobs") or {}).items():
            if isinstance(job, dict) and isinstance(job.get("uses"), str):
                prefixes.add(key)
                if isinstance(job.get("name"), str):
                    prefixes.add(job["name"])
    return prefixes


def _own_reusable_check(name: str, own_reusable_job_prefixes: set[str]) -> bool:
    stripped = re.sub(r" \(.+\)$", "", name)
    if " / " not in stripped:
        return False
    return stripped.split(" / ", 1)[0] in own_reusable_job_prefixes


async def _check_run_contexts(
    owner: str,
    name: str,
    shas: list[str],
    own_reusable_job_prefixes: set[str] | None = None,
) -> list[str]:
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
    own_reusable_job_prefixes = own_reusable_job_prefixes or set()
    latest_runs = await _github_actions_check_runs(owner, name, shas[0])
    own_suites = await _own_workflow_check_suite_ids(owner, name, shas[0])
    latest_runs = [
        run
        for run in latest_runs
        if run["check_suite"]["id"] in own_suites
        or _own_reusable_check(run["name"], own_reusable_job_prefixes)
    ]
    contexts = {run["name"] for run in latest_runs}
    suspect = {
        run["name"]
        for run in latest_runs
        if run["conclusion"] == "skipped" and " / " not in run["name"]
    }
    if not suspect:
        return sorted(_collapse_matrix_legs(contexts))

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

    return sorted(_collapse_matrix_legs(contexts))


_MATRIX_LEG = re.compile(r"^(?P<prefix>.*?(?: / )?)(?P<job>[^/]+) \(.+\)$")


def _collapse_matrix_legs(contexts: set[str]) -> set[str]:
    """Drop matrix child checks ("jj / test (0.42)") when a sibling gate check
    ("jj / gate", "git-gate") covers them. Matrix leg names carry the varied
    value (a tool version, a runner label) and churn as that value moves, so
    requiring them by name leaves a stale context stuck pending after every
    bump; the gate is a stable stand-in. Without a gate the legs are kept, so
    protection is never silently weakened.
    """
    kept = set()
    for name in contexts:
        match = _MATRIX_LEG.match(name)
        if match and _gate_sibling(match["prefix"], match["job"]) & contexts:
            continue
        kept.add(name)
    return kept


def _gate_sibling(prefix: str, job: str) -> set[str]:
    return {f"{prefix}gate", f"{prefix}{job}-gate", f"{prefix}{job} / gate"}


def _reconcile_reusable_prefix(sampled: list[str], existing: list[str]) -> list[str]:
    """A `workflow_call` check is reported as `<caller job> / <reusable job>`
    on most runs but sometimes bare `<reusable job>` -- a GitHub inconsistency
    across runs of the same workflow. When a freshly-sampled context is the
    prefix-stripped (or prefixed) form of one already required, keep the
    existing spelling: switching the merge gate to the bare name strands it
    pending, since `main`'s runs report the prefixed name.
    """
    if not sampled or not existing:
        return sorted(set(sampled))
    out: list[str] = []
    for ctx in sampled:
        if ctx in existing:
            out.append(ctx)
            continue
        equiv = next(
            (e for e in existing if e.endswith(f" / {ctx}") or ctx.endswith(f" / {e}")),
            None,
        )
        out.append(equiv if equiv is not None else ctx)
    return sorted(set(out))


# The GitHub Actions app id on github.com. Pinning a ruleset's required-check
# context to it is the ruleset-mode equivalent of classic mode's "only the
# github-actions app can satisfy this gate" filter.
GITHUB_ACTIONS_APP_ID = 15368

_RULESET_ROUND_TRIP_FIELDS = (
    "name",
    "target",
    "enforcement",
    "conditions",
    "rules",
    "bypass_actors",
)


def _ruleset_ref_matches(conditions: dict, default_branch: str) -> bool:
    ref = (conditions or {}).get("ref_name") or {}
    target = f"refs/heads/{default_branch}"

    def hit(pattern: str) -> bool:
        if pattern in ("~ALL", "~DEFAULT_BRANCH"):
            return True
        return pattern == target or fnmatch(target, pattern)

    include = ref.get("include") or []
    exclude = ref.get("exclude") or []
    return any(hit(p) for p in include) and not any(hit(p) for p in exclude)


def _ruleset_checks_rule(ruleset: dict) -> dict | None:
    for rule in ruleset.get("rules") or []:
        if rule.get("type") == "required_status_checks":
            return rule
    return None


def _ruleset_contexts(ruleset: dict) -> list[str]:
    rule = _ruleset_checks_rule(ruleset)
    if rule is None:
        return []
    checks = (rule.get("parameters") or {}).get("required_status_checks") or []
    return sorted(entry["context"] for entry in checks)


def _ruleset_up_to_date(ruleset: dict, contexts: list[str]) -> bool:
    rule = _ruleset_checks_rule(ruleset)
    if rule is None:
        return False
    params = rule.get("parameters") or {}
    return (
        _ruleset_contexts(ruleset) == sorted(contexts)
        and params.get("strict_required_status_checks_policy") is True
    )


def ruleset_checks_payload(ruleset: dict, contexts: list[str]) -> dict:
    """The whole-object PUT body for a ruleset with only its
    `required_status_checks` rule mutated to require `contexts`.

    Everything else -- `bypass_actors`, `conditions`, other rule types, name,
    enforcement -- is carried through verbatim from the fetched object.
    """
    updated = copy.deepcopy(ruleset)
    rule = _ruleset_checks_rule(updated)
    if rule is None:
        raise ValueError("ruleset has no required_status_checks rule")
    params = rule.setdefault("parameters", {})
    existing = params.get("required_status_checks") or []
    prev_by_context = {entry["context"]: entry for entry in existing}
    changing = sorted(prev_by_context) != sorted(contexts)

    new_entries = []
    for context in sorted(contexts):
        prev = prev_by_context.get(context)
        if prev is not None and "integration_id" in prev:
            new_entries.append(
                {"context": context, "integration_id": prev["integration_id"]}
            )
        elif changing:
            new_entries.append(
                {"context": context, "integration_id": GITHUB_ACTIONS_APP_ID}
            )
        else:
            new_entries.append({"context": context})
    params["required_status_checks"] = new_entries
    params["strict_required_status_checks_policy"] = True
    return {key: updated[key] for key in _RULESET_ROUND_TRIP_FIELDS if key in updated}


@dataclass
class ChecksTarget:
    """Where a repo's required-status-check gate is enforced -- classic branch
    protection, one or more repository rulesets, both, or neither.
    """

    classic: dict | None
    repo_rulesets: list[dict] = field(default_factory=list)
    org_rulesets: list[dict] = field(default_factory=list)

    @property
    def mechanism(self) -> str:
        has_classic = self.classic is not None
        has_ruleset = bool(self.repo_rulesets) or bool(self.org_rulesets)
        if has_classic and has_ruleset:
            return "both"
        if has_ruleset:
            return "ruleset"
        if has_classic:
            return "classic"
        return "none"

    def current_contexts(self) -> list[str]:
        contexts = set(current_protection_contexts(self.classic))
        for ruleset in [*self.repo_rulesets, *self.org_rulesets]:
            contexts.update(_ruleset_contexts(ruleset))
        return sorted(contexts)


def checks_target_up_to_date(target: ChecksTarget, contexts: list[str]) -> bool:
    mechanism = target.mechanism
    if mechanism in ("classic", "both", "none") and not branch_protection_up_to_date(
        target.classic, contexts
    ):
        return False
    if mechanism in ("ruleset", "both"):
        for ruleset in target.repo_rulesets:
            if not _ruleset_up_to_date(ruleset, contexts):
                return False
    for ruleset in target.org_rulesets:
        if _ruleset_contexts(ruleset) != sorted(contexts):
            return False
    return True


async def _matching_rulesets(
    owner: str, name: str, default_branch: str
) -> tuple[list[dict], list[dict]]:
    """`(repo_rulesets, org_rulesets)` -- active/evaluate branch rulesets that
    target the default branch and carry a `required_status_checks` rule,
    fetched as full objects. Org-level rulesets can't be edited by a
    repo-scoped PUT; they're returned separately so callers can report them.
    """
    listing = await api_raw(
        "GET",
        f"/repos/{owner}/{name}/rulesets",
        params={"includes_parents": "true", "targets": "branch"},
    )
    if listing.status_code in (403, 404):
        return [], []
    if not listing.is_success:
        raise GhError(error_message(listing), status_code=listing.status_code)
    summaries = listing.json()
    if not isinstance(summaries, list):
        return [], []

    repo_rulesets: list[dict] = []
    org_rulesets: list[dict] = []
    for summary in summaries:
        if summary.get("enforcement") not in ("active", "evaluate"):
            continue
        detail = await api_raw("GET", f"/repos/{owner}/{name}/rulesets/{summary['id']}")
        if not detail.is_success:
            continue
        ruleset = detail.json()
        if not isinstance(ruleset, dict):
            continue
        ruleset.setdefault("id", summary.get("id"))
        if not _ruleset_ref_matches(ruleset.get("conditions") or {}, default_branch):
            continue
        if _ruleset_checks_rule(ruleset) is None:
            continue
        source_type = ruleset.get("source_type") or summary.get("source_type")
        if source_type == "Organization":
            org_rulesets.append(ruleset)
        else:
            repo_rulesets.append(ruleset)
    return repo_rulesets, org_rulesets


async def _put_ruleset_checks(
    owner: str, name: str, ruleset: dict, contexts: list[str]
) -> None:
    payload = ruleset_checks_payload(ruleset, contexts)
    response = await api_raw(
        "PUT", f"/repos/{owner}/{name}/rulesets/{ruleset['id']}", json=payload
    )
    if not response.is_success:
        raise GhError(error_message(response), status_code=response.status_code)


def _plan_gated_result(repo: Repo, *, tag: Tag | None = None) -> RepoResult:
    status = Status.LIMITED_UNCHANGED
    detail = "private repo, plan does not allow branch protection"
    return RepoResult(repo, result_line(repo.name, detail, status), status, tag=tag)


def make_branch_protection_worker(
    owner: str, dry_run: bool, clear_stale_checks: bool = False
):
    async def worker(repo: Repo) -> RepoResult:
        # Contexts to require are derived from a recent PR's check runs when
        # available. Without any (a brand-new repo, or one that's only ever
        # been pushed to directly), the baseline protection -- PR required,
        # no force-push/deletion, admins enforced -- still applies; it just
        # can't gate on specific status checks yet. A later run picks up
        # contexts once a PR exists to sample them from.
        #
        # A repo that already requires checks but yields no sampleable ones
        # (the latest PR's workflow runs aged out, a stale PR predating CI got
        # bumped to the top by a comment) keeps its existing contexts rather
        # than having the merge gate silently cleared; --clear-stale-checks
        # opts into dropping them for a repo that genuinely retired its CI.
        pr_head_shas = await _recent_pr_head_shas(owner, repo.name)
        own_reusable_job_prefixes = await _own_reusable_job_prefixes(
            owner, repo.name, repo.default_branch
        )
        contexts: list[str] = []
        if pr_head_shas:
            contexts = await _check_run_contexts(
                owner, repo.name, pr_head_shas, own_reusable_job_prefixes
            )

        protection_response = await api_raw(
            "GET",
            f"/repos/{owner}/{repo.name}/branches/{repo.default_branch}/protection",
        )
        plan_gated = protection_response.status_code == 403
        if plan_gated or protection_response.status_code == 404:
            current = None
        elif protection_response.is_success:
            current = protection_response.json()
        else:
            raise GhError(
                error_message(protection_response),
                status_code=protection_response.status_code,
            )

        repo_rulesets, org_rulesets = await _matching_rulesets(
            owner, repo.name, repo.default_branch
        )
        target = ChecksTarget(
            classic=current,
            repo_rulesets=repo_rulesets,
            org_rulesets=org_rulesets,
        )
        mechanism = target.mechanism

        # A plan that gates classic protection only matters when no ruleset
        # already enforces the checks gate; with one, that's the write target.
        if plan_gated and mechanism != "ruleset":
            return _plan_gated_result(
                repo, tag=Tag.SKIPPED_NO_PLAN if not dry_run else None
            )

        existing = target.current_contexts()
        stale_retained = False
        pending_note = None
        if contexts and existing:
            reconciled = _reconcile_reusable_prefix(contexts, existing)
            if reconciled != sorted(set(contexts)):
                pending_note = (
                    f"kept {', '.join(existing)} over sampled "
                    f"{', '.join(sorted(set(contexts)))} "
                    "(reusable-workflow check-name inconsistency across runs)"
                )
                contexts = reconciled
                stale_retained = True
        if not pr_head_shas:
            pending_note = "no pull requests found yet, requiring none for now"
        elif not contexts and existing and not clear_stale_checks:
            contexts = existing
            stale_retained = True
            pending_note = (
                f"no check runs found on latest PR commit {pr_head_shas[0]}; "
                f"keeping {', '.join(existing)} "
                "(pass --clear-stale-checks to drop them)"
            )
        elif not contexts:
            pending_note = (
                f"no check runs found on latest PR commit {pr_head_shas[0]}, "
                "requiring none for now"
            )

        up_to_date = checks_target_up_to_date(target, contexts)

        require_desc = ", ".join(contexts) if contexts else "(none yet)"
        suffix = f"; {pending_note}" if pending_note else ""
        tag = Tag.APPLIED if contexts else Tag.APPLIED_NO_CHECKS
        ok_status = Status.LIMITED if stale_retained else Status.OK
        unchanged_status = (
            Status.LIMITED_UNCHANGED if stale_retained else Status.UNCHANGED
        )

        if mechanism in ("ruleset", "both"):
            ruleset_ids = sorted(str(rs["id"]) for rs in repo_rulesets)
            mech_label = "ruleset" if mechanism == "ruleset" else "classic + ruleset"

            if len(repo_rulesets) > 1:
                detail = (
                    "multiple rulesets enforce required checks: "
                    f"{', '.join(ruleset_ids)} -- resolve manually{suffix}"
                )
                return RepoResult(
                    repo,
                    result_line(repo.name, detail, Status.LIMITED_UNCHANGED),
                    Status.LIMITED_UNCHANGED,
                    tag=None if dry_run else Tag.MULTIPLE_RULESETS,
                )

            if org_rulesets and not up_to_date:
                org_ids = ", ".join(str(rs["id"]) for rs in org_rulesets)
                detail = (
                    f"required checks enforced by an org ruleset {org_ids}; "
                    f"not managed here{suffix}"
                )
                return RepoResult(
                    repo,
                    result_line(repo.name, detail, Status.LIMITED_UNCHANGED),
                    Status.LIMITED_UNCHANGED,
                    tag=None if dry_run else Tag.ORG_RULESET,
                )

            evaluate_mode = any(
                rs.get("enforcement") == "evaluate" for rs in repo_rulesets
            )
            applied_tag = (
                Tag.RULESET_EVALUATE_MODE
                if evaluate_mode
                else (
                    Tag.APPLIED_RULESET if mechanism == "ruleset" else Tag.APPLIED_BOTH
                )
            )
            limited = evaluate_mode or stale_retained
            result_ok = Status.LIMITED if limited else Status.OK
            result_unchanged = Status.LIMITED_UNCHANGED if limited else Status.UNCHANGED
            eval_note = (
                "; ruleset in evaluate mode, not enforcing" if evaluate_mode else ""
            )

            if dry_run:
                if up_to_date:
                    detail = f"{mech_label}: {require_desc}{suffix}{eval_note}"
                    return RepoResult(
                        repo,
                        result_line(repo.name, detail, result_unchanged),
                        result_unchanged,
                    )
                was_desc = ", ".join(existing) if existing else "(none yet)"
                detail = (
                    f"would update {mech_label} -> require: {require_desc} "
                    f"(was: {was_desc}){suffix}{eval_note}"
                )
                return RepoResult(
                    repo, result_line(repo.name, detail, result_ok), result_ok
                )

            if up_to_date:
                detail = f"{mech_label}: {require_desc}{suffix}{eval_note}"
                return RepoResult(
                    repo,
                    result_line(repo.name, detail, result_unchanged),
                    result_unchanged,
                    tag=applied_tag,
                )

            if mechanism == "both":
                classic_put = await api_raw(
                    "PUT",
                    f"/repos/{owner}/{repo.name}/branches/{repo.default_branch}/protection",
                    json=branch_protection_payload(contexts),
                )
                if not classic_put.is_success and classic_put.status_code != 403:
                    raise GhError(
                        error_message(classic_put),
                        status_code=classic_put.status_code,
                    )
            for ruleset in repo_rulesets:
                await _put_ruleset_checks(owner, repo.name, ruleset, contexts)

            detail = f"protected via {mech_label} ({require_desc}){suffix}{eval_note}"
            return RepoResult(
                repo,
                result_line(repo.name, detail, result_ok),
                result_ok,
                tag=applied_tag,
            )

        if dry_run:
            if up_to_date:
                detail = f"{require_desc}{suffix}"
                return RepoResult(
                    repo,
                    result_line(repo.name, detail, unchanged_status),
                    unchanged_status,
                )
            was_desc = ", ".join(existing) if existing else "(none yet)"
            detail = (
                f"would update -> require: {require_desc} (was: {was_desc}){suffix}"
            )
            return RepoResult(
                repo, result_line(repo.name, detail, ok_status), ok_status
            )

        if up_to_date:
            detail = f"{require_desc}{suffix}"
            return RepoResult(
                repo,
                result_line(repo.name, detail, unchanged_status),
                unchanged_status,
                tag=tag,
            )

        payload = branch_protection_payload(contexts)
        put_response = await api_raw(
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

        detail = f"protected ({require_desc}){suffix}"
        return RepoResult(
            repo, result_line(repo.name, detail, ok_status), ok_status, tag=tag
        )

    return worker


async def cmd_protection_sync(args: argparse.Namespace) -> int:
    repos = await list_repos_for_args(
        args, extra_skip=default_branch_protection_exclude()
    )
    results = await run_parallel(
        repos,
        make_branch_protection_worker(
            await default_owner(),
            args.dry_run,
            clear_stale_checks=getattr(args, "clear_stale_checks", False),
        ),
        verbose=args.verbose,
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
    ruleset_enforced = sorted(
        r.repo.name for r in results if r.tag in (Tag.APPLIED_RULESET, Tag.APPLIED_BOTH)
    )
    needs_attention = sorted(
        r.repo.name
        for r in results
        if r.tag in (Tag.MULTIPLE_RULESETS, Tag.ORG_RULESET, Tag.RULESET_EVALUATE_MODE)
    )
    print()
    print("Summary:")
    print(f"  Protected (with required status checks): {len(applied)}")
    print(
        f"  Enforced via a repository ruleset: {' '.join(ruleset_enforced) or 'none'}"
    )
    print(
        "  Needs manual attention (multiple/org/evaluate-mode rulesets): "
        f"{' '.join(needs_attention) or 'none'}"
    )
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
# pages sync
#
# Sets each repo's GitHub Pages custom domain from config/pages-domains.yaml
# -- the single source of truth also read by iac/cloudflare's OpenTofu
# config to generate the matching CNAME/verification DNS records. Unlike the other
# commands, this doesn't apply the same setting account-wide: only repos
# listed in the mapping are touched. The repo's homepage URL is also pointed
# at https://<domain> so the "website" link tracks the custom domain.
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


def pages_homepage_url(domain: str) -> str:
    return f"https://{domain}"


def pages_domain_dry_run_line(
    name: str,
    pages_json: dict,
    domain: str,
    status: Status,
    homepage_ok: bool = True,
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
    if not homepage_ok:
        detail += f"; would set homepage -> {pages_homepage_url(domain)}"
    return result_line(name, detail, status)


def pages_domain_apply_line(
    name: str,
    before: dict,
    after: dict,
    domain: str,
    status: Status,
    homepage_changed: bool = False,
) -> str:
    cname_changed = before.get("cname") != after.get("cname")
    https_changed = before.get("https_enforced") != after.get("https_enforced")
    if not cname_changed and not https_changed and not homepage_changed:
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
    if homepage_changed:
        parts.append(f"homepage -> {pages_homepage_url(domain)}")
    return result_line(name, ", ".join(parts), status)


def make_pages_domain_worker(owner: str, dry_run: bool, domains: dict[str, str]):
    async def worker(repo: Repo) -> RepoResult:
        domain = domains[repo.name]
        homepage_ok = repo.homepage == pages_homepage_url(domain)

        def plan_result(current: dict) -> RepoResult:
            would_change = (
                current.get("cname") != domain
                or not homepage_ok
                or (
                    pages_domain_https_ready(current)
                    and current.get("https_enforced") is not True
                )
            )
            status = classify_status(
                at_target=pages_domain_up_to_date(current, domain) and homepage_ok,
                changed=would_change,
            )
            return RepoResult(
                repo,
                pages_domain_dry_run_line(
                    repo.name, current, domain, status, homepage_ok
                ),
                status,
            )

        async def apply_result(before: dict) -> RepoResult:
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

            if not homepage_ok:
                await api_json(
                    "PATCH",
                    f"/repos/{owner}/{repo.name}",
                    json={"homepage": pages_homepage_url(domain)},
                )

            status = classify_status(
                at_target=pages_domain_up_to_date(after, domain),
                changed=before != after or not homepage_ok,
            )
            return RepoResult(
                repo,
                pages_domain_apply_line(
                    repo.name,
                    before,
                    after,
                    domain,
                    status,
                    homepage_changed=not homepage_ok,
                ),
                status,
            )

        return await run_reconcile(
            dry_run=dry_run,
            fetch=lambda: _pages_config(owner, repo.name),
            plan_result=plan_result,
            apply_result=apply_result,
        )

    return worker


async def cmd_pages_sync(args: argparse.Namespace) -> int:
    domains = lib.default_pages_domains()
    only = set(args.repos)
    if only:
        unknown = only - set(domains)
        if unknown:
            print(
                f"error: not in config/pages-domains.yaml: {', '.join(sorted(unknown))}",
                file=sys.stderr,
            )
            return 1
    else:
        only = set(domains)

    repos = await list_repos(await default_owner(), only=only, skip=as_set(args.skip))
    await run_parallel(
        repos,
        make_pages_domain_worker(await default_owner(), args.dry_run, domains),
        verbose=args.verbose,
    )
    return 0


# ---------------------------------------------------------------------------
# pages status
#
# Read-only survey of which repos have GitHub Pages enabled and what custom
# domain (if any) they're currently serving -- lets config/pages-domains.yaml
# be checked for repos that have Pages on but aren't mapped yet.
# ---------------------------------------------------------------------------


async def _fetch_pages_config(owner: str, name: str) -> dict | None:
    response = await api_raw("GET", f"/repos/{owner}/{name}/pages")
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
            return repo, await _fetch_pages_config(await default_owner(), repo.name)

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
        repos = await list_repos_for_args(args)

    enabled = await _pages_enabled_repos(repos)
    domains = lib.default_pages_domains()

    table = Table()
    table.add_column("NAME")
    table.add_column("URL", overflow="fold")
    table.add_column("HTTPS")
    table.add_column("MAPPED")
    table.add_column("HOMEPAGE", overflow="fold")
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
            repo.homepage or "(none)",
        )
    lib.console.print(table)

    missing = sorted(repo.name for repo, _config in enabled if repo.name not in domains)
    if missing:
        print()
        print(
            f"Pages enabled but not in config/pages-domains.yaml: {' '.join(missing)}"
        )

    return 0


# ---------------------------------------------------------------------------
# pages config
#
# Prints config/pages-domains.yaml-formatted entries to stdout for a base
# domain -- `<repo> -> <repo>.<domain>`, dots in the repo name replaced with
# dashes since a raw dot would split a hostname across two DNS labels
# instead of one. Given repo names, generates for exactly those repos (no
# API calls). With none, auto-discovers repos with Pages enabled but
# missing from config/pages-domains.yaml -- the same set `pages status`
# flags -- and suggests entries for those.
# ---------------------------------------------------------------------------


def pages_domain_suggest(repo_name: str, domain: str) -> str:
    return f"{repo_name.replace('.', '-')}.{domain}"


async def cmd_pages_config(args: argparse.Namespace) -> int:
    only = set(args.repos)
    if only:
        names = sorted(only)
    else:
        repos = await list_repos(
            await default_owner(), only=None, skip=as_set(args.skip)
        )
        enabled = await _pages_enabled_repos(repos)
        domains = lib.default_pages_domains()
        names = sorted(
            repo.name for repo, _config in enabled if repo.name not in domains
        )

    for name in names:
        print(f"{name}: {pages_domain_suggest(name, args.domain)}")

    return 0


# ---------------------------------------------------------------------------
# secrets sync
#
# Pushes shared GitHub Actions secrets (e.g. TAP_GITHUB_TOKEN) to their
# configured repos, via GitHub's REST API (lib.set_repo_secret, using
# PyNaCl to encrypt for each repo's public key) -- consistent with every
# other mutating command here, unlike shelling out to `gh secret set`.
# Secret names -> target repos come from config/secrets.yaml
# (git-committed, plaintext); values come from config/secrets.enc.yaml,
# decrypted once via `sops -d` at the start of the run.
#
# GitHub's API never returns a secret's existing value (only its
# last-updated timestamp, not a meaningful diff signal here), so there's no
# "unchanged" detection -- every apply run is an unconditional set, and
# dry-run just reports what would be set.
#
# A secret maps to many repos and a repo can receive multiple secrets, so
# this loops per-secret (its own list_repos + run_parallel call each) --
# the same non-aborting chaining cmd_sync does across sub-commands, so one
# secret's failure doesn't stop the next secret's sync.
# ---------------------------------------------------------------------------


def secrets_sync_line(secret_name: str, repo_name: str, *, dry_run: bool) -> str:
    detail = f"would set {secret_name}" if dry_run else f"set {secret_name}"
    return result_line(repo_name, detail, Status.OK)


def make_secrets_sync_worker(owner: str, dry_run: bool, secret_name: str, value: str):
    async def worker(repo: Repo) -> RepoResult:
        line = secrets_sync_line(secret_name, repo.name, dry_run=dry_run)
        if not dry_run:
            await lib.set_repo_secret(owner, repo.name, secret_name, value)
        return RepoResult(repo, line, Status.OK)

    return worker


async def cmd_secrets_sync(args: argparse.Namespace) -> int:
    config = lib.default_secrets()
    secret_names = as_set(args.secret)
    if secret_names:
        unknown = secret_names - set(config)
        if unknown:
            print(
                f"error: not in config/secrets.yaml: {', '.join(sorted(unknown))}",
                file=sys.stderr,
            )
            return 1
    else:
        secret_names = set(config)

    values = lib.decrypt_secrets() if not args.dry_run else {}

    only = set(args.repos)
    skip = as_set(args.skip)
    failed = False
    for secret_name in sorted(secret_names):
        target_repos = set(config[secret_name])
        if only:
            target_repos &= only
        if skip:
            target_repos -= skip

        if not target_repos:
            print(f"== {secret_name} == (no matching repos, skipping)")
            continue

        if not args.dry_run and secret_name not in values:
            print(
                f"error: {secret_name!r} has no value in config/secrets.enc.yaml",
                file=sys.stderr,
            )
            failed = True
            continue

        print(f"== {secret_name} ==")
        repos = await list_repos(await default_owner(), only=target_repos)
        try:
            await run_parallel(
                repos,
                make_secrets_sync_worker(
                    await default_owner(),
                    args.dry_run,
                    secret_name,
                    values.get(secret_name, ""),
                ),
                verbose=args.verbose,
            )
        except GhError as exc:
            print(exc, file=sys.stderr)
            failed = True

    return 1 if failed else 0


# ---------------------------------------------------------------------------
# secrets edit
#
# Opens config/secrets.enc.yaml in `sops` for interactive editing (decrypts
# to $EDITOR, re-encrypts on save) -- the first time, seeds it pre-populated
# with every config/secrets.yaml key (empty values) so there's something to
# fill in rather than requiring the user to hand-write sops' metadata
# block. After editing, warns about drift against config/secrets.yaml: a
# configured secret with no value set, or a value left over from a
# removed/renamed secret.
# ---------------------------------------------------------------------------


def secrets_edit_template(secret_names: set[str]) -> str:
    return yaml.safe_dump({name: "" for name in sorted(secret_names)}, sort_keys=False)


async def cmd_secrets_edit(args: argparse.Namespace) -> int:
    secret_names = set(lib.default_secrets())
    if not secret_names:
        print("error: no secrets configured in config/secrets.yaml", file=sys.stderr)
        return 1

    if not lib.SECRETS_ENC_FILE.exists():
        print(f"creating {lib.SECRETS_ENC_FILE.name}...")
        lib.init_secrets_file(secrets_edit_template(secret_names))

    if lib.edit_secrets_file() != 0:
        print(
            "error: sops exited with a nonzero status; changes may not be saved",
            file=sys.stderr,
        )
        return 1

    values = lib.decrypt_secrets()
    missing = secret_names - set(values)
    stale = set(values) - secret_names
    if missing:
        print(
            f"warning: no value set for: {', '.join(sorted(missing))}", file=sys.stderr
        )
    if stale:
        print(
            f"warning: not in config/secrets.yaml (stale?): {', '.join(sorted(stale))}",
            file=sys.stderr,
        )
    return 0


# ---------------------------------------------------------------------------
# sync (meta)
# ---------------------------------------------------------------------------


async def cmd_sync(args: argparse.Namespace) -> int:
    """Runs merge, protection, then security sync in that order (matching
    the README's ordering -- merge sync's PR-branch auto-update makes
    protection sync's auto-merge-friendly baseline behave as intended). One
    command failing doesn't stop the others; the exit code is nonzero if
    any of them failed.
    """
    failed = False
    for name, cmd in (
        ("merge sync", cmd_merge_sync),
        ("protection sync", cmd_protection_sync),
        ("security sync", cmd_security_sync),
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
# activity
# ---------------------------------------------------------------------------


async def cmd_activity(args: argparse.Namespace) -> int:
    return await activity.run(args)


async def cmd_repo_scaffold(args: argparse.Namespace) -> int:
    return await scaffold.run(args)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    resources = parser.add_subparsers(dest="resource", required=True)

    repo_scope = argparse.ArgumentParser(add_help=False)
    repo_scope.add_argument(
        "repos", nargs="*", metavar="REPO", help="repo names to target (default: all)"
    )
    repo_scope.add_argument("--skip", help="comma-separated repo names to exclude")

    mutating = argparse.ArgumentParser(add_help=False, parents=[repo_scope])
    mutating.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would change, without changing anything",
    )
    mutating.add_argument(
        "--verbose",
        action="store_true",
        help="show unchanged repos too, not just changes",
    )

    def resource_verbs(name: str) -> argparse._SubParsersAction:
        return resources.add_parser(name).add_subparsers(dest="verb", required=True)

    resource_verbs("repos").add_parser("list", parents=[repo_scope]).set_defaults(
        func=cmd_repos_list
    )

    repo_scaffold = resource_verbs("repo").add_parser(
        "scaffold", help="render a new repo's baseline files from templates/"
    )
    scaffold.add_arguments(repo_scaffold)
    repo_scaffold.set_defaults(func=cmd_repo_scaffold)
    resource_verbs("merge").add_parser("sync", parents=[mutating]).set_defaults(
        func=cmd_merge_sync
    )
    protection_sync = resource_verbs("protection").add_parser(
        "sync", parents=[mutating]
    )
    protection_sync.add_argument(
        "--clear-stale-checks",
        action="store_true",
        help=(
            "drop required status checks for a repo whose recent PRs yield no "
            "sampleable check runs (default: keep the existing ones)"
        ),
    )
    protection_sync.set_defaults(func=cmd_protection_sync)
    resource_verbs("security").add_parser("sync", parents=[mutating]).set_defaults(
        func=cmd_security_sync
    )
    resources.add_parser("sync", parents=[mutating]).set_defaults(func=cmd_sync)

    pages = resource_verbs("pages")
    pages.add_parser("status", parents=[repo_scope]).set_defaults(func=cmd_pages_status)
    pages.add_parser("sync", parents=[mutating]).set_defaults(func=cmd_pages_sync)
    pages_config_parser = pages.add_parser("config", parents=[repo_scope])
    pages_config_parser.add_argument(
        "--domain", required=True, help="base domain, e.g. larve.net"
    )
    pages_config_parser.set_defaults(func=cmd_pages_config)

    secrets = resource_verbs("secrets")
    secrets_sync_parser = secrets.add_parser("sync", parents=[mutating])
    secrets_sync_parser.add_argument(
        "--secret",
        help="comma-separated secret names to sync (default: all in "
        "config/secrets.yaml)",
    )
    secrets_sync_parser.set_defaults(func=cmd_secrets_sync)
    secrets.add_parser("edit").set_defaults(func=cmd_secrets_edit)

    activity_parser = resources.add_parser("activity")
    activity_parser.add_argument(
        "--window-months",
        type=int,
        default=12,
        help="how far back to fetch commits, in months (default 12)",
    )
    activity_parser.add_argument(
        "--half-life-days",
        type=float,
        default=30,
        help="decay half-life for the ranking score, in days (default 30)",
    )
    activity_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="max repos to show per table, 0 for unlimited (default 20)",
    )
    activity_parser.set_defaults(func=cmd_activity)

    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    return lib.run_cli(args.func, args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
