from datetime import UTC, datetime
from email import message_from_bytes
from typing import ClassVar

import httpx
import pytest
import respx
from digest import fetch_issues, fetch_prs, fetch_releases, render_html, send_email
from lib import API_BASE, Repo

# open PRs cover the last 14 days, closed PRs the last 7 -- both windows
# meet at UNTIL (2026-07-24).
SINCE_OPEN = datetime(2026, 7, 10, tzinfo=UTC)
SINCE_CLOSED = datetime(2026, 7, 17, tzinfo=UTC)
SINCE_RELEASE = datetime(2026, 7, 17, tzinfo=UTC)
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
        "head": {"sha": f"sha{number}"},
    }


def _mock_open_pr_extras(
    httpx2_mock: respx.Router,
    repo="repo-a",
    number=1,
    sha=None,
    check_runs=None,
    mergeable_state="clean",
):
    """Open PRs get two extra fetches (CI status, mergeable state) that
    closed PRs skip -- mock both for a given repo/PR/sha.
    """
    sha = sha or f"sha{number}"
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/{repo}/commits/{sha}/check-runs").mock(
        return_value=httpx.Response(
            200,
            json={
                "check_runs": check_runs if check_runs is not None else [_check_run()]
            },
        )
    )
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/{repo}/pulls/{number}").mock(
        return_value=httpx.Response(200, json={"mergeable_state": mergeable_state})
    )


def _check_run(status="completed", conclusion="success"):
    return {"status": status, "conclusion": conclusion}


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


async def test_fetch_prs_normalizes_fields(httpx2_mock: respx.Router):
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/repo-a/pulls").mock(
        return_value=httpx.Response(
            200, json=[_pr(number=5, title="Fix bug", login="hugoh")]
        )
    )
    _mock_open_pr_extras(httpx2_mock, number=5)
    prs = await fetch_prs("hugoh", [REPO_A], SINCE_OPEN)
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
            "ci_status": "passing",
            "mergeable": "clean",
        }
    ]


async def test_fetch_prs_excludes_prs_updated_before_since(httpx2_mock: respx.Router):
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/repo-a/pulls").mock(
        return_value=httpx.Response(
            200,
            json=[
                _pr(number=2, created_at="2026-07-20T10:00:00Z"),
                _pr(number=1, created_at="2026-07-05T10:00:00Z"),
            ],
        )
    )
    _mock_open_pr_extras(httpx2_mock, number=2)
    prs = await fetch_prs("hugoh", [REPO_A], SINCE_OPEN)
    assert [pr["number"] for pr in prs] == [2]


async def test_fetch_prs_includes_pr_opened_before_since_but_updated_after(
    httpx2_mock: respx.Router,
):
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/repo-a/pulls").mock(
        return_value=httpx.Response(
            200,
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
        )
    )
    prs = await fetch_prs("hugoh", [REPO_A], SINCE_OPEN)
    assert [pr["number"] for pr in prs] == [1]


async def test_fetch_prs_merged_true_when_merged_at_set(httpx2_mock: respx.Router):
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/repo-a/pulls").mock(
        return_value=httpx.Response(
            200,
            json=[
                _pr(
                    state="closed",
                    closed_at="2026-07-21T00:00:00Z",
                    merged_at="2026-07-21T00:00:00Z",
                )
            ],
        )
    )
    prs = await fetch_prs("hugoh", [REPO_A], SINCE_OPEN)
    assert prs[0]["merged"] is True


async def test_fetch_prs_merged_false_when_closed_without_merge(
    httpx2_mock: respx.Router,
):
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/repo-a/pulls").mock(
        return_value=httpx.Response(
            200,
            json=[
                _pr(state="closed", closed_at="2026-07-21T00:00:00Z", merged_at=None)
            ],
        )
    )
    prs = await fetch_prs("hugoh", [REPO_A], SINCE_OPEN)
    assert prs[0]["merged"] is False


async def test_fetch_prs_stops_paginating_once_page_is_entirely_older_than_since(
    httpx2_mock: respx.Router,
):
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/repo-a/pulls").mock(
        return_value=httpx.Response(
            200, json=[_pr(number=1, created_at="2026-07-01T00:00:00Z")]
        )
    )
    await fetch_prs("hugoh", [REPO_A], SINCE_OPEN)
    assert len(httpx2_mock.calls) == 1


