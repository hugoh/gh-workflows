"""Shared helpers for repo-admin/*.py scripts. Not meant to be run directly."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parent
DEFAULT_OWNER = os.environ.get("GH_OWNER", "hugoh")
DEFAULT_JOBS = int(os.environ.get("GH_JOBS", "6"))


class GhError(RuntimeError):
    """A `gh` invocation -- or a repo's worker function -- failed unexpectedly."""


def run_gh(*args: str, input: str | None = None) -> str:
    """Runs `gh <args>`, returning stdout. Raises GhError (with stderr as the
    message) on a nonzero exit -- callers that expect a particular failure
    (e.g. a 404 for a plan-gated feature) should catch GhError and inspect
    its message rather than pre-checking.
    """
    result = subprocess.run(
        ["gh", *args],
        input=input,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise GhError(result.stderr.strip())
    return result.stdout


@dataclass(frozen=True)
class Repo:
    name: str
    default_branch: str
    is_private: bool
    is_fork: bool


@dataclass
class RepoResult:
    repo: Repo
    line: str
    tag: str | None = None


def fetch_repos_json(owner: str) -> list[dict]:
    out = run_gh(
        "repo",
        "list",
        owner,
        "--limit",
        "300",
        "--json",
        "name,isFork,isArchived,isPrivate,defaultBranchRef",
    )
    return json.loads(out)


def default_include_forks() -> set[str]:
    """Forks hugoh actually maintains and wants managed like any other repo,
    read from include-forks.txt (one name per line, '#' comments and blank
    lines ignored). Override with GH_INCLUDE_FORKS (comma-separated) for a
    one-off run; edit the file to permanently add one.
    """
    env_value = os.environ.get("GH_INCLUDE_FORKS")
    if env_value is not None:
        return as_set(env_value) or set()
    forks_file = LIB_DIR / "include-forks.txt"
    forks = set()
    for line in forks_file.read_text().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            forks.add(stripped)
    return forks


def filter_repos(
    repos_json: list[dict],
    *,
    only: set[str] | None = None,
    skip: set[str] | None = None,
    include_forks: set[str] | None = None,
) -> list[Repo]:
    """Keeps every non-archived repo that's either not a fork or is listed in
    include_forks, then applies only/skip.
    """
    include_forks = include_forks or set()
    repos = []
    for entry in repos_json:
        if entry["isArchived"]:
            continue
        name = entry["name"]
        if entry["isFork"] and name not in include_forks:
            continue
        if only and name not in only:
            continue
        if skip and name in skip:
            continue
        default_branch_ref = entry.get("defaultBranchRef") or {}
        repos.append(
            Repo(
                name=name,
                default_branch=default_branch_ref.get("name", ""),
                is_private=entry["isPrivate"],
                is_fork=entry["isFork"],
            )
        )
    return repos


def list_repos(
    owner: str = DEFAULT_OWNER,
    *,
    only: set[str] | None = None,
    skip: set[str] | None = None,
    include_forks: set[str] | None = None,
) -> list[Repo]:
    if include_forks is None:
        include_forks = default_include_forks()
    return filter_repos(
        fetch_repos_json(owner), only=only, skip=skip, include_forks=include_forks
    )


def as_set(value: str | None) -> set[str] | None:
    if not value:
        return None
    return {v.strip() for v in value.split(",") if v.strip()}


def common_arg_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would change, without changing anything",
    )
    parser.add_argument("--only", help="comma-separated repo names to include")
    parser.add_argument("--skip", help="comma-separated repo names to exclude")
    return parser


def run_parallel(
    repos: list[Repo], worker, jobs: int = DEFAULT_JOBS
) -> list[RepoResult]:
    """Runs worker(repo) for each repo concurrently (a thread pool -- these
    are I/O-bound `gh`/network calls, not CPU-bound work), printing each
    result's line as soon as it's ready (completion order, not submission
    order) and returning every RepoResult.

    A worker exception is caught, reported to stderr, and doesn't stop the
    other repos from running -- but once every repo has been attempted, any
    failures are raised together as a single GhError so the run still ends
    with a nonzero exit.
    """
    results = []
    failed_names = []
    print_lock = threading.Lock()

    def call(repo: Repo) -> RepoResult:
        result = worker(repo)
        with print_lock:
            print(result.line)
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(call, repo): repo for repo in repos}
        for future in concurrent.futures.as_completed(futures):
            repo = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001 -- collected below, not swallowed
                failed_names.append(repo.name)
                print(f"{repo.name}: {exc}", file=sys.stderr)

    if failed_names:
        raise GhError(
            f"{len(failed_names)} repo(s) failed: {', '.join(sorted(failed_names))}"
        )

    return results
