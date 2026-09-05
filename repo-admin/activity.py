"""Reports recent commit activity per repo, ranked by an exponential
recency-decay score -- answers "what am I currently working on" better than
a flat commit count, since a repo touched yesterday should outrank one with
the same total spread evenly over a year.

Usage: activity.py [--window-months 12] [--half-life-days 30] [--limit 20]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta

import lib
from lib import (
    DEFAULT_JOBS,
    GhError,
    Repo,
    api_json,
    api_raw,
    default_owner,
    error_message,
    list_repos,
    public_repos,
)
from rich.table import Table

_PER_PAGE = "100"
_MONTH_DAYS = 30


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


async def _fetch_repo_commit_dates(
    owner: str, name: str, author: str, since: datetime, sem: asyncio.Semaphore
) -> list[datetime]:
    """Pages a repo's commits by the given author since `since` -- GitHub
    filters server-side by both `author` and `since`, so paging just walks
    until a short/empty page, no manual cutoff needed.
    """
    dates = []
    page = 1
    while True:
        async with sem:
            response = await api_raw(
                "GET",
                f"/repos/{owner}/{name}/commits",
                params={
                    "author": author,
                    "since": since.isoformat(),
                    "per_page": _PER_PAGE,
                    "page": str(page),
                },
            )
        if not response.is_success:
            raise GhError(error_message(response), status_code=response.status_code)
        items = response.json()
        if not items:
            break
        dates.extend(_parse_dt(item["commit"]["author"]["date"]) for item in items)
        if len(items) < int(_PER_PAGE):
            break
        page += 1
    return dates


async def fetch_commit_dates(
    owner: str,
    repos: list[Repo],
    author: str,
    since: datetime,
    jobs: int = DEFAULT_JOBS,
    sem: asyncio.Semaphore | None = None,
) -> dict[str, list[datetime]]:
    """Fetches every repo's commit dates concurrently, bounded by `sem`."""
    sem = sem or asyncio.Semaphore(jobs)
    with lib.progress_bar() as progress:
        task = progress.add_task("Fetching commits...", total=len(repos))

        async def run_one(repo: Repo) -> tuple[str, list[datetime]]:
            result = await _fetch_repo_commit_dates(
                owner, repo.name, author, since, sem
            )
            progress.advance(task)
            return repo.name, result

        results = await asyncio.gather(*(run_one(repo) for repo in repos))
    return dict(results)


def decay_score(dates: list[datetime], now: datetime, half_life_days: float) -> float:
    """Σ 0.5 ** (age_days / half_life) -- a commit today contributes 1.0, one
    half-life ago 0.5, two half-lives ago 0.25, etc. Rewards recent activity
    without the artificial cliff a fixed bucket-weight scheme would create at
    a bucket boundary.
    """
    total = 0.0
    for dt in dates:
        age_days = (now - dt).total_seconds() / 86400
        total += 0.5 ** (age_days / half_life_days)
    return total


def bucket_counts(dates: list[datetime], now: datetime) -> tuple[int, int, int]:
    """Plain commit counts within the last 1/6/12 months, for display
    alongside the decay score.
    """
    one_month = now.timestamp() - 1 * _MONTH_DAYS * 86400
    six_months = now.timestamp() - 6 * _MONTH_DAYS * 86400
    twelve_months = now.timestamp() - 12 * _MONTH_DAYS * 86400
    commits_1mo = sum(1 for dt in dates if dt.timestamp() >= one_month)
    commits_6mo = sum(1 for dt in dates if dt.timestamp() >= six_months)
    commits_12mo = sum(1 for dt in dates if dt.timestamp() >= twelve_months)
    return commits_1mo, commits_6mo, commits_12mo


def build_rows(
    repos: list[Repo],
    commit_dates: dict[str, list[datetime]],
    now: datetime,
    half_life_days: float,
) -> list[dict]:
    rows = []
    for repo in repos:
        dates = commit_dates.get(repo.name, [])
        if not dates:
            continue
        commits_1mo, commits_6mo, commits_12mo = bucket_counts(dates, now)
        rows.append(
            {
                "repo": repo.name,
                "score": decay_score(dates, now, half_life_days),
                "commits_1mo": commits_1mo,
                "commits_6mo": commits_6mo,
                "commits_12mo": commits_12mo,
                "private": repo.is_private,
            }
        )
    rows.sort(key=lambda row: row["score"], reverse=True)
    return rows


def render_table(title: str, rows: list[dict], limit: int) -> Table:
    table = Table(title=title)
    table.add_column("REPO")
    table.add_column("SCORE", justify="right")
    table.add_column("1MO", justify="right")
    table.add_column("6MO", justify="right")
    table.add_column("12MO", justify="right")
    table.add_column("PRIVATE")
    for row in rows[:limit] if limit else rows:
        table.add_row(
            row["repo"],
            f"{row['score']:.2f}",
            str(row["commits_1mo"]),
            str(row["commits_6mo"]),
            str(row["commits_12mo"]),
            str(row["private"]).lower(),
        )
    return table


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--window-months",
        type=int,
        default=12,
        help="how far back to fetch commits, in months (default 12)",
    )
    parser.add_argument(
        "--half-life-days",
        type=float,
        default=30,
        help="decay half-life for the ranking score, in days (default 30)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="max repos to show per table, 0 for unlimited (default 20)",
    )
    return parser


async def run(args: argparse.Namespace) -> int:
    """Entry point shared by this script's own CLI and repo_admin.py's
    `activity` subcommand -- both just need an args namespace with
    window_months/half_life_days/limit.
    """
    now = datetime.now(UTC)
    since = now - timedelta(days=args.window_months * _MONTH_DAYS)

    author = (await api_json("GET", "/user"))["login"]
    owner = await default_owner()
    all_repos, public_repos_json = await asyncio.gather(
        list_repos(owner),
        public_repos(owner),
    )
    public_names = {entry["name"] for entry in public_repos_json}
    public_repo_objs = [r for r in all_repos if r.name in public_names]

    sem = asyncio.Semaphore(DEFAULT_JOBS)
    commit_dates = await fetch_commit_dates(owner, all_repos, author, since, sem=sem)

    all_rows = build_rows(all_repos, commit_dates, now, args.half_life_days)
    public_rows = build_rows(public_repo_objs, commit_dates, now, args.half_life_days)

    lib.console.print(
        render_table("All repos (private + public)", all_rows, args.limit)
    )
    lib.console.print(render_table("Public repos only", public_rows, args.limit))
    lib.console.print(
        f"window={args.window_months}mo, half-life={args.half_life_days}d, "
        f"as of {now.strftime('%Y-%m-%d')}",
        style="dim",
    )
    return 0


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    return lib.run_cli(run, args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
