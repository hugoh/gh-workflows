"""Shared helpers for repo-admin/*.py scripts. Not meant to be run directly."""

from __future__ import annotations

import asyncio
import enum
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from rich.console import Console
from rich.progress import Progress

LIB_DIR = Path(__file__).resolve().parent
API_BASE = "https://api.github.com"
DEFAULT_OWNER = os.environ.get("GH_OWNER", "hugoh")
DEFAULT_JOBS = int(os.environ.get("GH_JOBS", "6"))

console = Console()


class Status(enum.Enum):
    """How a repo's result line relates to the desired end state -- distinct
    from RepoResult.tag, which is per-command bookkeeping used for
    end-of-run summaries.
    """

    OK = "ok"  # full target reached, changed this run
    UNCHANGED = "unchanged"  # full target reached, already was
    LIMITED = "limited"  # best-effort (capped by plan/data) reached, changed this run
    LIMITED_UNCHANGED = "limited_unchanged"  # best-effort reached, already was
    FAILED = "failed"  # worker raised


_STATUS_DISPLAY: dict[Status, tuple[str, str]] = {
    Status.OK: ("✓", "green"),
    Status.UNCHANGED: ("•", "green"),
    Status.LIMITED: ("○", "yellow"),
    Status.LIMITED_UNCHANGED: ("•", "yellow"),
    Status.FAILED: ("✗", "red"),
}


def classify_status(at_target: bool, changed: bool) -> Status:
    if at_target:
        return Status.OK if changed else Status.UNCHANGED
    return Status.LIMITED if changed else Status.LIMITED_UNCHANGED


def result_line(name: str, detail: str, status: Status) -> str:
    prefix = (
        "unchanged: " if status in (Status.UNCHANGED, Status.LIMITED_UNCHANGED) else ""
    )
    return f"{name:<30} {prefix}{detail}"


def print_status(status: Status, line: str) -> None:
    # Always goes through the single Console that Progress/Live owns
    # (run_parallel passes it to Progress(console=...)): a second Console
    # writing to a separate stream isn't coordinated by rich's Live
    # redraw bookkeeping and can visually corrupt output on a real
    # terminal (see run_parallel's failure-path comment).
    symbol, color = _STATUS_DISPLAY[status]
    # markup=False: `line` can contain repo/error text with literal "[" (dict
    # reprs, error messages) that would otherwise be parsed as rich markup.
    # highlight=False: rich's default ReprHighlighter recolors numbers,
    # paths, etc. within the line (e.g. a repo named "foo-410"), fighting
    # with the single status color we want for the whole line.
    console.print(f"{symbol} {line}", style=color, markup=False, highlight=False)


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


_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()


async def _get_client() -> httpx.AsyncClient:
    # One shared AsyncClient rather than one per thread: there's no longer a
    # thread pool, and gh auth token only needs to be paid once per process.
    global _client
    if _client is None:
        async with _client_lock:
            if _client is None:
                _client = httpx.AsyncClient(
                    headers={
                        "Authorization": f"Bearer {_auth_token()}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                    timeout=30,
                )
    return _client


async def aclose_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def api_request(
    method: str, path: str, *, json: Any = None, params: dict | None = None
) -> httpx.Response:
    """Makes one GitHub REST API call and returns the raw Response --
    callers decide what a given status means for their endpoint (e.g. a 404
    means "feature disabled" for vulnerability-alerts but "not found"
    everywhere else). Raises GhError only for genuine transport failures
    (DNS, timeout, connection reset); HTTP error statuses are returned, not
    raised.
    """
    url = path if path.startswith("http") else f"{API_BASE}{path}"
    client = await _get_client()
    try:
        return await client.request(method, url, json=json, params=params)
    except httpx.HTTPError as exc:
        raise GhError(str(exc)) from exc


def error_message(response: httpx.Response) -> str:
    """Extracts GitHub's own `message` field from an error response body,
    falling back to the raw response text if the body isn't JSON.
    """
    try:
        return response.json().get("message", response.text)
    except ValueError:
        return response.text


async def api_json(
    method: str, path: str, *, json: Any = None, params: dict | None = None
) -> dict:
    """Like api_request, but raises GhError (with status_code and GitHub's
    own error message) on any non-2xx response, and returns the parsed JSON
    body -- or {} for a body-less response like 204 No Content -- on success.
    """
    response = await api_request(method, path, json=json, params=params)
    if not response.is_success:
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
    status: Status = Status.OK
    tag: str | None = None


async def _paginated(
    method: str, path: str, *, params: dict | None = None
) -> list[dict]:
    items = []
    url = path
    query = params
    while url:
        response = await api_request(method, url, params=query)
        if not response.is_success:
            raise GhError(error_message(response), status_code=response.status_code)
        items.extend(response.json())
        url = response.links.get("next", {}).get("url")
        query = None  # the "next" link already carries the full query string
    return items


async def fetch_repos_json(owner: str) -> list[dict]:
    """Lists every repo for `owner`. When `owner` is the authenticated `gh`
    user, uses /user/repos so private repos are included; otherwise falls
    back to /users/{owner}/repos, which only ever returns public repos.
    """
    viewer = (await api_json("GET", "/user")).get("login")
    if owner == viewer:
        return await _paginated(
            "GET", "/user/repos", params={"affiliation": "owner", "per_page": "100"}
        )
    return await _paginated("GET", f"/users/{owner}/repos", params={"per_page": "100"})


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


async def list_repos(
    owner: str = DEFAULT_OWNER,
    *,
    only: set[str] | None = None,
    skip: set[str] | None = None,
    include_forks: set[str] | None = None,
) -> list[Repo]:
    if include_forks is None:
        include_forks = default_include_forks()
    repos_json = await fetch_repos_json(owner)
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


async def run_parallel(
    repos: list[Repo], worker, jobs: int = DEFAULT_JOBS
) -> list[RepoResult]:
    """Runs worker(repo) for each repo concurrently (bounded by a semaphore
    -- these are I/O-bound `gh`/network calls, not CPU-bound work), printing
    each result's line as soon as it's ready (completion order, not
    submission order) above a live progress bar, and returning every
    RepoResult.

    A worker exception is caught inside call() itself -- not left to
    propagate through the TaskGroup -- so one repo failing doesn't cancel
    the others (TaskGroup cancels every sibling task the moment any task
    raises). Instead it's printed as a failure line and collected; once
    every repo has been attempted, any failures are raised together as a
    single GhError so the run still ends with a nonzero exit.
    """
    results: list[RepoResult] = []
    failed_names: list[str] = []
    sem = asyncio.Semaphore(jobs)

    with Progress(console=console) as progress:
        task = progress.add_task("Processing repos...", total=len(repos))

        async def call(repo: Repo) -> None:
            async with sem:
                try:
                    result = await worker(repo)
                except Exception as exc:  # noqa: BLE001 -- collected below, not swallowed
                    failed_names.append(repo.name)
                    print_status(Status.FAILED, f"{repo.name}: {exc}")
                else:
                    print_status(result.status, result.line)
                    results.append(result)
                finally:
                    progress.advance(task)

        async with asyncio.TaskGroup() as tg:
            for repo in repos:
                tg.create_task(call(repo))

    if failed_names:
        raise GhError(
            f"{len(failed_names)} repo(s) failed: {', '.join(sorted(failed_names))}"
        )

    return results