async def test_fetch_prs_combines_multiple_repos(httpx2_mock: respx.Router):
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/repo-a/pulls").mock(
        return_value=httpx.Response(200, json=[_pr(number=1)])
    )
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/repo-b/pulls").mock(
        return_value=httpx.Response(200, json=[_pr(number=2)])
    )
    _mock_open_pr_extras(httpx2_mock, repo="repo-a", number=1)
    _mock_open_pr_extras(httpx2_mock, repo="repo-b", number=2)
    prs = await fetch_prs("hugoh", [REPO_A, REPO_B], SINCE_OPEN)
    assert sorted((pr["repo"], pr["number"]) for pr in prs) == [
        ("repo-a", 1),
        ("repo-b", 2),
    ]


# ---------------------------------------------------------------------------
# fetch_issues
# ---------------------------------------------------------------------------


def _issue(
    number=1,
    title="Something broke",
    created_at="2026-07-20T10:00:00Z",
    updated_at=None,
    closed_at=None,
    state="open",
    login="octocat",
    is_pr=False,
):
    item = {
        "number": number,
        "title": title,
        "html_url": f"https://github.com/hugoh/repo-a/issues/{number}",
        "user": {"login": login},
        "created_at": created_at,
        "updated_at": updated_at or closed_at or created_at,
        "closed_at": closed_at,
        "state": state,
    }
    if is_pr:
        item["pull_request"] = {"url": "https://api.github.com/..."}
    return item


async def test_fetch_issues_normalizes_fields(httpx2_mock: respx.Router):
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/repo-a/issues").mock(
        return_value=httpx.Response(
            200, json=[_issue(number=5, title="Broken build", login="hugoh")]
        )
    )
    issues = await fetch_issues("hugoh", [REPO_A], SINCE_OPEN)
    assert issues == [
        {
            "repo": "repo-a",
            "number": 5,
            "title": "Broken build",
            "url": "https://github.com/hugoh/repo-a/issues/5",
            "author": "hugoh",
            "created_at": datetime(2026, 7, 20, 10, 0, 0, tzinfo=UTC),
            "closed_at": None,
            "state": "open",
        }
    ]


async def test_fetch_issues_excludes_pull_requests(httpx2_mock: respx.Router):
    # GitHub's /issues endpoint also returns pull requests -- those are
    # covered by fetch_prs already and must not be double-counted here.
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/repo-a/issues").mock(
        return_value=httpx.Response(
            200,
            json=[
                _issue(number=1, is_pr=True),
                _issue(number=2, is_pr=False),
            ],
        )
    )
    issues = await fetch_issues("hugoh", [REPO_A], SINCE_OPEN)
    assert [issue["number"] for issue in issues] == [2]


async def test_fetch_issues_excludes_renovate_dependency_dashboard(
    httpx2_mock: respx.Router,
):
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/repo-a/issues").mock(
        return_value=httpx.Response(
            200,
            json=[
                _issue(number=1, title="Dependency Dashboard", login="renovate[bot]"),
                _issue(number=2, title="Real bug"),
            ],
        )
    )
    issues = await fetch_issues("hugoh", [REPO_A], SINCE_OPEN)
    assert [issue["number"] for issue in issues] == [2]


async def test_fetch_issues_keeps_dependency_dashboard_title_from_a_human(
    httpx2_mock: respx.Router,
):
    # only filter the renovate bot's own dashboard issue -- a human-authored
    # issue that happens to share its title is a real issue.
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/repo-a/issues").mock(
        return_value=httpx.Response(
            200, json=[_issue(number=1, title="Dependency Dashboard", login="octocat")]
        )
    )
    issues = await fetch_issues("hugoh", [REPO_A], SINCE_OPEN)
    assert [issue["number"] for issue in issues] == [1]


async def test_fetch_issues_excludes_issues_updated_before_since(
    httpx2_mock: respx.Router,
):
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/repo-a/issues").mock(
        return_value=httpx.Response(
            200,
            json=[
                _issue(number=2, created_at="2026-07-20T10:00:00Z"),
                _issue(number=1, created_at="2026-07-05T10:00:00Z"),
            ],
        )
    )
    issues = await fetch_issues("hugoh", [REPO_A], SINCE_OPEN)
    assert [issue["number"] for issue in issues] == [2]


