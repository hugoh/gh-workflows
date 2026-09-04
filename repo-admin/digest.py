"""Builds an HTML digest of activity on hugoh's repos -- still-open PRs and
issues opened in the last N days, releases published in the last R days, and
PRs and issues closed in the last M days -- in separate sections, and emails
it via an smtp2go SMTP relay. Renovate's "Dependency Dashboard" issues are
filtered out as noise.

Usage: digest.py [--open-days 365] [--release-days 7] [--closed-days 7]
    [--out FILE] [--no-send]

Reads SMTP settings and the recipient from the environment: SMTP_HOST,
SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, DIGEST_FROM_EMAIL,
DIGEST_TO_EMAIL. GH_OWNER overrides the default owner (hugoh), same as
repo_admin.py.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import smtplib
import sys
from datetime import UTC, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import lib
from jinja2 import Environment, FileSystemLoader, select_autoescape
from lib import (
    DEFAULT_JOBS,
    DEFAULT_OWNER,
    GhError,
    Repo,
    api_json,
    api_request,
    error_message,
    list_repos,
)
from rich.progress import Progress, TaskID

_PER_PAGE = "100"
_FAILING_CONCLUSIONS = {"failure", "timed_out", "cancelled", "action_required"}
_RENOVATE_LOGINS = {"renovate", "renovate[bot]"}
_RENOVATE_DASHBOARD_TITLE = "Dependency Dashboard"


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _parse_optional_dt(value: str | None) -> datetime | None:
    return _parse_dt(value) if value is not None else None


def _normalize_pr(repo_name: str, item: dict) -> dict:
    return {
        "repo": repo_name,
        "number": item["number"],
        "title": item["title"],
        "url": item["html_url"],
        "author": item["user"]["login"],
        "created_at": _parse_dt(item["created_at"]),
        "closed_at": _parse_optional_dt(item["closed_at"]),
        "merged": item["merged_at"] is not None,
        "state": item["state"],
    }


def _is_renovate_dashboard(item: dict) -> bool:
    """Renovate opens one "Dependency Dashboard" issue per repo and keeps it
    open indefinitely, editing it in place -- it's not an actionable item,
    just noise that would otherwise dominate the open-issues section.
    """
    return (
        item["title"] == _RENOVATE_DASHBOARD_TITLE
        and item["user"]["login"] in _RENOVATE_LOGINS
    )


def _normalize_issue(repo_name: str, item: dict) -> dict:
    return {
        "repo": repo_name,
        "number": item["number"],
        "title": item["title"],
        "url": item["html_url"],
        "author": item["user"]["login"],
        "created_at": _parse_dt(item["created_at"]),
        "closed_at": _parse_optional_dt(item["closed_at"]),
        "state": item["state"],
    }


def _normalize_release(repo_name: str, item: dict) -> dict:
    return {
        "repo": repo_name,
        "tag_name": item["tag_name"],
        "name": item["name"] or item["tag_name"],
        "url": item["html_url"],
        "published_at": _parse_dt(item["published_at"]),
        "prerelease": item["prerelease"],
    }


async def _ci_status(owner: str, name: str, sha: str) -> str:
    """Rolls a commit's check runs up into one summary: "no checks" if none
    exist, "pending" if any haven't completed, "failing" if any completed
    one failed, else "passing".
    """
    response = await api_request(
        "GET", f"/repos/{owner}/{name}/commits/{sha}/check-runs"
    )
    if not response.is_success:
        raise GhError(error_message(response), status_code=response.status_code)
    runs = response.json()["check_runs"]
    if not runs:
        return "no checks"
    if any(run["status"] != "completed" for run in runs):
        return "pending"
    if any(run["conclusion"] in _FAILING_CONCLUSIONS for run in runs):
        return "failing"
    return "passing"


async def _mergeable_status(owner: str, name: str, number: int) -> str:
    """ "dirty" is GitHub's mergeable_state for an actual merge conflict;
    everything else (including null/"unknown" right after a push, before
    GitHub finishes computing it) is treated as clean rather than flagged.
    """
    data = await api_json("GET", f"/repos/{owner}/{name}/pulls/{number}")
    return "conflict" if data.get("mergeable_state") == "dirty" else "clean"


class _GrowingTotal:
    """A counter for a Progress task's `total` that grows by one each time a
    unit of work is discovered -- avoids `total=None`'s indeterminate/pulsing
    bar, which never renders a finished 100% state. Incrementing `total`
    right before the matching `progress.advance()` call keeps total and
    completed in lockstep, so the bar naturally reaches 100% once the last
    unit finishes. No locking needed: asyncio is cooperatively single-
    threaded and grow() contains no `await`, so it's already atomic between
    task switches.
    """

    def __init__(self, progress: Progress, task: TaskID) -> None:
        self._progress = progress
        self._task = task
        self._total = 0

    def grow(self) -> None:
        self._total += 1
        self._progress.update(self._task, total=self._total)


async def _fetch_repo_prs(
    owner: str,
    name: str,
    since_updated: datetime,
    progress: Progress,
    task: TaskID,
    check_total: _GrowingTotal,
    sem: asyncio.Semaphore,
) -> list[dict]:
    """Pages a repo's pull requests newest-updated-first, stopping as soon as
    a page's PRs were all last updated before `since_updated` -- no need to
    walk full history every run. Sorted by `updated` rather than `created` so
    a PR opened long before the fetch window but closed within it (closing
    bumps `updated_at`) is still picked up; render_html does the actual
    open/closed windowing.

    Paging itself stays sequential (page N+1's URL/early-stop depends on
    page N's content), but every open PR's CI/mergeable checks -- the slow
    per-PR API calls -- are fired concurrently once paging finishes, bounded
    by the same semaphore `fetch_prs` shares across every repo (so a run
    with dozens of repos and many open PRs each doesn't fire hundreds of
    requests at once).
    """
    prs = []
    open_prs = []
    page = 1
    while True:
        async with sem:
            response = await api_request(
                "GET",
                f"/repos/{owner}/{name}/pulls",
                params={
                    "state": "all",
                    "sort": "updated",
                    "direction": "desc",
                    "per_page": _PER_PAGE,
                    "page": str(page),
                },
            )
        if not response.is_success:
            raise GhError(error_message(response), status_code=response.status_code)
        items = response.json()
        if not items:
            break

        page_exhausted = False
        for item in items:
            if _parse_dt(item["updated_at"]) < since_updated:
                page_exhausted = True
                break
            pr = _normalize_pr(name, item)
            if pr["state"] == "open":
                check_total.grow()
                pr["_head_sha"] = item["head"]["sha"]
                open_prs.append(pr)
            prs.append(pr)

        if page_exhausted or len(items) < int(_PER_PAGE):
            break
        page += 1

    async def check_one(pr: dict) -> None:
        async with sem:
            pr["ci_status"] = await _ci_status(owner, name, pr.pop("_head_sha"))
            pr["mergeable"] = await _mergeable_status(owner, name, pr["number"])
            progress.advance(task)

    if open_prs:
        await asyncio.gather(*(check_one(pr) for pr in open_prs))

    return prs


async def fetch_prs(
    owner: str,
    repos: list[Repo],
    since_updated: datetime,
    jobs: int = DEFAULT_JOBS,
    sem: asyncio.Semaphore | None = None,
) -> list[dict]:
    """Fetches every repo concurrently -- these are I/O-bound network calls
    (one repo's fetch doesn't depend on another's), and with dozens of repos
    doing them serially dominates the script's runtime.

    Two bars: repos fetched (as before), plus a growing counter of open PRs
    CI/mergeable-checked -- those per-PR calls are the slow part, so a repo
    with many open PRs no longer looks stalled until it finishes.

    `sem` is optional so this stays independently callable/testable; main()
    passes one shared with fetch_releases so the two together stay capped at
    `jobs` concurrent requests, not `2 * jobs`.
    """
    sem = sem or asyncio.Semaphore(jobs)
    prs = []
    with Progress(disable=not sys.stdout.isatty()) as progress:
        repo_task = progress.add_task("Fetching PRs...", total=len(repos))
        check_task = progress.add_task("Checking open PR status...", total=0)
        check_total = _GrowingTotal(progress, check_task)

        async def run_one(repo: Repo) -> list[dict]:
            result = await _fetch_repo_prs(
                owner, repo.name, since_updated, progress, check_task, check_total, sem
            )
            progress.advance(repo_task)
            return result

        results = await asyncio.gather(*(run_one(repo) for repo in repos))
    for result in results:
        prs.extend(result)
    return prs


async def _fetch_repo_issues(
    owner: str,
    name: str,
    since_updated: datetime,
    sem: asyncio.Semaphore,
) -> list[dict]:
    """Pages a repo's issues newest-updated-first, mirroring _fetch_repo_prs.
    GitHub's /issues endpoint returns pull requests too (distinguishable by
    a "pull_request" key) -- those are skipped since fetch_prs already
    covers them -- as are Renovate's "Dependency Dashboard" issues.
    """
    issues = []
    page = 1
    while True:
        async with sem:
            response = await api_request(
                "GET",
                f"/repos/{owner}/{name}/issues",
                params={
                    "state": "all",
                    "sort": "updated",
                    "direction": "desc",
                    "per_page": _PER_PAGE,
                    "page": str(page),
                },
            )
        if not response.is_success:
            raise GhError(error_message(response), status_code=response.status_code)
        items = response.json()
        if not items:
            break

        page_exhausted = False
        for item in items:
            if _parse_dt(item["updated_at"]) < since_updated:
                page_exhausted = True
                break
            if "pull_request" in item:
                continue
            if _is_renovate_dashboard(item):
                continue
            issues.append(_normalize_issue(name, item))

        if page_exhausted or len(items) < int(_PER_PAGE):
            break
        page += 1
    return issues


async def fetch_issues(
    owner: str,
    repos: list[Repo],
    since_updated: datetime,
    jobs: int = DEFAULT_JOBS,
    sem: asyncio.Semaphore | None = None,
) -> list[dict]:
    """Fetches every repo's issues concurrently, mirroring fetch_prs (minus
    the CI/mergeable checks, which don't apply to issues).
    """
    sem = sem or asyncio.Semaphore(jobs)
    issues = []
    with Progress(disable=not sys.stdout.isatty()) as progress:
        task = progress.add_task("Fetching issues...", total=len(repos))

        async def run_one(repo: Repo) -> list[dict]:
            result = await _fetch_repo_issues(owner, repo.name, since_updated, sem)
            progress.advance(task)
            return result

        results = await asyncio.gather(*(run_one(repo) for repo in repos))
    for result in results:
        issues.extend(result)
    return issues


async def _fetch_repo_releases(
    owner: str, name: str, since_published: datetime, sem: asyncio.Semaphore
) -> list[dict]:
    """Pages a repo's releases newest-first (GitHub's default order), stopping
    as soon as a page's releases were all published before `since_published`.
    Draft releases have no `published_at` and aren't a publish event, so
    they're skipped rather than breaking the cutoff comparison.
    """
    releases = []
    page = 1
    while True:
        async with sem:
            response = await api_request(
                "GET",
                f"/repos/{owner}/{name}/releases",
                params={"per_page": _PER_PAGE, "page": str(page)},
            )
        if not response.is_success:
            raise GhError(error_message(response), status_code=response.status_code)
        items = response.json()
        if not items:
            break

        page_exhausted = False
        for item in items:
            if item["draft"]:
                continue
            if _parse_dt(item["published_at"]) < since_published:
                page_exhausted = True
                break
            releases.append(_normalize_release(name, item))

        if page_exhausted or len(items) < int(_PER_PAGE):
            break
        page += 1
    return releases


async def fetch_releases(
    owner: str,
    repos: list[Repo],
    since_published: datetime,
    jobs: int = DEFAULT_JOBS,
    sem: asyncio.Semaphore | None = None,
) -> list[dict]:
    """Fetches every repo's releases concurrently, mirroring fetch_prs."""
    sem = sem or asyncio.Semaphore(jobs)
    releases = []
    with Progress(disable=not sys.stdout.isatty()) as progress:
        task = progress.add_task("Fetching releases...", total=len(repos))

        async def run_one(repo: Repo) -> list[dict]:
            result = await _fetch_repo_releases(owner, repo.name, since_published, sem)
            progress.advance(task)
            return result

        results = await asyncio.gather(*(run_one(repo) for repo in repos))
    for result in results:
        releases.extend(result)
    return releases


