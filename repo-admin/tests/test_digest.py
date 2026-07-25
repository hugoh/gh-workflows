from datetime import UTC, datetime
from email import message_from_bytes
from typing import ClassVar

import pytest
import responses
from digest import fetch_prs, render_html, send_email
from lib import API_BASE, Repo

# open PRs cover the last 14 days, closed PRs the last 7 -- both windows
# meet at UNTIL (2026-07-24).
SINCE_OPEN = datetime(2026, 7, 10, tzinfo=UTC)
SINCE_CLOSED = datetime(2026, 7, 17, tzinfo=UTC)
UNTIL = datetime(2026, 7, 24, tzinfo=UTC)

REPO_A = Repo(name="repo-a", default_branch="main", is_private=False, is_fork=False)
REPO_B = Repo(name="repo-b", default_branch="main", is_private=False, is_fork=False)


def _pr(
    number=1,
    title="Add feature",
    created_at="2026-07-20T10:00:00Z",
    updated_at=None,
    closed_at=None,
    merged_at=None,
    state="open",
    login="octocat",
):
    return {
        "number": number,
        "title": title,
        "html_url": f"https://github.com/hugoh/repo-a/pull/{number}",
        "user": {"login": login},
        "created_at": created_at,
        "updated_at": updated_at or closed_at or created_at,
        "closed_at": closed_at,
        "merged_at": merged_at,
        "state": state,
    }


@pytest.fixture(autouse=True)
def fake_auth_token(monkeypatch):
    monkeypatch.setattr("lib._auth_token", lambda: "fake-token")


# ---------------------------------------------------------------------------
# fetch_prs
#
# fetch_prs itself only knows one cutoff, the oldest `updated_at` worth
# fetching (the caller passes the older of the open/closed windows) --
# sorted/paged by `updated` rather than `created` so a PR opened long ago
# but closed recently is still picked up. Splitting into open/closed
# windows happens in render_html.
# ---------------------------------------------------------------------------


@responses.activate
def test_fetch_prs_normalizes_fields():
    responses.add(
        responses.GET,
        f"{API_BASE}/repos/hugoh/repo-a/pulls",
        json=[_pr(number=5, title="Fix bug", login="hugoh")],
        status=200,
    )
    prs = fetch_prs("hugoh", [REPO_A], SINCE_OPEN)
    assert prs == [
        {
            "repo": "repo-a",
            "number": 5,
            "title": "Fix bug",
            "url": "https://github.com/hugoh/repo-a/pull/5",
            "author": "hugoh",
            "created_at": datetime(2026, 7, 20, 10, 0, 0, tzinfo=UTC),
            "closed_at": None,
            "merged": False,
            "state": "open",
        }
    ]


@responses.activate
def test_fetch_prs_excludes_prs_updated_before_since():
    responses.add(
        responses.GET,
        f"{API_BASE}/repos/hugoh/repo-a/pulls",
        json=[
            _pr(number=2, created_at="2026-07-20T10:00:00Z"),
            _pr(number=1, created_at="2026-07-05T10:00:00Z"),
        ],
        status=200,
    )
    prs = fetch_prs("hugoh", [REPO_A], SINCE_OPEN)
    assert [pr["number"] for pr in prs] == [2]


@responses.activate
def test_fetch_prs_includes_pr_opened_before_since_but_updated_after():
    responses.add(
        responses.GET,
        f"{API_BASE}/repos/hugoh/repo-a/pulls",
        json=[
            _pr(
                number=1,
                created_at="2026-06-01T00:00:00Z",
                updated_at="2026-07-20T00:00:00Z",
                closed_at="2026-07-20T00:00:00Z",
                merged_at="2026-07-20T00:00:00Z",
                state="closed",
            )
        ],
        status=200,
    )
    prs = fetch_prs("hugoh", [REPO_A], SINCE_OPEN)
    assert [pr["number"] for pr in prs] == [1]


@responses.activate
def test_fetch_prs_merged_true_when_merged_at_set():
    responses.add(
        responses.GET,
        f"{API_BASE}/repos/hugoh/repo-a/pulls",
        json=[
            _pr(
                state="closed",
                closed_at="2026-07-21T00:00:00Z",
                merged_at="2026-07-21T00:00:00Z",
            )
        ],
        status=200,
    )
    prs = fetch_prs("hugoh", [REPO_A], SINCE_OPEN)
    assert prs[0]["merged"] is True