async def test_fetch_issues_stops_paginating_once_page_is_entirely_older_than_since(
    httpx2_mock: respx.Router,
):
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/repo-a/issues").mock(
        return_value=httpx.Response(
            200, json=[_issue(number=1, created_at="2026-07-01T00:00:00Z")]
        )
    )
    await fetch_issues("hugoh", [REPO_A], SINCE_OPEN)
    assert len(httpx2_mock.calls) == 1


async def test_fetch_issues_combines_multiple_repos(httpx2_mock: respx.Router):
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/repo-a/issues").mock(
        return_value=httpx.Response(200, json=[_issue(number=1)])
    )
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/repo-b/issues").mock(
        return_value=httpx.Response(200, json=[_issue(number=2)])
    )
    issues = await fetch_issues("hugoh", [REPO_A, REPO_B], SINCE_OPEN)
    assert sorted((issue["repo"], issue["number"]) for issue in issues) == [
        ("repo-a", 1),
        ("repo-b", 2),
    ]


# ---------------------------------------------------------------------------
# fetch_releases
# ---------------------------------------------------------------------------


def _release(
    tag_name="v1.0.0",
    name="Version 1.0.0",
    published_at="2026-07-20T10:00:00Z",
    draft=False,
    prerelease=False,
):
    return {
        "tag_name": tag_name,
        "name": name,
        "html_url": f"https://github.com/hugoh/repo-a/releases/tag/{tag_name}",
        "published_at": published_at,
        "draft": draft,
        "prerelease": prerelease,
    }


async def test_fetch_releases_normalizes_fields(httpx2_mock: respx.Router):
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/repo-a/releases").mock(
        return_value=httpx.Response(
            200, json=[_release(tag_name="v2.0.0", name="Version 2.0.0")]
        )
    )
    releases = await fetch_releases("hugoh", [REPO_A], SINCE_RELEASE)
    assert releases == [
        {
            "repo": "repo-a",
            "tag_name": "v2.0.0",
            "name": "Version 2.0.0",
            "url": "https://github.com/hugoh/repo-a/releases/tag/v2.0.0",
            "published_at": datetime(2026, 7, 20, 10, 0, 0, tzinfo=UTC),
            "prerelease": False,
        }
    ]


async def test_fetch_releases_falls_back_to_tag_name_when_name_blank(
    httpx2_mock: respx.Router,
):
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/repo-a/releases").mock(
        return_value=httpx.Response(200, json=[_release(tag_name="v2.0.0", name="")])
    )
    releases = await fetch_releases("hugoh", [REPO_A], SINCE_RELEASE)
    assert releases[0]["name"] == "v2.0.0"


async def test_fetch_releases_excludes_drafts(httpx2_mock: respx.Router):
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/repo-a/releases").mock(
        return_value=httpx.Response(200, json=[_release(draft=True, published_at=None)])
    )
    releases = await fetch_releases("hugoh", [REPO_A], SINCE_RELEASE)
    assert releases == []


async def test_fetch_releases_marks_prerelease(httpx2_mock: respx.Router):
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/repo-a/releases").mock(
        return_value=httpx.Response(200, json=[_release(prerelease=True)])
    )
    releases = await fetch_releases("hugoh", [REPO_A], SINCE_RELEASE)
    assert releases[0]["prerelease"] is True


async def test_fetch_releases_excludes_releases_published_before_since(
    httpx2_mock: respx.Router,
):
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/repo-a/releases").mock(
        return_value=httpx.Response(
            200,
            json=[
                _release(tag_name="v2.0.0", published_at="2026-07-20T10:00:00Z"),
                _release(tag_name="v1.0.0", published_at="2026-07-01T10:00:00Z"),
            ],
        )
    )
    releases = await fetch_releases("hugoh", [REPO_A], SINCE_RELEASE)
    assert [r["tag_name"] for r in releases] == ["v2.0.0"]


async def test_fetch_releases_stops_paginating_once_page_is_entirely_older_than_since(
    httpx2_mock: respx.Router,
):
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/repo-a/releases").mock(
        return_value=httpx.Response(
            200, json=[_release(published_at="2026-07-01T10:00:00Z")]
        )
    )
    await fetch_releases("hugoh", [REPO_A], SINCE_RELEASE)
    assert len(httpx2_mock.calls) == 1