# ---------------------------------------------------------------------------
# render_html
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = Path(__file__).resolve().parent / "config" / "templates"
_jinja_env = Environment(
    loader=FileSystemLoader(_TEMPLATES_DIR),
    autoescape=select_autoescape(["html", "jinja"]),
    trim_blocks=True,
    lstrip_blocks=True,
)
_digest_template = _jinja_env.get_template("digest.html.jinja")


def _format_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def render_html(
    prs: list[dict],
    releases: list[dict],
    issues: list[dict],
    since_open: datetime,
    since_closed: datetime,
    since_release: datetime,
    until: datetime,
) -> str:
    open_prs = sorted(
        (pr for pr in prs if pr["state"] == "open" and pr["created_at"] >= since_open),
        key=lambda pr: pr["created_at"],
        reverse=True,
    )
    closed_prs = sorted(
        (
            pr
            for pr in prs
            if pr["state"] == "closed"
            and pr["closed_at"] is not None
            and pr["closed_at"] >= since_closed
        ),
        key=lambda pr: pr["closed_at"],
        reverse=True,
    )
    recent_releases = sorted(
        (r for r in releases if r["published_at"] >= since_release),
        key=lambda r: r["published_at"],
        reverse=True,
    )
    open_issues = sorted(
        (
            issue
            for issue in issues
            if issue["state"] == "open" and issue["created_at"] >= since_open
        ),
        key=lambda issue: issue["created_at"],
        reverse=True,
    )
    closed_issues = sorted(
        (
            issue
            for issue in issues
            if issue["state"] == "closed"
            and issue["closed_at"] is not None
            and issue["closed_at"] >= since_closed
        ),
        key=lambda issue: issue["closed_at"],
        reverse=True,
    )
    return _digest_template.render(
        open_prs=open_prs,
        releases=recent_releases,
        closed_prs=closed_prs,
        open_issues=open_issues,
        closed_issues=closed_issues,
        since_open=since_open,
        since_closed=since_closed,
        since_release=since_release,
        until=until,
    )


