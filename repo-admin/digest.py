"""Builds an HTML digest of pull requests against hugoh's repos -- still-open
PRs opened in the last N days, and PRs closed in the last M days -- in
separate sections, and emails it via an smtp2go SMTP relay.

Usage: digest.py [--open-days 14] [--closed-days 7] [--out FILE] [--no-send]

Reads SMTP settings and the recipient from the environment: SMTP_HOST,
SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, DIGEST_FROM_EMAIL,
DIGEST_TO_EMAIL. GH_OWNER overrides the default owner (hugoh), same as
repo_admin.py.
"""

from __future__ import annotations

import argparse
import html
import os
import smtplib
import sys
from datetime import UTC, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from lib import DEFAULT_OWNER, GhError, Repo, api_request, error_message, list_repos

_PER_PAGE = "100"


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
            prs.append(_normalize_pr(name, item))

        if page_exhausted or len(items) < int(_PER_PAGE):
            break
        page += 1
    return prs


def fetch_prs(owner: str, repos: list[Repo], since_updated: datetime) -> list[dict]:
    prs = []
    for repo in repos:
        prs.extend(_fetch_repo_prs(owner, repo.name, since_updated))
    return prs


# ---------------------------------------------------------------------------
# render_html
# ---------------------------------------------------------------------------

_STYLE = """
body { font-family: -apple-system, Helvetica, Arial, sans-serif; color: #1a1a1a; }
h1 { font-size: 1.2em; }
h2 { font-size: 1.05em; margin-top: 1.5em; }
table { border-collapse: collapse; width: 100%; }
td, th { text-align: left; padding: 4px 8px; border-bottom: 1px solid #ddd; font-size: 0.9em; }
.empty { color: #666; font-style: italic; }
"""


def _format_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _pr_row(pr: dict, *, closed: bool) -> str:
    title = html.escape(pr["title"])
    author = html.escape(pr["author"])
    repo = html.escape(pr["repo"])
    cells = [
        f'<td><a href="{html.escape(pr["url"])}">{title}</a></td>',
        f"<td>{repo}</td>",
        f"<td>#{pr['number']}</td>",
        f"<td>{author}</td>",
        f"<td>{_format_date(pr['created_at'])}</td>",
    ]
    if closed:
        status = "merged" if pr["merged"] else "closed"
        cells.append(f"<td>{status} {_format_date(pr['closed_at'])}</td>")
    return "<tr>" + "".join(cells) + "</tr>"


def _section(
    title: str, since: datetime, until: datetime, prs: list[dict], *, closed: bool
) -> str:
    heading = f"<h2>{title} ({_format_date(since)} to {_format_date(until)})</h2>"
    if not prs:
        return f"{heading}<p class='empty'>No {title.lower()} PRs in this period.</p>"
    header = "<th>Title</th><th>Repo</th><th>#</th><th>Author</th><th>Opened</th>"
    if closed:
        header += "<th>Status</th>"
    rows = "".join(_pr_row(pr, closed=closed) for pr in prs)
    return f"{heading}<table><tr>{header}</tr>{rows}</table>"


def render_html(
    prs: list[dict], since_open: datetime, since_closed: datetime, until: datetime
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
    return (
        "<html><head><meta charset='utf-8'>"
        f"<style>{_STYLE}</style></head><body>"
        "<h1>PR digest</h1>"
        f"{_section('Open', since_open, until, open_prs, closed=False)}"
        f"{_section('Closed', since_closed, until, closed_prs, closed=True)}"
        "</body></html>"
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
    since_fetch = min(since_open, since_closed)

    repos = list_repos(DEFAULT_OWNER)
    prs = fetch_prs(DEFAULT_OWNER, repos, since_fetch)
    rendered = render_html(prs, since_open, since_closed, until)

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