async def test_fetch_releases_combines_multiple_repos(httpx2_mock: respx.Router):
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/repo-a/releases").mock(
        return_value=httpx.Response(200, json=[_release(tag_name="v1.0.0")])
    )
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/repo-b/releases").mock(
        return_value=httpx.Response(200, json=[_release(tag_name="v2.0.0")])
    )
    releases = await fetch_releases("hugoh", [REPO_A, REPO_B], SINCE_RELEASE)
    assert sorted(r["tag_name"] for r in releases) == ["v1.0.0", "v2.0.0"]


# ---------------------------------------------------------------------------
# CI status / mergeable state (open PRs only -- closed PRs skip these two
# extra fetches, since a closed PR's CI/conflict state isn't actionable)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("check_runs", "expected_status"),
    [
        (
            [_check_run(status="in_progress", conclusion=None)],
            "pending",
        ),
        (
            [_check_run(), _check_run(conclusion="failure")],
            "failing",
        ),
        ([], "no checks"),
    ],
    ids=["pending", "failing", "no_checks"],
)
async def test_fetch_prs_ci_status(
    httpx2_mock: respx.Router, check_runs, expected_status
):
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/repo-a/pulls").mock(
        return_value=httpx.Response(200, json=[_pr(number=1)])
    )
    _mock_open_pr_extras(httpx2_mock, number=1, check_runs=check_runs)
    prs = await fetch_prs("hugoh", [REPO_A], SINCE_OPEN)
    assert prs[0]["ci_status"] == expected_status


async def test_fetch_prs_mergeable_conflict_when_dirty(httpx2_mock: respx.Router):
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/repo-a/pulls").mock(
        return_value=httpx.Response(200, json=[_pr(number=1)])
    )
    _mock_open_pr_extras(httpx2_mock, number=1, mergeable_state="dirty")
    prs = await fetch_prs("hugoh", [REPO_A], SINCE_OPEN)
    assert prs[0]["mergeable"] == "conflict"


async def test_fetch_prs_mergeable_clean_when_state_unknown(httpx2_mock: respx.Router):
    # mergeable_state can be null/"unknown" right after a push, before
    # GitHub finishes computing it -- treated the same as clean, not
    # flagged as a conflict.
    httpx2_mock.get(f"{API_BASE}/repos/hugoh/repo-a/pulls").mock(
        return_value=httpx.Response(200, json=[_pr(number=1)])
    )
    _mock_open_pr_extras(httpx2_mock, number=1, mergeable_state="unknown")
    prs = await fetch_prs("hugoh", [REPO_A], SINCE_OPEN)
    assert prs[0]["mergeable"] == "clean"


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
        "ci_status": "passing",
        "mergeable": "clean",
    }
    base.update(overrides)
    return base


def test_render_html_lists_open_pr_with_relevant_info():
    html = render_html(
        [_normalized_pr()], [], [], SINCE_OPEN, SINCE_CLOSED, SINCE_RELEASE, UNTIL
    )
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
    html = render_html(
        [open_pr, closed_pr], [], [], SINCE_OPEN, SINCE_CLOSED, SINCE_RELEASE, UNTIL
    )
    open_idx = html.index("Open one")
    closed_idx = html.index("Closed one")
    open_section_idx = html.index("Open (")
    closed_section_idx = html.index("Closed (", open_section_idx)
    assert open_section_idx < open_idx < closed_section_idx < closed_idx


def test_render_html_orders_sections_open_releases_closed():
    open_pr = _normalized_pr(state="open")
    closed_pr = _normalized_pr(
        state="closed", merged=True, closed_at=datetime(2026, 7, 21, tzinfo=UTC)
    )
    release = _normalized_release()
    html = render_html(
        [open_pr, closed_pr],
        [release],
        [],
        SINCE_OPEN,
        SINCE_CLOSED,
        SINCE_RELEASE,
        UNTIL,
    )
    open_idx = html.index("Open (")
    releases_idx = html.index("Releases (")
    closed_idx = html.index("Closed (")
    assert open_idx < releases_idx < closed_idx