# ---------------------------------------------------------------------------
# send_email
# ---------------------------------------------------------------------------


def send_email(
    html_body: str,
    *,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    from_addr: str,
    to_addr: str,
    subject: str,
) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.attach(MIMEText("This email requires an HTML-capable mail client.", "plain"))
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.send_message(msg)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--open-days",
        type=int,
        default=365,
        help="how many days back to look for still-open PRs and issues (default 365)",
    )
    parser.add_argument(
        "--closed-days",
        type=int,
        default=7,
        help="how many days back to look for closed PRs (default 7)",
    )
    parser.add_argument(
        "--release-days",
        type=int,
        default=7,
        help="how many days back to look for published releases (default 7)",
    )
    parser.add_argument("--out", help="write the rendered HTML to this file")
    parser.add_argument(
        "--no-send", action="store_true", help="skip sending the email (for dry runs)"
    )
    return parser


async def _main_async(args: argparse.Namespace) -> int:
    until = datetime.now(UTC)
    since_open = until - timedelta(days=args.open_days)
    since_closed = until - timedelta(days=args.closed_days)
    since_release = until - timedelta(days=args.release_days)
    since_fetch = min(since_open, since_closed, since_release)

    repos = await list_repos(DEFAULT_OWNER)
    sem = asyncio.Semaphore(DEFAULT_JOBS)
    prs, releases, issues = await asyncio.gather(
        fetch_prs(DEFAULT_OWNER, repos, since_fetch, sem=sem),
        fetch_releases(DEFAULT_OWNER, repos, since_fetch, sem=sem),
        fetch_issues(DEFAULT_OWNER, repos, since_fetch, sem=sem),
    )
    rendered = render_html(
        prs, releases, issues, since_open, since_closed, since_release, until
    )

    if args.out:
        await asyncio.to_thread(Path(args.out).write_text, rendered)

    if not args.no_send:
        send_email(
            rendered,
            smtp_host=os.environ["SMTP_HOST"],
            smtp_port=int(os.environ["SMTP_PORT"]),
            smtp_user=os.environ["SMTP_USERNAME"],
            smtp_password=os.environ["SMTP_PASSWORD"],
            from_addr=os.environ["DIGEST_FROM_EMAIL"],
            to_addr=os.environ["DIGEST_TO_EMAIL"],
            subject=f"GitHub digest: {_format_date(until)}",
        )
    return 0


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)

    async def _run() -> int:
        try:
            return await _main_async(args)
        finally:
            await lib.aclose_client()

    return asyncio.run(_run())


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except GhError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