@responses.activate
def test_fetch_prs_merged_false_when_closed_without_merge():
    responses.add(
        responses.GET,
        f"{API_BASE}/repos/hugoh/repo-a/pulls",
        json=[_pr(state="closed", closed_at="2026-07-21T00:00:00Z", merged_at=None)],
        status=200,
    )
    prs = fetch_prs("hugoh", [REPO_A], SINCE_OPEN)
    assert prs[0]["merged"] is False


@responses.activate
def test_fetch_prs_stops_paginating_once_page_is_entirely_older_than_since():
    responses.add(
        responses.GET,
        f"{API_BASE}/repos/hugoh/repo-a/pulls",
        json=[_pr(number=1, created_at="2026-07-01T00:00:00Z")],
        status=200,
    )
    fetch_prs("hugoh", [REPO_A], SINCE_OPEN)
    assert len(responses.calls) == 1


@responses.activate
def test_fetch_prs_combines_multiple_repos():
    responses.add(
        responses.GET,
        f"{API_BASE}/repos/hugoh/repo-a/pulls",
        json=[_pr(number=1)],
        status=200,
    )
    responses.add(
        responses.GET,
        f"{API_BASE}/repos/hugoh/repo-b/pulls",
        json=[_pr(number=2)],
        status=200,
    )
    prs = fetch_prs("hugoh", [REPO_A, REPO_B], SINCE_OPEN)
    assert sorted((pr["repo"], pr["number"]) for pr in prs) == [
        ("repo-a", 1),
        ("repo-b", 2),
    ]


# ---------------------------------------------------------------------------
# render_html
# ---------------------------------------------------------------------------


def _normalized_pr(**overrides):
    base = {
        "repo": "repo-a",
        "number": 1,
        "title": "Add feature",
        "url": "https://github.com/hugoh/repo-a/pull/1",
        "author": "octocat",
        "created_at": datetime(2026, 7, 20, tzinfo=UTC),
        "closed_at": None,
        "merged": False,
        "state": "open",
    }
    base.update(overrides)
    return base


def test_render_html_lists_open_pr_with_relevant_info():
    html = render_html([_normalized_pr()], SINCE_OPEN, SINCE_CLOSED, UNTIL)
    assert "Add feature" in html
    assert "repo-a" in html
    assert "#1" in html
    assert "octocat" in html
    assert "2026-07-20" in html
    assert "https://github.com/hugoh/repo-a/pull/1" in html


def test_render_html_splits_open_and_closed_sections():
    open_pr = _normalized_pr(number=1, title="Open one", state="open")
    closed_pr = _normalized_pr(
        number=2,
        title="Closed one",
        state="closed",
        merged=True,
        closed_at=datetime(2026, 7, 21, tzinfo=UTC),
    )
    html = render_html([open_pr, closed_pr], SINCE_OPEN, SINCE_CLOSED, UNTIL)
    open_idx = html.index("Open one")
    closed_idx = html.index("Closed one")
    open_section_idx = html.index("Open")
    closed_section_idx = html.index("Closed", open_section_idx)
    assert open_section_idx < open_idx < closed_section_idx < closed_idx


def test_render_html_open_pr_outside_open_window_is_excluded():
    # created 20 days before UNTIL -- inside the (implied) 30-day closed
    # window used here isn't relevant since it's still open; it's outside
    # the 14-day open window (SINCE_OPEN is 2026-07-10).
    pr = _normalized_pr(created_at=datetime(2026, 7, 1, tzinfo=UTC), state="open")
    html = render_html([pr], SINCE_OPEN, SINCE_CLOSED, UNTIL)
    assert "no open" in html.lower()


def test_render_html_closed_pr_outside_closed_window_is_excluded():
    # opened well within the open window, but closed before the (shorter)
    # closed window started -- must not show up in either section.
    pr = _normalized_pr(
        state="closed",
        merged=True,
        created_at=datetime(2026, 7, 12, tzinfo=UTC),
        closed_at=datetime(2026, 7, 13, tzinfo=UTC),
    )
    html = render_html([pr], SINCE_OPEN, SINCE_CLOSED, UNTIL)
    assert "no closed" in html.lower()
    assert "Add feature" not in html