def test_render_html_open_pr_outside_open_window_is_excluded():
    # created 20 days before UNTIL -- inside the (implied) 30-day closed
    # window used here isn't relevant since it's still open; it's outside
    # the 14-day open window (SINCE_OPEN is 2026-07-10).
    pr = _normalized_pr(created_at=datetime(2026, 7, 1, tzinfo=UTC), state="open")
    html = render_html([pr], [], [], SINCE_OPEN, SINCE_CLOSED, SINCE_RELEASE, UNTIL)
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
    html = render_html([pr], [], [], SINCE_OPEN, SINCE_CLOSED, SINCE_RELEASE, UNTIL)
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
    html = render_html([pr], [], [], SINCE_OPEN, SINCE_CLOSED, SINCE_RELEASE, UNTIL)
    assert "Add feature" in html


def test_render_html_shows_merged_status_for_merged_pr():
    pr = _normalized_pr(
        state="closed", merged=True, closed_at=datetime(2026, 7, 21, tzinfo=UTC)
    )
    html = render_html([pr], [], [], SINCE_OPEN, SINCE_CLOSED, SINCE_RELEASE, UNTIL)
    assert "merged" in html.lower()


def test_render_html_shows_closed_without_merge():
    pr = _normalized_pr(
        state="closed",
        merged=False,
        closed_at=datetime(2026, 7, 21, tzinfo=UTC),
    )
    html = render_html([pr], [], [], SINCE_OPEN, SINCE_CLOSED, SINCE_RELEASE, UNTIL)
    assert "closed" in html.lower()
    assert "merged" not in html.lower()


def test_render_html_empty_state_for_no_open_prs():
    closed_pr = _normalized_pr(
        state="closed", merged=True, closed_at=datetime(2026, 7, 21, tzinfo=UTC)
    )
    html = render_html(
        [closed_pr], [], [], SINCE_OPEN, SINCE_CLOSED, SINCE_RELEASE, UNTIL
    )
    assert "no open" in html.lower()


def test_render_html_empty_state_for_no_closed_prs():
    html = render_html(
        [_normalized_pr()], [], [], SINCE_OPEN, SINCE_CLOSED, SINCE_RELEASE, UNTIL
    )
    assert "no closed" in html.lower()


def test_render_html_section_headers_show_each_windows_own_date_range():
    html = render_html([], [], [], SINCE_OPEN, SINCE_CLOSED, SINCE_RELEASE, UNTIL)
    assert "2026-07-10" in html  # since_open
    assert "2026-07-17" in html  # since_closed
    assert "2026-07-24" in html  # until, shared


def test_render_html_shows_cutoff_summary_with_day_counts_and_dates():
    # top-of-email summary so the windows are legible without reading every
    # section header's own "(since to until)" range.
    html = render_html([], [], [], SINCE_OPEN, SINCE_CLOSED, SINCE_RELEASE, UNTIL)
    assert "14 days" in html  # until - since_open
    assert "7 days" in html  # until - since_closed / since_release
    assert "2026-07-10" in html
    assert "2026-07-17" in html


def test_render_html_shows_ci_status_and_mergeable_for_open_prs():
    pr = _normalized_pr(ci_status="failing", mergeable="conflict")
    html = render_html([pr], [], [], SINCE_OPEN, SINCE_CLOSED, SINCE_RELEASE, UNTIL)
    assert "failing" in html
    assert "conflict" in html


def test_render_html_color_codes_passing_ci_status():
    pr = _normalized_pr(ci_status="passing")
    html = render_html([pr], [], [], SINCE_OPEN, SINCE_CLOSED, SINCE_RELEASE, UNTIL)
    assert "status-passing" in html


def test_render_html_color_codes_failing_ci_status():
    pr = _normalized_pr(ci_status="failing")
    html = render_html([pr], [], [], SINCE_OPEN, SINCE_CLOSED, SINCE_RELEASE, UNTIL)
    assert "status-failing" in html


def test_render_html_color_codes_pending_ci_status():
    pr = _normalized_pr(ci_status="pending")
    html = render_html([pr], [], [], SINCE_OPEN, SINCE_CLOSED, SINCE_RELEASE, UNTIL)
    assert "status-pending" in html


def test_render_html_color_codes_no_checks_ci_status():
    pr = _normalized_pr(ci_status="no checks")
    html = render_html([pr], [], [], SINCE_OPEN, SINCE_CLOSED, SINCE_RELEASE, UNTIL)
    assert "status-no-checks" in html


def test_render_html_color_codes_clean_mergeable():
    pr = _normalized_pr(mergeable="clean")
    html = render_html([pr], [], [], SINCE_OPEN, SINCE_CLOSED, SINCE_RELEASE, UNTIL)
    assert "mergeable-clean" in html


