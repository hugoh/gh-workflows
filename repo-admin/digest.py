"""Builds an HTML digest of activity on hugoh's repos -- still-open PRs
opened in the last N days, releases published in the last R days, and PRs
closed in the last M days -- in separate sections, and emails it via an
smtp2go SMTP relay.

Usage: digest.py [--open-days 14] [--release-days 7] [--closed-days 7]
    [--out FILE] [--no-send]

Reads SMTP settings and the recipient from the environment: SMTP_HOST,
SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, DIGEST_FROM_EMAIL,
DIGEST_TO_EMAIL. GH_OWNER overrides the default owner (hugoh), same as
repo_admin.py.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import smtplib
import sys
from datetime import UTC, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

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
from rich.progress import Progress

_PER_PAGE = "100"
_FAILING_CONCLUSIONS = {"failure", "timed_out", "cancelled", "action_required"}


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


def _normalize_release(repo_name: str, item: dict) -> dict:
    return {
        "repo": repo_name,
        "tag_name": item["tag_name"],
        "name": item["name"] or item["tag_name"],
        "url": item["html_url"],
        "published_at": _parse_dt(item["published_at"]),
        "prerelease": item["prerelease"],
    }


def _ci_status(owner: str, name: str, sha: str) -> str:
    """Rolls a commit's check runs up into one summary: "no checks" if none
    exist, "pending" if any haven't completed, "failing" if any completed
    one failed, else "passing".
    """
    response = api_request("GET", f"/repos/{owner}/{name}/commits/{sha}/check-runs")
    if not response.ok:
        raise GhError(error_message(response), status_code=response.status_code)
    runs = response.json()["check_runs"]
    if not runs:
        return "no checks"
    if any(run["status"] != "completed" for run in runs):
        return "pending"
    if any(run["conclusion"] in _FAILING_CONCLUSIONS for run in runs):
        return "failing"
    return "passing"


def _mergeable_status(owner: str, name: str, number: int) -> str:
    """ "dirty" is GitHub's mergeable_state for an actual merge conflict;
    everything else (including null/"unknown" right after a push, before
    GitHub finishes computing it) is treated as clean rather than flagged.
    """
    data = api_json("GET", f"/repos/{owner}/{name}/pulls/{number}")
    return "conflict" if data.get("mergeable_state") == "dirty" else "clean"


def _fetch_repo_prs(owner: str, name: str, since_updated: datetime) -> list[dict]:
    """Pages a repo's pull requests newest-updated-first, stopping as soon as
    a page's PRs were all last updated before `since_updated` -- no need to
    walk full history every run. Sorted by `updated` rather than `created` so
    a PR opened long before the fetch window but closed within it (closing
    bumps `updated_at`) is still picked up; render_html does the actual
    open/closed windowing.
    """
    prs = []
    page = 1
    while True:
        response = api_request(
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
        if not response.ok:
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
                pr["ci_status"] = _ci_status(owner, name, item["head"]["sha"])
                pr["mergeable"] = _mergeable_status(owner, name, pr["number"])
            prs.append(pr)

        if page_exhausted or len(items) < int(_PER_PAGE):
            break
        page += 1
    return prs


def fetch_prs(
    owner: str, repos: list[Repo], since_updated: datetime, jobs: int = DEFAULT_JOBS
) -> list[dict]:
    """Fetches every repo concurrently -- these are I/O-bound network calls
    (one repo's fetch doesn't depend on another's), and with dozens of repos
    doing them serially dominates the script's runtime. lib.py's requests
    Session is thread-local specifically to support this.
    """
    prs = []
    with (
        concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool,
        Progress(disable=not sys.stdout.isatty()) as progress,
    ):
        task = progress.add_task("Fetching PRs...", total=len(repos))
        futures = [
            pool.submit(_fetch_repo_prs, owner, repo.name, since_updated)
            for repo in repos
        ]
        for future in concurrent.futures.as_completed(futures):
            prs.extend(future.result())
            progress.advance(task)
    return prs


def _fetch_repo_releases(
    owner: str, name: str, since_published: datetime
) -> list[dict]:
    """Pages a repo's releases newest-first (GitHub's default order), stopping
    as soon as a page's releases were all published before `since_published`.
    Draft releases have no `published_at` and aren't a publish event, so
    they're skipped rather than breaking the cutoff comparison.
    """
    releases = []
    page = 1
    while True:
        response = api_request(
            "GET",
            f"/repos/{owner}/{name}/releases",
            params={"per_page": _PER_PAGE, "page": str(page)},
        )
        if not response.ok:
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


def fetch_releases(
    owner: str, repos: list[Repo], since_published: datetime, jobs: int = DEFAULT_JOBS
) -> list[dict]:
    """Fetches every repo's releases concurrently, mirroring fetch_prs."""
    releases = []
    with (
        concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool,
        Progress(disable=not sys.stdout.isatty()) as progress,
    ):
        task = progress.add_task("Fetching releases...", total=len(repos))
        futures = [
            pool.submit(_fetch_repo_releases, owner, repo.name, since_published)
            for repo in repos
        ]
        for future in concurrent.futures.as_completed(futures):
            releases.extend(future.result())
            progress.advance(task)
    return releases


# ---------------------------------------------------------------------------
# render_html
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
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
    return _digest_template.render(
        open_prs=open_prs,
        releases=recent_releases,
        closed_prs=closed_prs,
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
        default=14,
        help="how many days back to look for still-open PRs (default 14)",
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


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)

    until = datetime.now(UTC)
    since_open = until - timedelta(days=args.open_days)
    since_closed = until - timedelta(days=args.closed_days)
    since_release = until - timedelta(days=args.release_days)
    since_fetch = min(since_open, since_closed, since_release)

    repos = list_repos(DEFAULT_OWNER)
    prs = fetch_prs(DEFAULT_OWNER, repos, since_fetch)
    releases = fetch_releases(DEFAULT_OWNER, repos, since_fetch)
    rendered = render_html(
        prs, releases, since_open, since_closed, since_release, until
    )

    if args.out:
        with open(args.out, "w") as f:
            f.write(rendered)

    if not args.no_send:
        send_email(
            rendered,
            smtp_host=os.environ["SMTP_HOST"],
            smtp_port=int(os.environ["SMTP_PORT"]),
            smtp_user=os.environ["SMTP_USERNAME"],
            smtp_password=os.environ["SMTP_PASSWORD"],
            from_addr=os.environ["DIGEST_FROM_EMAIL"],
            to_addr=os.environ["DIGEST_TO_EMAIL"],
            subject=f"PR digest: {_format_date(until)}",
        )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except GhError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