def test_render_html_closed_pr_opened_before_open_window_still_shown():
    # opened well before the open window even started, but closed within
    # the closed window -- should still appear in Closed.
    pr = _normalized_pr(
        state="closed",
        merged=True,
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
        closed_at=datetime(2026, 7, 20, tzinfo=UTC),
    )
    html = render_html([pr], SINCE_OPEN, SINCE_CLOSED, UNTIL)
    assert "Add feature" in html


def test_render_html_shows_merged_status_for_merged_pr():
    pr = _normalized_pr(
        state="closed", merged=True, closed_at=datetime(2026, 7, 21, tzinfo=UTC)
    )
    html = render_html([pr], SINCE_OPEN, SINCE_CLOSED, UNTIL)
    assert "merged" in html.lower()


def test_render_html_shows_closed_without_merge():
    pr = _normalized_pr(
        state="closed",
        merged=False,
        closed_at=datetime(2026, 7, 21, tzinfo=UTC),
    )
    html = render_html([pr], SINCE_OPEN, SINCE_CLOSED, UNTIL)
    assert "closed" in html.lower()
    assert "merged" not in html.lower()


def test_render_html_empty_state_for_no_open_prs():
    closed_pr = _normalized_pr(
        state="closed", merged=True, closed_at=datetime(2026, 7, 21, tzinfo=UTC)
    )
    html = render_html([closed_pr], SINCE_OPEN, SINCE_CLOSED, UNTIL)
    assert "no open" in html.lower()


def test_render_html_empty_state_for_no_closed_prs():
    html = render_html([_normalized_pr()], SINCE_OPEN, SINCE_CLOSED, UNTIL)
    assert "no closed" in html.lower()


def test_render_html_section_headers_show_each_windows_own_date_range():
    html = render_html([], SINCE_OPEN, SINCE_CLOSED, UNTIL)
    assert "2026-07-10" in html  # since_open
    assert "2026-07-17" in html  # since_closed
    assert "2026-07-24" in html  # until, shared


def test_render_html_escapes_title():
    pr = _normalized_pr(title="<script>alert(1)</script>")
    html = render_html([pr], SINCE_OPEN, SINCE_CLOSED, UNTIL)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# send_email
# ---------------------------------------------------------------------------


class FakeSMTP:
    instances: ClassVar[list] = []

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.calls = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def starttls(self):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, msg):
        self.calls.append(("send_message", msg))


@pytest.fixture(autouse=True)
def fake_smtp(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    return FakeSMTP


def test_send_email_starttls_login_and_sends(fake_smtp):
    send_email(
        "<html>hi</html>",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="user",
        smtp_password="pass",
        from_addr="from@example.com",
        to_addr="to@example.com",
        subject="PR digest",
    )
    (instance,) = fake_smtp.instances
    assert instance.host == "smtp.example.com"
    assert instance.port == 587
    assert instance.calls[0] == "starttls"
    assert instance.calls[1] == ("login", "user", "pass")
    assert instance.calls[2][0] == "send_message"


def test_send_email_message_has_plain_and_html_parts(fake_smtp):
    send_email(
        "<html><body>hi</body></html>",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="user",
        smtp_password="pass",
        from_addr="from@example.com",
        to_addr="to@example.com",
        subject="PR digest",
    )
    (instance,) = fake_smtp.instances
    msg = instance.calls[2][1]
    parsed = message_from_bytes(msg.as_bytes())
    content_types = {part.get_content_type() for part in parsed.walk()}
    assert "text/plain" in content_types
    assert "text/html" in content_types


def test_send_email_sets_subject_and_addresses(fake_smtp):
    send_email(
        "<html>hi</html>",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="user",
        smtp_password="pass",
        from_addr="from@example.com",
        to_addr="to@example.com",
        subject="PR digest",
    )
    (instance,) = fake_smtp.instances
    msg = instance.calls[2][1]
    assert msg["Subject"] == "PR digest"
    assert msg["From"] == "from@example.com"
    assert msg["To"] == "to@example.com"