def test_render_html_color_codes_conflict_mergeable():
    pr = _normalized_pr(mergeable="conflict")
    html = render_html([pr], [], [], SINCE_OPEN, SINCE_CLOSED, SINCE_RELEASE, UNTIL)
    assert "mergeable-conflict" in html


def test_render_html_closed_section_has_no_ci_or_mergeable_columns():
    pr = _normalized_pr(
        state="closed", merged=True, closed_at=datetime(2026, 7, 21, tzinfo=UTC)
    )
    html = render_html([pr], [], [], SINCE_OPEN, SINCE_CLOSED, SINCE_RELEASE, UNTIL)
    closed_section = html[html.index("Closed (") :]
    assert "status-" not in closed_section
    assert "mergeable-" not in closed_section


def test_render_html_escapes_title():
    pr = _normalized_pr(title="<script>alert(1)</script>")
    html = render_html([pr], [], [], SINCE_OPEN, SINCE_CLOSED, SINCE_RELEASE, UNTIL)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_render_html_shows_counts_in_section_headers():
    open_pr = _normalized_pr(number=1, state="open")
    closed_pr = _normalized_pr(
        number=2,
        state="closed",
        merged=True,
        closed_at=datetime(2026, 7, 21, tzinfo=UTC),
    )
    releases = [_normalized_release(), _normalized_release(tag_name="v2.0.0")]
    html = render_html(
        [open_pr, closed_pr],
        releases,
        [],
        SINCE_OPEN,
        SINCE_CLOSED,
        SINCE_RELEASE,
        UNTIL,
    )
    assert "Open (1)" in html
    assert "Releases (2)" in html
    assert "Closed (1)" in html


# ---------------------------------------------------------------------------
# releases (render_html)
# ---------------------------------------------------------------------------


def _normalized_release(**overrides):
    base = {
        "repo": "repo-a",
        "tag_name": "v1.0.0",
        "name": "Version 1.0.0",
        "url": "https://github.com/hugoh/repo-a/releases/tag/v1.0.0",
        "published_at": datetime(2026, 7, 20, tzinfo=UTC),
        "prerelease": False,
    }
    base.update(overrides)
    return base


def test_render_html_lists_release_with_relevant_info():
    html = render_html(
        [], [_normalized_release()], [], SINCE_OPEN, SINCE_CLOSED, SINCE_RELEASE, UNTIL
    )
    assert "repo-a v1.0.0" in html
    assert "2026-07-20" in html
    assert "https://github.com/hugoh/repo-a/releases/tag/v1.0.0" in html


def test_render_html_release_outside_window_is_excluded():
    release = _normalized_release(published_at=datetime(2026, 7, 1, tzinfo=UTC))
    html = render_html(
        [], [release], [], SINCE_OPEN, SINCE_CLOSED, SINCE_RELEASE, UNTIL
    )
    assert "no releases" in html.lower()
    assert "repo-a v1.0.0" not in html


def test_render_html_empty_state_for_no_releases():
    html = render_html([], [], [], SINCE_OPEN, SINCE_CLOSED, SINCE_RELEASE, UNTIL)
    assert "no releases" in html.lower()


def test_render_html_marks_prerelease():
    release = _normalized_release(prerelease=True)
    html = render_html(
        [], [release], [], SINCE_OPEN, SINCE_CLOSED, SINCE_RELEASE, UNTIL
    )
    assert "prerelease" in html.lower()


def test_render_html_releases_sorted_newest_first():
    older = _normalized_release(
        tag_name="v1.0.0",
        url="https://github.com/hugoh/repo-a/releases/tag/v1.0.0",
        published_at=datetime(2026, 7, 18, tzinfo=UTC),
    )
    newer = _normalized_release(
        tag_name="v2.0.0",
        url="https://github.com/hugoh/repo-a/releases/tag/v2.0.0",
        published_at=datetime(2026, 7, 22, tzinfo=UTC),
    )
    html = render_html(
        [], [older, newer], [], SINCE_OPEN, SINCE_CLOSED, SINCE_RELEASE, UNTIL
    )
    assert html.index("repo-a v2.0.0") < html.index("repo-a v1.0.0")


