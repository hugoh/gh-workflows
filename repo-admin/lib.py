"""Shared helpers for repo-admin/*.py scripts. Not meant to be run directly."""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

LIB_DIR = Path(__file__).resolve().parent
API_BASE = "https://api.github.com"
DEFAULT_OWNER = os.environ.get("GH_OWNER", "hugoh")
DEFAULT_JOBS = int(os.environ.get("GH_JOBS", "6"))


class GhError(RuntimeError):
    """A GitHub API call -- or a repo's worker function -- failed unexpectedly.

    status_code is set for HTTP errors raised by api_json(), so callers can
    branch on the real status code (e.g. 403 vs 404) instead of
    string-matching an error message.
    """

    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _auth_token() -> str:
    """Reads the token `gh` already has -- keychain storage, SSO, and 2FA are
    already solved by `gh auth login`, so this reuses that instead of
    managing a separate credential.
    """
    result = subprocess.run(
        ["gh", "auth", "token"], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise GhError(f"gh auth token failed: {result.stderr.strip()}")
    return result.stdout.strip()


_local = threading.local()


def _session() -> requests.Session:
    # Thread-local rather than one shared Session: requests.Session doesn't
    # document thread-safety guarantees, and run_parallel calls into this
    # from a thread pool. Each worker thread pays the `gh auth token`
    # subprocess cost once, then reuses its own session for every repo it
    # handles.
    session = getattr(_local, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(
            {
                "Authorization": f"Bearer {_auth_token()}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )
        _local.session = session
    return session


def api_request(
    method: str, path: str, *, json: Any = None, params: dict | None = None
) -> requests.Response:
    """Makes one GitHub REST API call and returns the raw Response --
    callers decide what a given status means for their endpoint (e.g. a 404
    means "feature disabled" for vulnerability-alerts but "not found"
    everywhere else). Raises GhError only for genuine transport failures
    (DNS, timeout, connection reset); HTTP error statuses are returned, not
    raised.
    """
    url = path if path.startswith("http") else f"{API_BASE}{path}"
    try:
        return _session().request(method, url, json=json, params=params, timeout=30)
    except requests.RequestException as exc:
        raise GhError(str(exc)) from exc


def error_message(response: requests.Response) -> str:
    """Extracts GitHub's own `message` field from an error response body,
    falling back to the raw response text if the body isn't JSON.
    """
    try:
        return response.json().get("message", response.text)
    except ValueError:
        return response.text


def api_json(
    method: str, path: str, *, json: Any = None, params: dict | None = None
) -> dict:
    """Like api_request, but raises GhError (with status_code and GitHub's
    own error message) on any non-2xx response, and returns the parsed JSON
    body -- or {} for a body-less response like 204 No Content -- on success.
    """
    response = api_request(method, path, json=json, params=params)
    if not response.ok:
        raise GhError(error_message(response), status_code=response.status_code)
    return response.json() if response.content else {}


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


def _paginated(method: str, path: str, *, params: dict | None = None) -> list[dict]:
    items = []
    url = path
    query = params
    while url:
        response = api_request(method, url, params=query)
        if not response.ok:
            raise GhError(error_message(response), status_code=response.status_code)
        items.extend(response.json())
        url = response.links.get("next", {}).get("url")
        query = None  # the "next" link already carries the full query string
    return items


def fetch_repos_json(owner: str) -> list[dict]:
    """Lists every repo for `owner`. When `owner` is the authenticated `gh`
    user, uses /user/repos so private repos are included; otherwise falls
    back to /users/{owner}/repos, which only ever returns public repos.
    """
    viewer = api_json("GET", "/user").get("login")
    if owner == viewer:
        return _paginated(
            "GET", "/user/repos", params={"affiliation": "owner", "per_page": "100"}
        )
    return _paginated("GET", f"/users/{owner}/repos", params={"per_page": "100"})


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


def unmatched_include_forks(
    include_forks: set[str], repos_json: list[dict]
) -> set[str]:
    """include-forks.txt entries (or GH_INCLUDE_FORKS) that don't match any
    fetched repo -- a typo, a rename, or a repo that's gone, silently going
    stale otherwise since filter_repos() just never matches them.
    """
    repo_names = {entry["name"] for entry in repos_json}
    return include_forks - repo_names


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
        if entry["archived"]:
            continue
        name = entry["name"]
        if entry["fork"] and name not in include_forks:
            continue
        if only and name not in only:
            continue
        if skip and name in skip:
            continue
        repos.append(
            Repo(
                name=name,
                default_branch=entry.get("default_branch") or "",
                is_private=entry["private"],
                is_fork=entry["fork"],
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
    repos_json = fetch_repos_json(owner)
    for name in sorted(unmatched_include_forks(include_forks, repos_json)):
        print(
            f"warning: include-forks entry {name!r} doesn't match any repo",
            file=sys.stderr,
        )
    return filter_repos(repos_json, only=only, skip=skip, include_forks=include_forks)


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