def test_render_html_release_section_header_shows_its_own_window():
    html = render_html([], [], [], SINCE_OPEN, SINCE_CLOSED, SINCE_RELEASE, UNTIL)
    assert "2026-07-17" in html  # since_release


def test_render_html_escapes_release_tag_name():
    release = _normalized_release(tag_name="<script>alert(1)</script>")
    html = render_html(
        [], [release], [], SINCE_OPEN, SINCE_CLOSED, SINCE_RELEASE, UNTIL
    )
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# issues (render_html)
# ---------------------------------------------------------------------------


def _normalized_issue(**overrides):
    base = {
        "repo": "repo-a",
        "number": 1,
        "title": "Something broke",
        "url": "https://github.com/hugoh/repo-a/issues/1",
        "author": "octocat",
        "created_at": datetime(2026, 7, 20, tzinfo=UTC),
        "closed_at": None,
        "state": "open",
    }
    base.update(overrides)
    return base


def test_render_html_lists_open_issue_with_relevant_info():
    html = render_html(
        [], [], [_normalized_issue()], SINCE_OPEN, SINCE_CLOSED, SINCE_RELEASE, UNTIL
    )
    assert "Something broke" in html
    assert "repo-a" in html
    assert "#1" in html
    assert "octocat" in html
    assert "https://github.com/hugoh/repo-a/issues/1" in html


def test_render_html_splits_open_and_closed_issue_sections():
    open_issue = _normalized_issue(number=1, title="Open one", state="open")
    closed_issue = _normalized_issue(
        number=2,
        title="Closed one",
        state="closed",
        closed_at=datetime(2026, 7, 21, tzinfo=UTC),
    )
    html = render_html(
        [],
        [],
        [open_issue, closed_issue],
        SINCE_OPEN,
        SINCE_CLOSED,
        SINCE_RELEASE,
        UNTIL,
    )
    open_idx = html.index("Open one")
    closed_idx = html.index("Closed one")
    open_section_idx = html.index("Open issues")
    closed_section_idx = html.index("Closed issues", open_section_idx)
    assert open_section_idx < open_idx < closed_section_idx < closed_idx


def test_render_html_open_issue_outside_open_window_is_excluded():
    issue = _normalized_issue(created_at=datetime(2026, 7, 1, tzinfo=UTC), state="open")
    html = render_html([], [], [issue], SINCE_OPEN, SINCE_CLOSED, SINCE_RELEASE, UNTIL)
    assert "no open issues" in html.lower()


def test_render_html_closed_issue_outside_closed_window_is_excluded():
    issue = _normalized_issue(
        state="closed",
        created_at=datetime(2026, 7, 12, tzinfo=UTC),
        closed_at=datetime(2026, 7, 13, tzinfo=UTC),
    )
    html = render_html([], [], [issue], SINCE_OPEN, SINCE_CLOSED, SINCE_RELEASE, UNTIL)
    assert "no closed issues" in html.lower()
    assert "Something broke" not in html


def test_render_html_empty_state_for_no_open_issues():
    closed_issue = _normalized_issue(
        state="closed", closed_at=datetime(2026, 7, 21, tzinfo=UTC)
    )
    html = render_html(
        [], [], [closed_issue], SINCE_OPEN, SINCE_CLOSED, SINCE_RELEASE, UNTIL
    )
    assert "no open issues" in html.lower()


def test_render_html_empty_state_for_no_closed_issues():
    html = render_html(
        [], [], [_normalized_issue()], SINCE_OPEN, SINCE_CLOSED, SINCE_RELEASE, UNTIL
    )
    assert "no closed issues" in html.lower()


def test_render_html_shows_counts_in_issue_section_headers():
    open_issue = _normalized_issue(number=1, state="open")
    closed_issue = _normalized_issue(
        number=2, state="closed", closed_at=datetime(2026, 7, 21, tzinfo=UTC)
    )
    html = render_html(
        [],
        [],
        [open_issue, closed_issue],
        SINCE_OPEN,
        SINCE_CLOSED,
        SINCE_RELEASE,
        UNTIL,
    )
    assert "Open issues (1)" in html
    assert "Closed issues (1)" in html


def test_render_html_escapes_issue_title():
    issue = _normalized_issue(title="<script>alert(1)</script>")
    html = render_html([], [], [issue], SINCE_OPEN, SINCE_CLOSED, SINCE_RELEASE, UNTIL)
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
